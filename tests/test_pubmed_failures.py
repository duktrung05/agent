"""Regression: lỗi PubMed không được diễn giải thành không có nghiên cứu."""

from contextlib import redirect_stderr, redirect_stdout
import importlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lessons"))
pubmed = importlib.import_module("02_search_pubmed")
agent = importlib.import_module("03_tool_calling")


class FakeResponse(io.BytesIO):
    def __init__(self, body, content_type="application/json"):
        super().__init__(body)
        self.status = 200
        self.headers = {"Content-Type": content_type}


def valid_response(count="1", pmids=None):
    return FakeResponse(json.dumps({"esearchresult": {
        "count": count, "idlist": ["123"] if pmids is None else pmids,
    }}).encode())


TOOL_CALL = {"function": {
    "name": "search_pubmed", "arguments": {"query": "sleep AND Alzheimer", "limit": 3},
}}
ASSISTANT = {"role": "assistant", "content": "", "tool_calls": [TOOL_CALL]}


class PubMedFailureTests(unittest.TestCase):
    def test_invalid_json_then_success_retries_once(self):
        responses = [FakeResponse(b"<html>temporary response</html>", "text/html"), valid_response()]
        with patch.object(pubmed, "urlopen", side_effect=responses) as network, \
             patch.object(pubmed.time, "sleep") as sleep, redirect_stderr(io.StringIO()):
            result = pubmed.search_pubmed("sleep", 3)
        self.assertEqual(result["pmids"], ["123"])
        self.assertEqual(network.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_repeated_invalid_json_reports_diagnostics_and_stops(self):
        responses = [FakeResponse(b"<html>challenge</html>", "text/html") for _ in range(2)]
        with patch.object(pubmed, "urlopen", side_effect=responses) as network, \
             patch.object(pubmed.time, "sleep") as sleep, redirect_stderr(io.StringIO()), \
             self.assertRaisesRegex(RuntimeError, "HTTP 200, Content-Type: text/html.*challenge"):
            pubmed.search_pubmed("sleep", 3)
        self.assertEqual(network.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_ncbi_block_page_is_reported_without_retry(self):
        response = FakeResponse(b"<html><title>NCBI - WWW Error Blocked Diagnostic</title></html>", "text/html")
        with patch.object(pubmed, "urlopen", return_value=response) as network, \
             patch.object(pubmed.time, "sleep") as sleep, self.assertRaisesRegex(RuntimeError, "NCBI trả trang chặn HTML"):
            pubmed.search_pubmed("sleep", 3)
        self.assertEqual(network.call_count, 1)
        sleep.assert_not_called()

    def test_http429_retries_but_http400_does_not(self):
        error = HTTPError("https://example.test", 429, "limit", {}, io.BytesIO(b"rate limit"))
        with patch.object(pubmed, "urlopen", side_effect=[error, valid_response()]) as network, \
             patch.object(pubmed.time, "sleep"), redirect_stderr(io.StringIO()):
            self.assertEqual(pubmed.search_pubmed("sleep")["returned"], 1)
        self.assertEqual(network.call_count, 2)
        error = HTTPError("https://example.test", 400, "bad request", {}, io.BytesIO(b"invalid"))
        with patch.object(pubmed, "urlopen", side_effect=error) as network, \
             patch.object(pubmed.time, "sleep") as sleep, self.assertRaisesRegex(RuntimeError, "HTTP 400"):
            pubmed.search_pubmed("sleep")
        self.assertEqual(network.call_count, 1)
        sleep.assert_not_called()

    def test_successful_zero_results_remains_successful_data(self):
        with patch.object(pubmed, "urlopen", return_value=valid_response("0", [])):
            result = pubmed.search_pubmed("unmatched term")
        self.assertEqual(result["total_matches"], 0)
        self.assertEqual(result["pmids"], [])

    def test_failed_tool_never_reaches_final_model(self):
        output = io.StringIO()
        # Nếu code gọi model lần hai, mock sẽ trả đúng câu sai trong ảnh.
        wrong_final = {"role": "assistant", "content": "Không có bài báo nào được tìm thấy."}
        with patch.object(agent, "ask_model", side_effect=[ASSISTANT, wrong_final]) as model, \
             patch.object(agent, "search_pubmed", side_effect=RuntimeError("response không phải JSON")), \
             redirect_stdout(output):
            success = agent.run_question("Tìm bài báo", "test")
        self.assertFalse(success)
        self.assertEqual(model.call_count, 1)
        self.assertIn("Chưa lấy được dữ liệu PubMed", output.getvalue())
        self.assertNotIn("Không có bài báo nào được tìm thấy", output.getvalue())

    def test_successful_tool_still_returns_result_to_model(self):
        data = {"total_matches": 1, "returned": 1, "pmids": ["123"]}
        with patch.object(agent, "ask_model", side_effect=[ASSISTANT, {"role": "assistant", "content": "PMID 123"}]) as model, \
             patch.object(agent, "search_pubmed", return_value=data), redirect_stdout(io.StringIO()):
            self.assertTrue(agent.run_question("Tìm bài báo", "test"))
        self.assertEqual(model.call_count, 2)
        tool_message = model.call_args.args[0][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertTrue(json.loads(tool_message["content"])["ok"])

    def test_one_shot_cli_returns_failure_for_failed_tool(self):
        with patch.object(sys, "argv", ["lesson", "--question", "Tìm bài"]), \
             patch.object(agent, "run_question", return_value=False), redirect_stdout(io.StringIO()):
            self.assertEqual(agent.main(), 1)


if __name__ == "__main__":
    unittest.main()
