"""Kiểm tra vòng search/fetch, nguồn và điều kiện dừng, không gọi API thật."""

from contextlib import redirect_stdout
from copy import deepcopy
import importlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lessons"))
loop = importlib.import_module("05_agent_loop")


def call_message(name, arguments, index=0):
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": f"call-{index}",
        "function": {"index": index, "name": name, "arguments": arguments},
    }]}


SEARCH = call_message("search_pubmed", {"query": "sleep Alzheimer"})
FETCH = call_message("fetch_pubmed_records", {"pmids": ["123"]})
SEARCH_DATA = {"pmids": ["123"], "returned": 1, "total_matches": 10, "query_errors": {}}
FETCH_DATA = {
    "records": [{"pmid": "123", "title": "Synthetic fixture", "abstract": "Test fixture, not medical evidence.",
                 "content_scope": "metadata_and_abstract", "url": "https://example.test/123"}],
    "missing_pmids": [], "returned": 1,
}
FINAL = {"role": "assistant", "content": "Mô tả nội dung nguồn thử nghiệm. [PMID: 123]"}


class AgentLoopTests(unittest.TestCase):
    def test_search_fetch_and_final_follow_real_tool_result(self):
        with patch.object(loop.lesson3, "ask_model", side_effect=[SEARCH, FETCH, FINAL]) as model, \
             patch.object(loop.lesson3, "execute_tool", return_value={"ok": True, "data": deepcopy(SEARCH_DATA)}) as execute, \
             patch.object(loop, "fetch_pubmed_records", return_value=deepcopy(FETCH_DATA)) as fetch, \
             redirect_stdout(io.StringIO()):
            self.assertTrue(loop.run_question("Tìm và đọc một bài", "test"))
        self.assertEqual(model.call_count, 3)
        self.assertEqual(execute.call_args.args[0]["function"]["arguments"], {"query": "sleep Alzheimer", "limit": 1})
        fetch.assert_called_once_with(["123"])
        tool_messages = [m for m in model.call_args.args[0] if m["role"] == "tool"]
        self.assertEqual([m["tool_name"] for m in tool_messages], ["search_pubmed", "fetch_pubmed_records"])
        self.assertIn("Test fixture", json.loads(tool_messages[-1]["content"])["data"]["records"][0]["abstract"])

    def test_fetch_before_search_or_unknown_pmid_is_blocked(self):
        with patch.object(loop, "fetch_pubmed_records") as fetch:
            for known in [set(), {"999"}]:
                with self.subTest(known=known), self.assertRaises(ValueError):
                    loop.execute_tool(FETCH["tool_calls"][0], {"known_pmids": known, "records": {}})
            fetch.assert_not_called()

    def test_source_error_stops_without_final_model_call(self):
        with patch.object(loop.lesson3, "ask_model", return_value=SEARCH) as model, \
             patch.object(loop.lesson3, "execute_tool", return_value={"ok": False, "error": "offline"}), \
             redirect_stdout(io.StringIO()) as output:
            self.assertFalse(loop.run_question("Tìm một bài", "test"))
        self.assertEqual(model.call_count, 1)
        self.assertIn("không để model diễn giải lỗi", output.getvalue())

    def test_fetch_error_stops_before_summary(self):
        with patch.object(loop.lesson3, "ask_model", side_effect=[SEARCH, FETCH]) as model, \
             patch.object(loop.lesson3, "execute_tool", return_value={"ok": True, "data": deepcopy(SEARCH_DATA)}), \
             patch.object(loop, "fetch_pubmed_records", side_effect=RuntimeError("NCBI trả HTML")), \
             redirect_stdout(io.StringIO()):
            self.assertFalse(loop.run_question("Tìm và đọc một bài", "test"))
        self.assertEqual(model.call_count, 2)

    def test_blocked_ncbi_html_is_not_parsed_as_article(self):
        fetch_module = importlib.import_module("04_fetch_pubmed")
        response = io.BytesIO(b"<html><title>NCBI - WWW Error Blocked Diagnostic</title></html>")
        response.status = 200
        response.headers = {"Content-Type": "text/html"}
        state = {"known_pmids": {"123"}, "records": {}}
        with patch.object(fetch_module, "urlopen", return_value=response):
            result = loop.execute_tool(FETCH["tool_calls"][0], state)
        self.assertFalse(result["ok"])
        self.assertIn("NCBI trả trang chặn HTML", result["error"])
        self.assertEqual(state["records"], {})

    def test_repeated_call_detected_even_if_index_changes(self):
        repeated = call_message("search_pubmed", {"query": "sleep Alzheimer"}, index=7)
        with patch.object(loop.lesson3, "ask_model", side_effect=[SEARCH, repeated]), \
             patch.object(loop.lesson3, "execute_tool", return_value={"ok": True, "data": deepcopy(SEARCH_DATA)}) as execute, \
             redirect_stdout(io.StringIO()) as output:
            self.assertFalse(loop.run_question("Tìm một bài", "test"))
        self.assertEqual(execute.call_count, 1)
        self.assertIn("lặp lại", output.getvalue())

    def test_early_final_cannot_pretend_an_abstract_was_read(self):
        with patch.object(loop.lesson3, "ask_model", return_value=FINAL), redirect_stdout(io.StringIO()) as output:
            self.assertFalse(loop.run_question("Đọc một bài", "test"))
        self.assertIn("Chưa đọc được abstract", output.getvalue())
        self.assertNotIn("Mô tả nội dung nguồn", output.getvalue())

    def test_unknown_citation_is_rejected_in_text_or_url(self):
        state = {"records": {"123": FETCH_DATA["records"][0]}}
        for content in ["Nhận định [PMID: 999]", "PMID 999", "https://pubmed.ncbi.nlm.nih.gov/999/"]:
            with self.subTest(content=content), self.assertRaises(ValueError), redirect_stdout(io.StringIO()):
                loop.print_final(content, state)

    def test_program_renders_source_when_model_omits_marker(self):
        state = {"records": {"123": FETCH_DATA["records"][0]}}
        with redirect_stdout(io.StringIO()) as output:
            self.assertTrue(loop.print_final("Nội dung tóm tắt thử nghiệm.", state))
        self.assertIn("[PMID: 123] https://example.test/123", output.getvalue())

    def test_metadata_only_is_not_enough_for_summary(self):
        state = {"records": {"123": {**FETCH_DATA["records"][0], "abstract": None}}}
        with redirect_stdout(io.StringIO()):
            self.assertFalse(loop.print_final(FINAL["content"], state))

    def test_fourth_tool_is_blocked_by_budget(self):
        responses = [call_message("search_pubmed", {"query": f"query-{i}"}) for i in range(4)]
        with patch.object(loop.lesson3, "ask_model", side_effect=responses) as model, \
             patch.object(loop.lesson3, "execute_tool", return_value={"ok": True, "data": deepcopy(SEARCH_DATA)}) as execute, \
             redirect_stdout(io.StringIO()) as output:
            self.assertFalse(loop.run_question("Tìm một bài", "test"))
        self.assertEqual(model.call_count, 4)
        self.assertEqual(execute.call_count, 3)
        self.assertIn("Hết ngân sách tool", output.getvalue())

    def test_oversized_abstract_stops_without_silent_truncation(self):
        data = deepcopy(FETCH_DATA)
        data["records"][0]["abstract"] = "x" * 6001
        state = {"known_pmids": {"123"}, "records": {}}
        with patch.object(loop, "fetch_pubmed_records", return_value=data):
            result = loop.execute_tool(FETCH["tool_calls"][0], state)
        self.assertFalse(result["ok"])
        self.assertFalse(state["records"])


if __name__ == "__main__":
    unittest.main()
