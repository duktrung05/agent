"""Local HTML application backed by the existing agent, using only the standard library."""

import argparse
from contextlib import contextmanager
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .core import AgentError, Config, utc_now
from .engine import Agent
from .model import OllamaModel
from .report import evidence_ledger, readiness, render_report
from .sources import Sources
from .store import Store
from .web_security import WebAccess, create_auth_file


FRONTEND = Path(__file__).parent / "frontend"
RUN_ID = re.compile(r"^[a-f0-9]{12}$")
TERMINAL = {"completed", "partial", "failed", "out_of_scope"}


class WebError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class StopRequested(Exception):
    pass


def run_summary(state):
    return {key: state.get(key) for key in ("id", "goal", "status", "created_at", "updated_at", "stop_reason", "review")}


def run_detail(state):
    ledger = evidence_ledger(state)
    sources = []
    for sid, doc in state["documents"].items():
        source = {key: doc.get(key) for key in ("title", "year", "doi", "pmid", "url", "fetched_at", "content_scope", "read_mode", "eligible", "assessment")}
        sources.append({"id": sid, **source})
    return {**run_summary(state), "question": state.get("question"), "plan": state.get("plan"),
            "config": state["config"], "readiness": readiness(state), "sources": sources,
            "claims": state.get("claims", []), "evidence": ledger, "search_history": state.get("search_history", []),
            "errors": state.get("errors", []), "report_errors": state.get("report_errors", []),
            "steps": state["steps"], "model_calls": state["model_calls"], "tool_calls": state["tool_calls"],
            "elapsed_seconds": state["elapsed_seconds"]}


class WebApp:
    def __init__(self, directory="outputs", config=None, model_factory=OllamaModel, access=None):
        self.access = access or WebAccess()
        self.directory = Path(directory).resolve()
        self.config = (config or Config(offline=True, max_seconds=300, max_sources=3)).validate()
        self.model_factory = model_factory
        self.csrf_token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.jobs = {}
        self.active_id = None
        self.worker = None
        self.stopping = False
        with self.store():
            pass

    @contextmanager
    def store(self):
        store = Store(self.directory)
        try:
            yield store
        finally:
            store.close()

    def _active(self):
        job = self.jobs.get(self.active_id)
        return job if job and job["status"] in {"queued", "running", "stopping"} else None

    @staticmethod
    def public_job(job):
        return {key: job.get(key) for key in ("id", "run_id", "status", "started_at", "finished_at", "error")}

    def bootstrap(self):
        with self.store() as store:
            runs = store.recent(30)
            count = store.document_count()
            total_runs = store.db.execute("SELECT count(*) FROM runs").fetchone()[0]
        with self.lock:
            job = self._active()
            active = self.public_job(job) if job else None
        return {"csrf_token": self.csrf_token, "documents_count": count, "runs_count": total_runs,
                "runs": [run_summary(s) for s in runs], "active_job": active, "model": self.config.model}

    def health(self):
        try:
            data = self.model_factory(self.config).health()
            names = [m.get("name") for m in data.get("models", [])]
            available = self.config.model in names or self.config.model + ":latest" in names
            return {"available": available, "model": self.config.model,
                    "message": "Model cục bộ sẵn sàng" if available else "Chưa tìm thấy model đã cấu hình trong Ollama"}
        except (AgentError, ValueError, OSError) as exc:
            return {"available": False, "model": self.config.model, "message": str(exc)}

    def get_run(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise WebError("ID phiên không hợp lệ.", 404)
        with self.store() as store:
            try:
                state = store.load(run_id)
            except ValueError as exc:
                raise WebError("Không tìm thấy phiên nghiên cứu.", 404) from exc
            # Model packets are intentionally excluded from the browser timeline.
            events = [{"seq": e["seq"], "at": e["at"], "kind": e["kind"],
                       "purpose": e["payload"].get("purpose"), "action": e["payload"].get("action")}
                      for e in store.events(run_id)[-60:]]
        return {"run": run_detail(state), "events": events}

    def documents(self, query=""):
        if len(query) > 1000:
            raise WebError("Truy vấn thư viện tối đa 1000 ký tự.")
        with self.store() as store:
            if query.strip():
                ids = [d["id"] for d in store.search_documents(query, limit=100)]
                docs = [store.get_document(sid) for sid in ids]
            else:
                docs = [json.loads(row["payload"]) for row in store.db.execute("SELECT payload FROM documents ORDER BY indexed_at DESC LIMIT 100")]
            total = store.document_count()
        return {"total": total, "documents": [{key: doc.get(key) for key in
                ("id", "title", "year", "doi", "pmid", "url", "fetched_at", "content_scope", "origin", "content")} for doc in docs]}

    def submit(self, payload, run_id=None):
        if not isinstance(payload, dict):
            raise WebError("Yêu cầu phải là JSON object.")
        if run_id:
            previous = self.get_run(run_id)["run"]
            if previous["status"] in TERMINAL:
                raise WebError("Phiên này đã kết thúc. Hãy tạo phiên mới để thay đổi phạm vi.", 409)
            answer = payload.get("answer")
            if answer is not None and (not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 2000):
                raise WebError("Trả lời làm rõ cần từ 1 đến 2000 ký tự.")
            if previous["status"] == "waiting_for_user" and not answer:
                raise WebError("Hãy trả lời câu hỏi làm rõ trước khi tiếp tục.")
            config = Config(**previous["config"]).validate()
        else:
            question = payload.get("question")
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000:
                raise WebError("Câu hỏi cần từ 1 đến 4000 ký tự.")
            offline = payload.get("offline", True)
            count, seconds = payload.get("max_sources", 3), payload.get("max_seconds", 300)
            if type(offline) is not bool or type(count) is not int or not 1 <= count <= 6 or type(seconds) is not int or not 60 <= seconds <= 600:
                raise WebError("Cấu hình không hợp lệ: 1–6 nguồn, 60–600 giây, offline là boolean.")
            config = replace(self.config, offline=offline, max_sources=count, max_seconds=seconds).validate()
        with self.lock:
            if self.stopping:
                raise WebError("Server đang dừng; hãy thử lại sau.", 503)
            if self._active():
                raise WebError("Đang có một phiên nghiên cứu. Hãy chờ hoàn tất hoặc dừng phiên đó.", 409)
            identifier = uuid4().hex[:12]
            job = {"id": identifier, "run_id": run_id, "status": "queued", "started_at": utc_now(),
                   "finished_at": None, "error": None, "stop": threading.Event()}
            self.jobs[identifier] = job
            self.active_id = identifier
            # Keep completed jobs bounded, while preserving all runs in SQLite.
            for old in list(self.jobs)[:-30]:
                if old != self.active_id:
                    self.jobs.pop(old, None)
            worker = threading.Thread(target=self._work, args=(job, config, payload, run_id), daemon=True)
            self.worker = worker
            worker.start()
        return self.public_job(job)

    def stop(self):
        with self.lock:
            self.stopping = True
            active = self._active()
            if active:
                active["stop"].set()
            worker = self.worker
        # Allow the current Ollama request to return and the worker to checkpoint.
        if worker:
            worker.join(timeout=self.config.request_timeout + 15)
        return worker is None or not worker.is_alive()

    def _work(self, job, config, payload, run_id):
        store, agent, final = None, None, "failed"
        try:
            store = Store(self.directory)
            def on_event(kind, details):
                with self.lock:
                    job["run_id"] = agent.current_run_id
                    job["status"] = "stopping" if job["stop"].is_set() else "running"
                if job["stop"].is_set():
                    raise StopRequested()

            agent = Agent(store, self.model_factory(config), Sources(store), config, on_event=on_event)
            if run_id:
                agent.resume(run_id, payload.get("answer"))
            else:
                agent.start(payload["question"])
            final = "finished"
        except StopRequested:
            state = store.load(agent.current_run_id)
            # Do not turn a checkpoint already completed into a resumable run.
            if state["status"] not in TERMINAL:
                state["status"] = "interrupted"
            state["stop_reason"] = "Đã dừng theo yêu cầu; checkpoint và ngân sách được giữ lại."
            store.checkpoint(state, "run_interrupted")
            store.write_report(state)
            final = "stopped"
        except Exception as exc:
            with self.lock:
                job["error"] = str(exc)
            if store and agent and getattr(agent, "current_run_id", None):
                try:
                    state = store.load(agent.current_run_id)
                    state["status"] = "partial" if state["documents"] else "failed"
                    state["stop_reason"] = str(exc)
                    store.checkpoint(state, "web_error", {"error": str(exc)})
                    store.write_report(state)
                except (ValueError, OSError, sqlite3.Error):
                    pass
        finally:
            try:
                if store:
                    store.close()
            finally:
                with self.lock:
                    job["status"] = final
                    job["finished_at"] = utc_now()

    def get_job(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            if not job:
                raise WebError("Không tìm thấy tác vụ; xem lại lịch sử phiên.", 404)
            result = self.public_job(job)
        detail = self.get_run(result["run_id"]) if result["run_id"] else {"run": None, "events": []}
        return {"job": result, **detail}

    def cancel(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            if not job or job["status"] not in {"queued", "running", "stopping"}:
                raise WebError("Tác vụ này không còn chạy.", 409)
            job["stop"].set()
            job["status"] = "stopping"
            return self.public_job(job)

    def import_document(self, payload):
        name, content = payload.get("name"), payload.get("content")
        if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name or Path(name).suffix.lower() not in {".json", ".jsonl", ".txt", ".md"}:
            raise WebError("Chọn file txt, md, json hoặc jsonl có tên hợp lệ.")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 10 * 1024 * 1024:
            raise WebError("Nội dung file tối đa 10 MB.")
        raw = content.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        folder = self.directory / "uploads"
        with self.lock:
            if self._active():
                raise WebError("Hãy chờ phiên đang chạy hoàn tất trước khi cập nhật thư viện.", 409)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (digest + Path(name).suffix.lower())
            created = not path.exists()
            if created:
                path.write_bytes(raw)
            elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise WebError("File đã lưu không khớp hash, chưa nhập lại dữ liệu.")
            try:
                with self.store() as store:
                    count = Sources(store).ingest(path)
            except (ValueError, OSError, sqlite3.Error):
                if created:
                    path.unlink(missing_ok=True)
                raise
        return {"imported": count, "name": name}

    def review(self, run_id, payload):
        decision, note, reviewer = payload.get("decision"), payload.get("note", ""), payload.get("reviewer", "")
        if decision not in {"approved", "rejected"} or not isinstance(note, str) or len(note) > 2000 or not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 100:
            raise WebError("Cần quyết định review, tên người đọc và ghi chú tối đa 2000 ký tự.")
        with self.lock:
            if self._active():
                raise WebError("Hãy chờ phiên đang chạy hoàn tất trước khi review.", 409)
            self.get_run(run_id)
            with self.store() as store:
                state = store.load(run_id)
                if state["status"] not in TERMINAL:
                    raise WebError("Phiên chưa kết thúc để review.", 409)
                state["review"] = {"status": decision, "kind": "human", "reviewer": reviewer.strip(), "note": note}
                store.checkpoint(state, "human_review", state["review"])
                store.write_report(state)
        return self.get_run(run_id)


class Handler(BaseHTTPRequestHandler):
    server_version = "MediSearch/1.0"

    @property
    def app(self):
        return self.server.app

    def send_content(self, status, content, content_type="application/json; charset=utf-8", download=None):
        raw = json.dumps(content, ensure_ascii=False).encode() if content_type.startswith("application/json") else content
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="MediSearch", charset="UTF-8"')
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def check_host(self):
        host = self.headers.get("Host", "")
        if len(self.headers.get_all("Host", [])) != 1:
            raise WebError("Host không hợp lệ.", 403)
        if self.app.access.origin:
            expected = urlsplit(self.app.access.origin).netloc
            allowed = {expected}
            if ":" not in expected:
                allowed.add(expected + ":443")
            if host.lower() not in allowed:
                raise WebError("Tên miền không nằm trong cấu hình cho phép.", 403)
            return host
        try:
            parsed = urlsplit("http://" + host)
            valid = parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == self.server.server_port
        except ValueError:
            valid = False
        if not valid:
            raise WebError("Ứng dụng chỉ nhận yêu cầu từ localhost.", 403)
        return host

    def check_access(self):
        host = self.check_host()
        if (len(self.headers.get_all("Authorization", [])) > 1
                or not self.app.access.authenticated(self.headers.get("Authorization"))):
            self.close_connection = True
            raise WebError("Cần đăng nhập để truy cập MediSearch.", 401)
        return host

    def do_GET(self):
        try:
            self.check_access()
            path = urlsplit(self.path).path
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/favicon.svg": ("favicon.svg", "image/svg+xml")}
            if path in files:
                name, mime = files[path]
                return self.send_content(200, (FRONTEND / name).read_bytes(), mime)
            if path == "/api/bootstrap":
                return self.send_content(200, self.app.bootstrap())
            if path == "/api/health":
                return self.send_content(200, self.app.health())
            if path == "/api/documents":
                query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
                return self.send_content(200, self.app.documents(query))
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{12})", path)
            if match:
                return self.send_content(200, self.app.get_job(match[1]))
            match = re.fullmatch(r"/api/runs/([a-f0-9]{12})(/report)?", path)
            if match:
                if match[2]:
                    self.app.get_run(match[1])
                    with self.app.store() as store:
                        report = render_report(store.load(match[1])).encode("utf-8")
                    return self.send_content(200, report, "text/markdown; charset=utf-8", f"medisearch-{match[1]}.md")
                return self.send_content(200, self.app.get_run(match[1]))
            raise WebError("Không tìm thấy tài nguyên.", 404)
        except (WebError, ValueError, OSError, sqlite3.Error) as exc:
            self.send_content(getattr(exc, "status", 500), {"error": str(exc)})

    def do_POST(self):
        try:
            host = self.check_access()
            origin = self.headers.get("Origin")
            expected = self.app.access.origin or "http://" + host
            if (len(self.headers.get_all("Origin", [])) > 1
                    or (self.app.access.origin and origin is None)
                    or (origin is not None and origin != expected)):
                raise WebError("Yêu cầu khác nguồn bị từ chối.", 403)
            token = self.headers.get("X-App-Token", "")
            if not token.isascii() or not secrets.compare_digest(token, self.app.csrf_token):
                raise WebError("Phiên trình duyệt đã hết hạn. Hãy tải lại trang.", 403)
            path = urlsplit(self.path).path
            limit = 14 * 1024 * 1024 if path == "/api/documents" else 32 * 1024
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                raise WebError("Yêu cầu cần Content-Type application/json.", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise WebError("Kích thước request không hợp lệ.") from exc
            if not 0 < length <= limit:
                raise WebError("Request rỗng hoặc vượt giới hạn kích thước.", 413)
            try:
                payload = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError) as exc:
                raise WebError("JSON không hợp lệ.") from exc
            if not isinstance(payload, dict):
                raise WebError("Yêu cầu phải là JSON object.")
            if path == "/api/runs":
                return self.send_content(202, {"job": self.app.submit(payload)})
            if path == "/api/documents":
                return self.send_content(201, self.app.import_document(payload))
            match = re.fullmatch(r"/api/runs/([a-f0-9]{12})/(resume|review)", path)
            if match:
                if match[2] == "review":
                    return self.send_content(200, self.app.review(match[1], payload))
                return self.send_content(202, {"job": self.app.submit(payload, match[1])})
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{12})/cancel", path)
            if match:
                return self.send_content(200, {"job": self.app.cancel(match[1])})
            raise WebError("Không tìm thấy thao tác.", 404)
        except (WebError, ValueError, OSError, sqlite3.Error) as exc:
            self.send_content(getattr(exc, "status", 400), {"error": str(exc)})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chạy MediSearch trên localhost hoặc sau HTTPS reverse proxy.")
    parser.add_argument("--home", default="outputs")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--public-origin", help="HTTPS origin duy nhất được phép truy cập qua reverse proxy")
    parser.add_argument("--auth-file", help="File tài khoản đã tạo bằng --create-auth")
    parser.add_argument("--create-auth", metavar="FILE", help="Tạo file tài khoản bằng nhập mật khẩu ẩn rồi thoát")
    parser.add_argument("--username", default="admin", help="Tên đăng nhập khi tạo tài khoản")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Port phải từ 1024 đến 65535.")
    try:
        if args.create_auth:
            create_auth_file(args.create_auth, args.username)
            print("Đã tạo file tài khoản với quyền 0600; không lưu mật khẩu gốc.")
            return 0
        access = WebAccess(args.public_origin, args.auth_file)
        app = WebApp(args.home, access=access)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        server.app = app
        print(f"MediSearch đang chạy: {access.origin or f'http://127.0.0.1:{args.port}'}", flush=True)
        print(f"Dữ liệu: {app.directory} · Ctrl+C để dừng server.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nĐang dừng server và chờ lưu checkpoint...", flush=True)
            saved = app.stop()
            print("Đã dừng server; có thể resume checkpoint ở lần mở tiếp theo." if saved
                  else "Worker chưa dừng đúng hạn; kiểm tra checkpoint trong lịch sử khi mở lại.")
        finally:
            server.server_close()
        return 0
    except (OSError, ValueError, EOFError, sqlite3.Error) as exc:
        parser.exit(1, f"Không mở được app: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
