"""Local web API tests use controlled models and in-memory HTTP, without sockets/Ollama."""

import io
import base64
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from medical_agent.core import Config
from medical_agent.web import Handler, WebApp, WebError
from medical_agent.web_security import WebAccess, create_auth_file, public_origin
from test_system import QUOTE, ScriptedModel, assessment, decision, report


def fixture_model(config):
    return ScriptedModel([decision("search_local", "broad"), decision("read_source", source_id="pubmed:101"), decision("finish")], [assessment()], [report()])


class SocketFixture:
    def __init__(self, request):
        self.request = request
        self.response = bytearray()

    def makefile(self, mode, *args):
        return io.BytesIO(self.request)

    def sendall(self, value):
        self.response.extend(value)


class QuietHandler(Handler):
    def log_message(self, *args):
        pass


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = WebApp(self.temp.name, model_factory=fixture_model)
        self.app.import_document({"name": "fixture.json", "content": json.dumps({"records": [{"pmid": "101", "title": "Synthetic broad source", "abstract": QUOTE, "year": 2024}]})})

    def tearDown(self):
        self.temp.cleanup()

    def wait(self, identifier):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            data = self.app.get_job(identifier)
            if data["job"]["status"] not in {"queued", "running", "stopping"}:
                return data
            time.sleep(.01)
        self.fail("Fixture worker did not finish")

    def http(self, path="/", method="GET", payload=None, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode() if payload is not None else b""
        values = {"Host": "127.0.0.1:8001", "Content-Type": "application/json", "Content-Length": str(len(body))}
        values.update(headers or {})
        request = (f"{method} {path} HTTP/1.0\r\n" + "".join(f"{key}: {value}\r\n" for key, value in values.items()) + "\r\n").encode() + body
        connection = SocketFixture(request)
        QuietHandler(connection, ("127.0.0.1", 12345), SimpleNamespace(app=self.app, server_port=8001))
        head, _, result = bytes(connection.response).partition(b"\r\n\r\n")
        return int(head.split(b" ")[1]), head.decode(), result

    def test_async_agent_run_exposes_progress_and_literal_evidence(self):
        job = self.app.submit({"question": "Tóm tắt 1 bài nghiên cứu.", "offline": True})
        result = self.wait(job["id"])
        self.assertEqual(result["job"]["status"], "finished")
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["run"]["evidence"]["pubmed:101#e1"]["quote"], QUOTE)
        self.assertTrue(result["run"]["claims"])
        self.assertTrue(result["events"])
        self.assertTrue(all("payload" not in event for event in result["events"]))
        self.assertIsNone(self.app.bootstrap()["active_job"])

    def test_busy_cancel_resume_preserves_same_run_and_budget(self):
        entered, release = threading.Event(), threading.Event()
        model = fixture_model(Config())
        generate = model.generate

        def blocked(*args):
            entered.set()
            release.wait(3)
            return generate(*args)

        model.generate = blocked
        self.app.model_factory = lambda config: model
        job = self.app.submit({"question": "Tóm tắt 1 bài."})
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(WebError) as busy:
                self.app.submit({"question": "Phiên thứ hai."})
            self.assertEqual(busy.exception.status, 409)
            with self.assertRaises(WebError):
                self.app.import_document({"name": "other.txt", "content": "Synthetic"})
            self.app.cancel(job["id"])
        finally:
            release.set()
        stopped = self.wait(job["id"])
        self.assertEqual(stopped["run"]["status"], "interrupted")
        self.assertEqual(stopped["run"]["model_calls"], 1)
        self.app.model_factory = fixture_model
        next_job = self.app.submit({}, stopped["run"]["id"])
        resumed = self.wait(next_job["id"])
        self.assertEqual(resumed["run"]["id"], stopped["run"]["id"])
        self.assertEqual(resumed["run"]["status"], "completed")
        self.assertGreater(resumed["run"]["model_calls"], stopped["run"]["model_calls"])

    def test_resume_waiting_run_requires_answer_and_keeps_scope(self):
        model = fixture_model(Config())
        plan = model.queues["plan"][0].copy()
        model.queues["plan"].append(plan.copy())
        self.app.model_factory = lambda config: model
        first = self.wait(self.app.submit({"question": "Tìm 1 bài gần đây."})["id"])
        self.assertEqual(first["run"]["status"], "waiting_for_user")
        with self.assertRaises(WebError):
            self.app.submit({}, first["run"]["id"])
        resumed = self.wait(self.app.submit({"answer": "2020–2026"}, first["run"]["id"])["id"])
        self.assertEqual(resumed["run"]["status"], "completed")
        self.assertEqual(resumed["run"]["plan"]["start_year"], 2020)
        self.assertEqual(resumed["run"]["id"], first["run"]["id"])

    def test_rejects_invalid_inputs_before_creating_worker(self):
        for payload in ([], {"question": " "}, {"question": "x", "offline": "true"}, {"question": "x", "max_sources": 99}, {"question": "x", "max_seconds": True}):
            with self.subTest(payload=payload), self.assertRaises(WebError):
                self.app.submit(payload)
        self.assertFalse(self.app.jobs)
        with self.assertRaises(WebError):
            self.app.get_run("../../outside")

    def test_model_initialization_failure_does_not_leave_busy_job(self):
        def unavailable(config):
            raise ValueError("Fixture model initialization failed")
        self.app.model_factory = unavailable
        result = self.wait(self.app.submit({"question": "Tóm tắt 1 bài."})["id"])
        self.assertEqual(result["job"]["status"], "failed")
        self.assertIn("initialization failed", result["job"]["error"])
        self.assertIsNone(result["run"])
        self.assertIsNone(self.app.bootstrap()["active_job"])

    def test_import_is_idempotent_and_invalid_file_does_not_mutate_library(self):
        before = self.app.bootstrap()["documents_count"]
        with self.assertRaises(ValueError):
            self.app.import_document({"name": "bad.json", "content": "{bad json"})
        with self.assertRaises(WebError):
            self.app.import_document({"name": "../../outside.txt", "content": "No"})
        self.assertEqual(self.app.bootstrap()["documents_count"], before)
        self.assertEqual(len(list((Path(self.temp.name) / "uploads").glob("*"))), 1)
        self.assertEqual(self.app.documents("no_such_term")["documents"], [])

    def test_review_is_explicit_and_cannot_restart_completed_run(self):
        data = self.wait(self.app.submit({"question": "Tóm tắt 1 bài."})["id"])
        run_id = data["run"]["id"]
        with self.assertRaises(WebError):
            self.app.review(run_id, {"decision": "approved", "reviewer": ""})
        result = self.app.review(run_id, {"decision": "rejected", "reviewer": "fixture_reviewer", "note": "Synthetic review only."})
        self.assertEqual(result["run"]["review"]["status"], "rejected")
        self.assertEqual(result["run"]["review"]["kind"], "human")
        with self.assertRaises(WebError):
            self.app.submit({}, run_id)

    def test_http_serves_html_and_blocks_arbitrary_files(self):
        status, headers, body = self.http()
        self.assertEqual(status, 200)
        self.assertIn(b"MediSearch", body)
        self.assertIn("Content-Security-Policy:", headers)
        self.assertIn("script-src 'self'", headers)
        self.assertEqual(self.http("/../AGENTS.md")[0], 404)
        self.assertEqual(self.http(headers={"Host": "evil.example:8001"})[0], 403)

    def test_http_post_requires_token_same_origin_json_and_bounded_body(self):
        payload = {"question": "Tóm tắt 1 bài."}
        self.assertEqual(self.http("/api/runs", "POST", payload)[0], 403)
        self.assertEqual(self.http("/api/runs", "POST", payload, {"X-App-Token": "không-hợp-lệ"})[0], 403)
        valid = {"X-App-Token": self.app.csrf_token, "Origin": "http://127.0.0.1:8001"}
        self.assertEqual(self.http("/api/runs", "POST", payload, {**valid, "Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.http("/api/runs", "POST", payload, {**valid, "Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.http("/api/runs", "POST", None, valid, raw=b"not-json")[0], 400)
        self.assertEqual(self.http("/api/runs", "POST", payload, {**valid, "Content-Length": "99999999"})[0], 413)
        self.assertFalse(self.app.jobs)

    def test_http_can_start_poll_and_download_real_report_without_network(self):
        status, _, body = self.http("/api/runs", "POST", {"question": "Tóm tắt 1 bài."}, {"X-App-Token": self.app.csrf_token})
        self.assertEqual(status, 202)
        job = json.loads(body)["job"]
        result = self.wait(job["id"])
        status, _, body = self.http(f"/api/jobs/{job['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["run"]["status"], "completed")
        status, headers, body = self.http(f"/api/runs/{result['run']['id']}/report")
        self.assertEqual(status, 200)
        self.assertIn("Content-Disposition: attachment;", headers)
        self.assertIn("text/markdown", headers)
        self.assertIn(QUOTE.encode(), body)

    def public_access(self):
        path = Path(self.temp.name) / "auth.json"
        with patch("medical_agent.web_security.getpass.getpass", return_value="fixture-password-123"):
            create_auth_file(path, "reviewer")
        self.app.access = WebAccess("https://research.example.com", str(path))
        credential = base64.b64encode(b"reviewer:fixture-password-123").decode()
        return {"Host": "research.example.com", "Authorization": "Basic " + credential}

    def test_public_origin_requires_auth_and_rejects_invalid_configuration(self):
        for value in ("http://example.com", "https://user:pass@example.com", "https://example.com/path",
                      "https://example.com?x=1", "https://example.com#x", "https://example.com:0",
                      "https://example.com:70000", "https://example.com\n"):
            with self.subTest(origin=value):
                with self.assertRaises(ValueError):
                    public_origin(value)
        with self.assertRaises(ValueError):
            WebAccess("https://example.com")
        self.assertEqual(public_origin("https://EXAMPLE.com:443/"), "https://example.com")

    def test_public_auth_guards_html_data_and_mutations(self):
        valid = self.public_access()
        for path in ("/", "/app.js", "/api/bootstrap", "/api/documents", "/api/health"):
            with self.subTest(path=path):
                status, headers, body = self.http(path, headers={"Host": valid["Host"]})
                self.assertEqual(status, 401)
                self.assertIn("WWW-Authenticate: Basic", headers)
                self.assertNotIn(b"csrf_token", body)
        status, _, _ = self.http("/api/runs", "POST", {"question": "test"}, {"Host": valid["Host"]})
        self.assertEqual(status, 401)
        self.assertFalse(self.app.jobs)
        self.assertEqual(self.http(headers=valid)[0], 200)
        self.assertEqual(self.http("/api/bootstrap", headers=valid)[0], 200)
        self.assertEqual(self.http(headers={**valid, "Host": "127.0.0.1:8001"})[0], 403)
        self.assertEqual(self.http(headers={**valid, "Host": "evil.example"})[0], 403)
        for authorization in ("Basic !!!", "Bearer token", "Basic /w==", "Basic " + base64.b64encode(b"reviewer:incorrect-password").decode()):
            with self.subTest(authorization=authorization):
                self.assertEqual(self.http(headers={**valid, "Authorization": authorization})[0], 401)

    def test_public_post_requires_exact_https_origin_even_with_valid_login(self):
        valid = {**self.public_access(), "X-App-Token": self.app.csrf_token}
        payload = {"question": "Tóm tắt 1 bài."}
        for origin in (None, "null", "http://research.example.com", "https://evil.example"):
            with self.subTest(origin=origin):
                headers = {**valid, **({"Origin": origin} if origin else {})}
                self.assertEqual(self.http("/api/runs", "POST", payload, headers)[0], 403)
        self.assertFalse(self.app.jobs)
        status, _, body = self.http("/api/runs", "POST", payload, {**valid, "Origin": self.app.access.origin})
        self.assertEqual(status, 202)
        self.assertEqual(self.wait(json.loads(body)["job"]["id"])["run"]["status"], "completed")

    def test_forwarded_headers_cannot_override_host_or_origin(self):
        valid = {**self.public_access(), "X-App-Token": self.app.csrf_token,
                 "X-Forwarded-Host": "research.example.com", "X-Forwarded-Proto": "https"}
        self.assertEqual(self.http(headers={**valid, "Host": "evil.example"})[0], 403)
        self.assertEqual(self.http("/api/runs", "POST", {"question": "test"},
                                   {**valid, "Origin": "https://evil.example"})[0], 403)

    def test_auth_file_is_private_hashed_and_never_overwritten(self):
        self.public_access()
        path = Path(self.app.access.auth_file)
        before = path.read_bytes()
        self.assertNotIn(b"fixture-password-123", before)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with patch("medical_agent.web_security.getpass.getpass", return_value="replacement-password"):
            with self.assertRaises(FileExistsError):
                create_auth_file(path, "replacement")
        self.assertEqual(path.read_bytes(), before)
        path.write_text('{"username":"reviewer","salt":"bad"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            WebAccess("https://research.example.com", str(path))

    def test_service_stop_waits_for_checkpoint_and_keeps_run_resumable(self):
        entered, release = threading.Event(), threading.Event()
        model = fixture_model(Config())
        generate = model.generate

        def blocked(*args):
            entered.set()
            release.wait(3)
            return generate(*args)

        model.generate = blocked
        self.app.model_factory = lambda config: model
        job = self.app.submit({"question": "Tóm tắt 1 bài."})
        try:
            self.assertTrue(entered.wait(2))
            def release_after_stop():
                self.app.jobs[job["id"]]["stop"].wait(2)
                release.set()

            unblock = threading.Thread(target=release_after_stop)
            unblock.start()
            self.assertTrue(self.app.stop())
            unblock.join()
        finally:
            release.set()
        data = self.app.get_job(job["id"])
        self.assertEqual(data["job"]["status"], "stopped")
        self.assertEqual(data["run"]["status"], "interrupted")
        self.assertEqual(data["run"]["model_calls"], 1)
        with self.assertRaises(WebError) as stopped:
            self.app.submit({"question": "Phiên mới."})
        self.assertEqual(stopped.exception.status, 503)


if __name__ == "__main__":
    unittest.main()
