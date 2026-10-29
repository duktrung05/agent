"""Goal -> decision -> tool -> evidence assessment -> updated state -> decision."""

from datetime import datetime, timezone
import json
import re
import time
from uuid import uuid4

from .core import AgentError, BudgetExceeded, Config, SourceBlocked, utc_now
from . import prompts
from .report import evidence_ledger, extract_study_fields, normalized, readiness, source_spans, validate_claims


class Agent:
    def __init__(self, store, model, sources, config=None, on_event=None):
        self.store, self.model, self.sources = store, model, sources
        self.config = (config or Config()).validate()
        self.on_event = on_event or (lambda kind, payload: None)

    def save(self, state, kind, payload=None):
        self.store.checkpoint(state, kind, payload)
        self.on_event(kind, payload or {})

    def start(self, goal):
        if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 4000:
            raise ValueError("Mục tiêu phải có nội dung và tối đa 4000 ký tự.")
        state = {
            "id": uuid4().hex[:12], "goal": goal.strip(), "created_at": utc_now(), "status": "planning",
            "config": self.config.to_dict(), "plan": None, "clarifications": [], "question": None,
            "steps": 0, "model_calls": 0, "tool_calls": 0, "searches": 0, "elapsed_seconds": 0.0,
            "candidates": {}, "documents": {}, "pending_action": None, "pending_assessments": [],
            "search_history": [], "observations": [], "errors": [], "invalid_actions": 0,
            "claims": [], "report_errors": [], "pubmed_disabled": self.config.offline,
            "review": {"status": "pending"},
            "schema_version": "0.2", "prompt_version": "v2",
        }
        self.current_run_id = state["id"]
        self.save(state, "run_created", {"goal": state["goal"], "offline": self.config.offline})
        return self.run(state)

    def resume(self, run_id, answer=None, offline=None):
        state = self.store.load(run_id)
        self.current_run_id = run_id
        if offline is not None:
            state["config"]["offline"] = offline
        self.config = Config(**state["config"]).validate()
        from .model import OllamaModel
        if isinstance(self.model, OllamaModel):
            self.model = OllamaModel(self.config)
        if state["status"] in {"completed", "partial", "failed", "out_of_scope"} and not answer:
            return state
        if state["status"] == "waiting_for_user" and not answer:
            return state
        if answer:
            if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 2000:
                raise ValueError("Nội dung bổ sung tối đa 2000 ký tự.")
            state["clarifications"].append(answer.strip())
            state["plan"] = None
            state["claims"] = []
            for doc in state["documents"].values():
                doc.pop("assessment", None)
                doc["eligible"] = False
            state["pending_assessments"] = list(state["documents"])
        state["question"] = None
        state["pubmed_disabled"] = self.config.offline
        state["status"] = "running" if state["plan"] else "planning"
        self.save(state, "run_resumed", {"answer": answer})
        return self.run(state)

    def _model(self, state, purpose, system, packet, schema):
        if state["model_calls"] >= self.config.max_model_calls:
            raise BudgetExceeded("Hết ngân sách model call.")
        remaining = self.config.max_seconds - state["elapsed_seconds"]
        if remaining <= 0:
            raise BudgetExceeded("Hết ngân sách thời gian chủ động.")
        # Garde-fou bảo thủ theo byte, không giả vờ đây là tokenizer chính xác.
        byte_size = len(json.dumps({"system": system, "packet": packet, "schema": schema}, ensure_ascii=False).encode())
        if byte_size > max(1024, self.config.num_ctx - self.config.max_output - 512):
            raise AgentError(f"Context packet quá lớn cho cấu hình bảo thủ ({byte_size} bytes); không tự cắt bằng chứng.")
        state["model_calls"] += 1
        self.save(state, "model_started", {"purpose": purpose, "model": self.config.model, "packet": packet, "prompt_version": "v2"})
        started = time.monotonic()
        try:
            result, usage = self.model.generate(system, packet, schema, min(self.config.request_timeout, remaining))
        except KeyboardInterrupt:
            state["elapsed_seconds"] += time.monotonic() - started
            self.save(state, "model_interrupted", {"purpose": purpose})
            raise
        except (AgentError, ValueError, OSError) as exc:
            state["elapsed_seconds"] += time.monotonic() - started
            self.save(state, "model_error", {"purpose": purpose, "error": str(exc)})
            raise AgentError(str(exc)) from exc
        state["elapsed_seconds"] += time.monotonic() - started
        self.save(state, "model_completed", {"purpose": purpose, "usage": usage, "output": result})
        return result

    def _plan(self, state):
        result = self._model(state, "plan", prompts.PLAN_PROMPT, {"goal": state["goal"], "clarifications": state["clarifications"], "as_of": utc_now(), "max_sources": self.config.max_sources}, prompts.PLAN_SCHEMA)
        for field in ("topic", "query", "question_type", "clarification"):
            if not isinstance(result.get(field), str):
                raise AgentError(f"Plan thiếu {field} dạng chuỗi.")
        criteria, target = result.get("criteria"), result.get("target_sources")
        if not isinstance(criteria, list) or not 1 <= len(criteria) <= 4 or not all(isinstance(x, str) and 1 <= len(x) <= 300 for x in criteria):
            raise AgentError("Plan phải có 1-4 tiêu chí ngắn.")
        if type(target) is not int or not 1 <= target <= 20 or result["question_type"] not in prompts.SKILLS:
            raise AgentError("Số nguồn hoặc loại câu hỏi trong plan không hợp lệ.")
        goal = state["goal"]
        narrow = re.search(r"(?:chỉ cần|only need)\s+(.+)", goal, re.I)
        if narrow and len(narrow[1].strip()) <= 300:
            # Giữ nguyên yêu cầu giới hạn rõ của người dùng, không tự đẻ thêm tiêu chí.
            result["criteria"] = [narrow[1].strip().rstrip(".!?")]
        latest = state["clarifications"][-1] if state["clarifications"] else ""
        if "needs_clarification" in result and type(result["needs_clarification"]) is not bool:
            raise AgentError("needs_clarification phải là boolean.")
        clarification = normalized(result["clarification"]).casefold().strip(" .!?:")
        acknowledged = latest and (
            clarification == normalized(latest).casefold().strip(" .!?:")
            or ("?" not in result["clarification"] and re.match(r"^(?:đã (?:xác nhận|bổ sung|làm rõ)|confirmed|acknowledged)\b", clarification))
        )
        if result.get("needs_clarification") is False or acknowledged:
            result["clarification"] = ""
        scope = latest if re.search(r"\b(?:19|20)\d{2}\b", latest) else goal
        years = [int(x) for x in re.findall(r"\b(?:19|20)\d{2}\b", scope)]
        if years:
            result["start_year"], result["end_year"] = min(years), max(years)
        else:
            # Model không được tự thêm bộ lọc năm mà người dùng chưa yêu cầu.
            result["start_year"], result["end_year"] = None, None
        count_pattern = r"\b(\d+)\s*(?:bài|nguồn|nghiên cứu|papers?|sources?)\b"
        explicit_count = re.search(count_pattern, latest, re.I) or re.search(count_pattern, goal, re.I)
        if explicit_count:
            result["target_sources"] = int(explicit_count[1])
        for field in ("start_year", "end_year"):
            value = result.get(field)
            if value is not None and (type(value) is not int or not 1800 <= value <= 2100):
                raise AgentError("Khoảng năm trong plan không hợp lệ.")
        if result.get("start_year") and result.get("end_year") and result["start_year"] > result["end_year"]:
            raise AgentError("Năm đầu lớn hơn năm cuối.")
        if re.search(r"gần đây|recent|latest", goal, re.I) and not years and not latest:
            result["clarification"] = "Bạn muốn dùng khoảng năm công bố nào cho 'gần đây'?"
        if result["target_sources"] < 1 or result["target_sources"] > self.config.max_sources:
            result["clarification"] = f"Mục tiêu {result['target_sources']} nguồn vượt cấu hình tối đa {self.config.max_sources}. Hãy bổ sung phạm vi hoặc tạo run với giới hạn phù hợp."
        if result.get("start_year", 0) and result["start_year"] > datetime.now(timezone.utc).year:
            result["clarification"] = "Khoảng năm bắt đầu ở tương lai; bạn muốn tra cứu phạm vi nào đã có dữ liệu?"
        state["plan"] = result
        user_text = normalized(" ".join([goal, *state["clarifications"]])).casefold()
        stated = {}
        for field in ("population", "intervention_or_exposure", "outcome"):
            value = result.get(field, "")
            stated[field] = value if isinstance(value, str) and value.strip() and normalized(value).casefold() in user_text else None
        state["question_spec"] = {
            "original_question": goal, "clarifications": list(state["clarifications"]),
            "requested_years": [min(years), max(years)] if years else None,
            "applied_years": [result.get("start_year"), result.get("end_year")],
            "search_as_of": utc_now(), **stated,
        }
        self.save(state, "plan_created", {"plan": result})
        personal = re.search(r"tôi (?:bị|đang|có bệnh|nên)|(?:mẹ|bố|cha|con|vợ|chồng) tôi", user_text)
        instruction = re.search(r"chẩn đoán|kê đơn|liều|uống thuốc|đổi thuốc|nên uống|điều trị cho", user_text)
        if result.get("scope_status") == "out_of_scope" or (personal and instruction):
            state["status"] = "out_of_scope"
            state["stop_reason"] = "Yêu cầu chẩn đoán/kê đơn/điều trị cá nhân ngoài phạm vi tra cứu tài liệu của hệ thống."
            self.save(state, "scope_rejected", {"reason": state["stop_reason"]})
            return
        if result["clarification"]:
            state["question"] = result["clarification"]
            state["status"] = "waiting_for_user"
            self.save(state, "waiting_for_user", {"question": state["question"]})

    def _assess(self, state, source_id):
        doc = state["documents"][source_id]
        plan = state["plan"]
        reason = None
        if not doc.get("content", "").strip():
            reason = "Nguồn không có nội dung/abstract để đọc."
        elif (plan.get("start_year") or plan.get("end_year")) and doc.get("year") is None:
            reason = "Thiếu năm để xác nhận phạm vi người dùng yêu cầu."
        elif plan.get("start_year") and doc["year"] < plan["start_year"]:
            reason = "Nguồn cũ hơn khoảng năm yêu cầu."
        elif plan.get("end_year") and doc["year"] > plan["end_year"]:
            reason = "Nguồn mới hơn khoảng năm yêu cầu."
        if reason:
            assessment = {"relevant": False, "reason": reason, "study_type": "unknown", "evidence": []}
        else:
            spans = source_spans(doc["content"])
            packet = {"goal": state["goal"], "plan": plan, "criteria": {str(i): c for i, c in enumerate(plan["criteria"], 1)}, "untrusted_document": {"id": source_id, "title": doc.get("title"), "year": doc.get("year"), "spans": spans, "scope": doc.get("content_scope")}}
            try:
                schema = json.loads(json.dumps(prompts.ASSESS_SCHEMA))
                schema["properties"]["evidence"]["items"]["properties"]["span_id"]["enum"] = list(spans)
                raw = self._model(state, "assess_source", prompts.ASSESS_PROMPT + prompts.SKILLS[plan["question_type"]], packet, schema)
                if type(raw.get("relevant")) is not bool or not isinstance(raw.get("evidence"), list):
                    raise AgentError("Đánh giá nguồn thiếu relevant/evidence hợp lệ.")
                evidence = []
                for item in raw["evidence"][:2]:
                    if not isinstance(item, dict):
                        continue
                    span_id, note, covered = item.get("span_id"), item.get("note"), item.get("criteria")
                    if not isinstance(span_id, str) or span_id not in spans:
                        continue
                    quote = spans[span_id]
                    if not isinstance(note, str) or not note.strip() or not isinstance(covered, list):
                        continue
                    covered = sorted({c for c in covered if type(c) is int and 1 <= c <= len(plan["criteria"])})
                    if covered and not any(e["quote"] == quote for e in evidence):
                        evidence.append({"id": f"{source_id}#e{len(evidence) + 1}", "span_id": span_id, "quote": quote, "note": note[:400], "criteria": covered})
                assessment = {"relevant": raw["relevant"], "reason": str(raw.get("reason") or "")[:400], "study_type": str(raw.get("study_type") or "unknown")[:100], "evidence": evidence,
                              "study_fields": extract_study_fields(raw.get("study_fields"), spans)}
                if not evidence:
                    assessment["reason"] += " Không có trích đoạn hợp lệ được đối chiếu."
            except BudgetExceeded:
                raise
            except AgentError as exc:
                assessment = {"relevant": False, "reason": f"Chưa đánh giá được: {exc}", "study_type": "unknown", "evidence": []}
        doc["assessment"] = assessment
        doc["eligible"] = bool(assessment["relevant"] and assessment["evidence"])
        if source_id in state["pending_assessments"]:
            state["pending_assessments"].remove(source_id)
        state["observations"].append({"type": "source_assessment", "source_id": source_id, "eligible": doc["eligible"], "reason": assessment["reason"]})
        self.save(state, "source_assessed", {"source_id": source_id, "eligible": doc["eligible"], "reason": assessment["reason"], "evidence_ids": [e["id"] for e in assessment["evidence"]]})

    def _actions(self, state):
        actions = []
        if state["searches"] < self.config.max_searches:
            if not state["pubmed_disabled"]:
                actions.append("search_pubmed")
            if self.store.has_unread_documents(state["documents"]):
                actions.append("search_local")
        if len(state["documents"]) < self.config.max_sources and any(s not in state["documents"] for s in state["candidates"]):
            actions.append("read_source")
        ready = readiness(state)["ready"]
        if ready:
            actions.append("finish")
        else:
            actions.append("ask_user")
        return actions

    def _packet(self, state):
        candidates = []
        for sid, item in state["candidates"].items():
            if sid not in state["documents"]:
                candidates.append({"id": sid, "title": (item.get("title") or "chưa lấy tiêu đề")[:150], "year": item.get("year"), "origin": item.get("origin")})
        assessments = [{"id": sid, "title": (d.get("title") or "")[:100], "eligible": d.get("eligible"), "reason": d.get("assessment", {}).get("reason", "")[:160]} for sid, d in state["documents"].items()]
        return {"goal": state["goal"], "plan": state["plan"], "available_actions": self._actions(state), "readiness": readiness(state), "unread_candidates": candidates[:6], "other_candidates_count": max(0, len(candidates) - 6), "source_assessments": assessments, "recent_observations": state["observations"][-2:], "previous_searches": [{"action": s["action"], "query": s["query"], "count": s["count"], "scope": s.get("scope")} for s in state["search_history"]], "remaining": {"steps": self.config.max_steps - state["steps"], "tools": self.config.max_tool_calls - state["tool_calls"], "sources": self.config.max_sources - len(state["documents"])}}

    def _decision(self, state):
        if self.config.strategy == "fixed":
            return self._fixed_decision(state)
        packet = self._packet(state)
        schema = json.loads(json.dumps(prompts.DECISION_SCHEMA))
        schema["properties"]["action"]["enum"] = packet["available_actions"]
        ids = [item["id"] for item in packet["unread_candidates"]]
        if ids:
            schema["properties"]["source_ids"]["items"]["enum"] = ids
        else:
            schema["properties"]["source_ids"]["maxItems"] = 0
        return self._model(state, "decide", prompts.DECISION_PROMPT + prompts.SKILLS[state["plan"]["question_type"]], packet, schema)

    def _fixed_decision(self, state):
        """Baseline: một query từ plan, đọc top-k theo thứ tự, không tìm bổ sung."""
        action, ids = None, []
        available = self._actions(state)
        if not state["search_history"]:
            action = "search_local" if self.config.offline else "search_pubmed"
            if action not in available:
                return None
        else:
            initial = state["search_history"][0].get("candidate_ids", list(state["candidates"]))[:self.config.top_k]
            unread = [sid for sid in initial if sid not in state["documents"]]
            if unread and "read_source" in available:
                action, ids = "read_source", [unread[0]]
            elif "finish" in available:
                action = "finish"
            else:
                return None
        return {"action": action, "query": state["plan"]["query"] if action.startswith("search_") else "", "source_ids": ids,
                "message": "", "reason": "Baseline cố định: một truy vấn và đọc top-k, không điều chỉnh theo kết quả."}

    def _validate_action(self, state, decision):
        action = decision.get("action")
        if action not in self._actions(state):
            raise ValueError(f"Hành động không được phép ở state này: {action!r}.")
        if action.startswith("search_"):
            query = decision.get("query")
            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1000:
                raise ValueError("Truy vấn không hợp lệ.")
            scope = [state["plan"].get("start_year"), state["plan"].get("end_year")]
            if any(s["action"] == action and s.get("scope") == scope and normalized(s["query"]).casefold() == normalized(query).casefold() for s in state["search_history"]):
                raise ValueError("Truy vấn này đã chạy; hãy đọc ứng viên hiện có hoặc đổi truy vấn.")
        if action == "read_source":
            ids = decision.get("source_ids")
            if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or ids[0] not in state["candidates"]:
                raise ValueError("Chỉ được đọc một source ID đã xuất hiện trong kết quả tìm kiếm.")
            if ids[0] in state["documents"]:
                raise ValueError("Nguồn này đã đọc; không tạo thêm tiến triển.")
        if action == "ask_user" and (not isinstance(decision.get("message"), str) or not 1 <= len(decision["message"].strip()) <= 1000):
            raise ValueError("Câu hỏi làm rõ không hợp lệ.")
        return action

    def _execute_pending(self, state):
        decision = state["pending_action"]
        action = decision["action"]
        remaining = max(0.1, min(30, self.config.max_seconds - state["elapsed_seconds"]))
        started = time.monotonic()
        try:
            if action == "search_pubmed":
                result = self.sources.search_pubmed(decision["query"], state["plan"], timeout=remaining)
            elif action == "search_local":
                result = self.sources.search_local(decision["query"])
            else:
                source_id = decision["source_ids"][0]
                doc = self.sources.read_source(source_id, offline=state["pubmed_disabled"], timeout=remaining)
                if doc.get("id") != source_id or not isinstance(doc.get("content"), str):
                    raise AgentError("Tool trả tài liệu sai định danh hoặc cấu trúc.")
                state["documents"][source_id] = doc
                state["candidates"][source_id].update({"title": doc.get("title"), "year": doc.get("year")})
                state["pending_assessments"].append(source_id)
                result = {"source_id": source_id, "title": doc.get("title"), "scope": doc.get("content_scope"), "read_mode": doc.get("read_mode"), "fetched_at": doc.get("fetched_at")}
            if action.startswith("search_"):
                for item in result["candidates"]:
                    state["candidates"].setdefault(item["id"], item)
                state["search_history"].append({"action": action, "query": decision["query"], "count": len(result["candidates"]),
                                               "candidate_ids": [item["id"] for item in result["candidates"]],
                                               "query_translation": result.get("query_translation"), "warnings": result.get("warnings"),
                                               "fetched_at": result.get("fetched_at"),
                                               "scope": [state["plan"].get("start_year"), state["plan"].get("end_year")], "source_mode": result.get("source_mode"), "at": utc_now()})
            state["observations"].append({"type": "tool_success", "action": action, "result": result if action == "read_source" else {"count": len(result["candidates"]), "source_mode": result.get("source_mode")}})
            state["invalid_actions"] = 0
            event, payload = "tool_completed", {"action": action, "result": result}
        except KeyboardInterrupt:
            state["elapsed_seconds"] += time.monotonic() - started
            self.save(state, "tool_interrupted", {"action": action})
            raise
        except SourceBlocked as exc:
            state["pubmed_disabled"] = True
            state["errors"].append(str(exc))
            state["observations"].append({"type": "source_blocked", "error": str(exc), "pubmed_available": False})
            if not self.store.document_count():
                state["status"] = "paused_source"
                state["stop_reason"] = "Nguồn PubMed đang chặn; có thể resume sau, hoặc nạp corpus local."
            event, payload = "source_blocked", {"action": action, "error": str(exc)}
        except (AgentError, ValueError, OSError) as exc:
            state["errors"].append(str(exc))
            state["observations"].append({"type": "tool_error", "action": action, "error": str(exc)[:500]})
            state["invalid_actions"] += 1
            event, payload = "tool_error", {"action": action, "error": str(exc)}
        state["elapsed_seconds"] += time.monotonic() - started
        state["pending_action"] = None
        self.save(state, event, payload)

    def _finish(self, state, reason, complete=False):
        state["stop_reason"] = reason
        state["status"] = "completed" if complete and readiness(state)["ready"] else "partial"
        ledger = evidence_ledger(state)
        if ledger and state["model_calls"] < self.config.max_model_calls and state["elapsed_seconds"] < self.config.max_seconds:
            try:
                packet = {"goal": state["goal"], "evidence": ledger, "scope": "Chỉ phần tài liệu đã đọc, chưa phải toàn văn hay toàn bộ y văn."}
                schema = json.loads(json.dumps(prompts.REPORT_SCHEMA))
                schema["properties"]["claims"]["items"]["properties"]["evidence_ids"]["items"]["enum"] = list(ledger)
                result = self._model(state, "report", prompts.REPORT_PROMPT, packet, schema)
                state["claims"], state["report_errors"] = validate_claims(result, state)
            except (AgentError, ValueError) as exc:
                state["report_errors"] = [str(exc)]
        if state["status"] == "completed" and (not state["claims"] or state["report_errors"]):
            state["status"] = "partial"
        self.save(state, "run_finished", {"status": state["status"], "reason": reason, "readiness": readiness(state)})
        self._write_report(state)
        return state

    def _write_report(self, state):
        self.store.write_report(state)

    def run(self, state):
        try:
            if state["plan"] is None:
                self._plan(state)
            if state["status"] in {"waiting_for_user", "out_of_scope"}:
                self._write_report(state)
                return state
            state["status"] = "running"
            # Pending action được checkpoint trước khi gọi tool; resume chỉ lặp thao tác đọc.
            if state["pending_action"]:
                if state["elapsed_seconds"] >= self.config.max_seconds:
                    return self._finish(state, "Hết thời gian trước khi thực hiện lại pending action.")
                self._execute_pending(state)
            while state["status"] == "running":
                while state["pending_assessments"]:
                    self._assess(state, state["pending_assessments"][0])
                if state["steps"] >= self.config.max_steps or state["tool_calls"] >= self.config.max_tool_calls or state["elapsed_seconds"] >= self.config.max_seconds:
                    return self._finish(state, "Đã chạm giới hạn bước/tool/thời gian; giữ báo cáo một phần.")
                if state["invalid_actions"] >= 2:
                    return self._finish(state, "Không có tiến triển sau hai quyết định/lỗi liên tiếp.")
                state["steps"] += 1
                decision = self._decision(state)
                if decision is None:
                    return self._finish(state, "Baseline đã đọc hết top-k hoặc không còn tool phù hợp; chưa đủ tiêu chí.")
                self.save(state, "decision", decision)
                try:
                    action = self._validate_action(state, decision)
                except ValueError as exc:
                    state["invalid_actions"] += 1
                    state["observations"].append({"type": "policy_rejection", "error": str(exc)})
                    self.save(state, "policy_rejection", {"error": str(exc)})
                    continue
                if action == "ask_user":
                    state["status"], state["question"] = "waiting_for_user", decision["message"]
                    self.save(state, "waiting_for_user", {"question": state["question"]})
                    self._write_report(state)
                    return state
                if action == "finish":
                    return self._finish(state, "Đạt số nguồn và độ phủ tiêu chí theo kiểm tra cấu trúc; nội dung chờ review.", complete=True)
                state["pending_action"] = decision
                state["tool_calls"] += 1
                if action.startswith("search_"):
                    state["searches"] += 1
                self.save(state, "tool_started", {"action": action, "arguments": decision})
                self._execute_pending(state)
            self._write_report(state)
            return state
        except BudgetExceeded as exc:
            return self._finish(state, str(exc))
        except (AgentError, ValueError, OSError) as exc:
            state["errors"].append(str(exc))
            state["status"] = "failed" if not state["documents"] else "partial"
            state["stop_reason"] = str(exc)
            self.save(state, "run_error", {"error": str(exc)})
            self._write_report(state)
            return state
