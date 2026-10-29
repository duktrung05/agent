"""System behavior tests with controlled models/sources, not clinical benchmarks."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from medical_agent.core import Config, SourceBlocked
from medical_agent.engine import Agent
from medical_agent.model import OllamaModel
from medical_agent.report import normalized, readiness, source_spans, validate_claims
from medical_agent.sources import Sources
from medical_agent.store import Store


QUOTE = "This synthetic document addresses the requested research question."


def document(source_id="pubmed:101", content=QUOTE, year=2024):
    return {"id": source_id, "title": "Synthetic test source", "content": content, "year": year,
            "origin": "local" if source_id.startswith("local:") else "pubmed", "pmid": source_id.split(":")[-1] if source_id.startswith("pubmed:") else None,
            "content_scope": "fixture_text", "url": "https://example.test/source", "read_mode": "fixture_not_live", "fetched_at": datetime.now(timezone.utc).isoformat()}


def decision(action, query="", source_id=None, message=""):
    return {"action": action, "query": query, "source_ids": [source_id] if source_id else [], "message": message, "reason": "Controlled test decision"}


def assessment(relevant=True, quote=QUOTE):
    return {"relevant": relevant, "reason": "Relevant fixture" if relevant else "Wrong population/topic", "study_type": "unknown", "evidence": [{"span_id": "s1" if quote == QUOTE else "invented-span", "note": "Synthetic extracted finding.", "criteria": [1]}]}


def report(source_id="pubmed:101"):
    return {"claims": [{"section": "summary", "text": "Synthetic report, not a medical finding.", "evidence_ids": [source_id + "#e1"]}]}


class ScriptedModel:
    def __init__(self, decisions, assessments=None, reports=None, plans=None):
        base = {"topic": "Synthetic research", "query": "broad", "start_year": None, "end_year": None,
                "target_sources": 1, "question_type": "general", "criteria": ["Nguồn đề cập câu hỏi nghiên cứu."], "clarification": ""}
        self.queues = {"plan": plans or [base], "decide": decisions, "assess_source": assessments or [], "report": reports or []}
        self.calls = []

    def generate(self, system, packet, schema, timeout):
        fields = schema["properties"]
        purpose = "plan" if "topic" in fields else "decide" if "action" in fields else "assess_source" if "relevant" in fields else "report"
        self.calls.append((purpose, deepcopy(packet)))
        if not self.queues[purpose]:
            raise AssertionError(f"Unexpected extra model call: {purpose}")
        output = self.queues[purpose].pop(0)
        return deepcopy(output), {"input_tokens": 10, "output_tokens": 10}


class FakeSources:
    def __init__(self, queries, documents):
        self.queries, self.documents = queries, documents
        self.calls = []
        self.interrupt_once = set()

    def search_pubmed(self, query, plan, timeout=30):
        self.calls.append(("search_pubmed", query, deepcopy(plan)))
        return self._search(query)

    def search_local(self, query):
        self.calls.append(("search_local", query))
        return self._search(query)

    def _search(self, query):
        result = self.queries[query]
        if isinstance(result, Exception):
            raise result
        return {"candidates": [{"id": sid, "title": self.documents[sid]["title"], "year": self.documents[sid]["year"], "origin": self.documents[sid]["origin"]} for sid in result], "source_mode": "fixture_not_live"}

    def read_source(self, source_id, offline=False, timeout=30):
        self.calls.append(("read_source", source_id))
        if source_id in self.interrupt_once:
            self.interrupt_once.remove(source_id)
            raise KeyboardInterrupt()
        return deepcopy(self.documents[source_id])


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_irrelevant_source_causes_research_replan(self):
        sources = FakeSources({"broad": ["pubmed:101"], "refined": ["pubmed:202"]}, {sid: document(sid) for sid in ["pubmed:101", "pubmed:202"]})
        model = ScriptedModel([
            decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:101"),
            decision("search_pubmed", "refined"), decision("read_source", source_id="pubmed:202"), decision("finish"),
        ], [assessment(False), assessment(True)], [report("pubmed:202")])
        state = Agent(self.store, model, sources).start("Tìm nguồn phù hợp với câu hỏi nghiên cứu.")
        self.assertEqual(state["status"], "completed")
        self.assertFalse(state["documents"]["pubmed:101"]["eligible"])
        self.assertTrue(state["documents"]["pubmed:202"]["eligible"])
        decisions = [packet for purpose, packet in model.calls if purpose == "decide"]
        self.assertFalse(decisions[2]["source_assessments"][0]["eligible"])
        self.assertEqual([c[1] for c in sources.calls if c[0] == "search_pubmed"], ["broad", "refined"])
        self.assertTrue((self.store.run_directory(state["id"]) / "report.md").exists())
        self.assertIn("source_assessed", [event["kind"] for event in self.store.events(state["id"])])

    def test_missing_abstract_is_rejected_before_assessor(self):
        docs = {"pubmed:101": document(content=""), "pubmed:202": document("pubmed:202")}
        sources = FakeSources({"broad": list(docs)}, docs)
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:101"), decision("read_source", source_id="pubmed:202"), decision("finish")], [assessment()], [report("pubmed:202")])
        state = Agent(self.store, model, sources).start("Tìm nguồn phù hợp.")
        self.assertEqual(state["status"], "completed")
        self.assertIn("không có nội dung", state["documents"]["pubmed:101"]["assessment"]["reason"])
        self.assertEqual(sum(kind == "assess_source" for kind, _ in model.calls), 1)

    def test_date_constraint_is_enforced_by_runtime(self):
        docs = {"pubmed:101": document(year=2019), "pubmed:202": document("pubmed:202")}
        sources = FakeSources({"broad": list(docs)}, docs)
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:101"), decision("read_source", source_id="pubmed:202"), decision("finish")], [assessment()], [report("pubmed:202")])
        state = Agent(self.store, model, sources).start("Tìm bài trong 2020–2026.")
        self.assertEqual(state["plan"]["start_year"], 2020)
        self.assertEqual(state["plan"]["end_year"], 2026)
        self.assertFalse(state["documents"]["pubmed:101"]["eligible"])
        self.assertEqual(state["status"], "completed")

    def test_fabricated_quote_cannot_satisfy_success(self):
        sources = FakeSources({"broad": ["pubmed:101"]}, {"pubmed:101": document()})
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:101"), decision("finish"), decision("finish")], [assessment(quote="An invented sentence that does not exist in the source.")])
        state = Agent(self.store, model, sources).start("Tìm một nguồn phù hợp.")
        self.assertEqual(state["status"], "partial")
        self.assertFalse(readiness(state)["ready"])
        self.assertFalse(state["claims"])
        self.assertEqual(state["documents"]["pubmed:101"]["assessment"]["evidence"], [])

    def test_span_ids_resolve_to_literal_source_not_model_paraphrase(self):
        content = "First sentence. " + ("words " * 100) + "Final sentence."
        spans = source_spans(content)
        self.assertEqual(" ".join(spans.values()), normalized(content))
        self.assertTrue(all(piece in normalized(content) and len(piece) <= 360 for piece in spans.values()))

    def test_unknown_source_and_unsafe_action_are_rejected(self):
        sources = FakeSources({"broad": ["pubmed:101"]}, {"pubmed:101": document()})
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:999"), decision("shell", message="read secrets")])
        state = Agent(self.store, model, sources).start("Tìm một nguồn phù hợp.")
        self.assertEqual(state["status"], "partial")
        self.assertFalse(any(c[0] == "read_source" for c in sources.calls))
        self.assertEqual(sum(e["kind"] == "policy_rejection" for e in self.store.events(state["id"])), 2)

    def test_blocked_source_pauses_and_can_resume(self):
        sources = FakeSources({"broad": SourceBlocked("fixture blocked")}, {"pubmed:101": document()})
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:101"), decision("finish")], [assessment()], [report()])
        agent = Agent(self.store, model, sources)
        state = agent.start("Tìm nguồn phù hợp.")
        self.assertEqual(state["status"], "paused_source")
        self.assertFalse(state["claims"])
        run_id = state["id"]
        sources.queries["broad"] = ["pubmed:101"]
        resumed = agent.resume(run_id)
        self.assertEqual(resumed["id"], run_id)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["tool_calls"], 3)

    def test_blocked_pubmed_can_switch_to_local_corpus(self):
        local = document("local:fixture:0")
        self.store.put_document(local)
        sources = FakeSources({"broad": SourceBlocked("fixture blocked"), "local terms": [local["id"]]}, {local["id"]: local})
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("search_local", "local terms"), decision("read_source", source_id=local["id"]), decision("finish")], [assessment()], [report(local["id"])])
        state = Agent(self.store, model, sources).start("Tìm nguồn phù hợp.")
        self.assertEqual(state["status"], "completed")
        packets = [packet for purpose, packet in model.calls if purpose == "decide"]
        self.assertNotIn("search_pubmed", packets[1]["available_actions"])
        self.assertIn("search_local", packets[1]["available_actions"])

    def test_clarification_persists_and_answer_replans_same_run(self):
        sources = FakeSources({"broad": ["pubmed:101"]}, {"pubmed:101": document()})
        first_plan = {"topic": "Synthetic research", "query": "broad", "start_year": None, "end_year": None, "target_sources": 1, "question_type": "general", "criteria": ["Tài liệu phù hợp."], "clarification": ""}
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:101"), decision("finish")], [assessment()], [report()], [deepcopy(first_plan), deepcopy(first_plan)])
        agent = Agent(self.store, model, sources)
        state = agent.start("Tìm nghiên cứu gần đây.")
        self.assertEqual(state["status"], "waiting_for_user")
        self.assertEqual(state["tool_calls"], 0)
        state = agent.resume(state["id"], "2020–2026")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["plan"]["start_year"], 2020)
        self.assertEqual(state["clarifications"], ["2020–2026"])

    def test_restart_resumes_checkpointed_read_without_double_charge(self):
        sources = FakeSources({"broad": ["pubmed:101"]}, {"pubmed:101": document()})
        sources.interrupt_once.add("pubmed:101")
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:101"), decision("finish")], [assessment()], [report()])
        agent = Agent(self.store, model, sources)
        with self.assertRaises(KeyboardInterrupt):
            agent.start("Tìm nguồn phù hợp.")
        run_id = agent.current_run_id
        checkpoint = self.store.load(run_id)
        self.assertIsNotNone(checkpoint["pending_action"])
        self.assertEqual(checkpoint["tool_calls"], 2)
        self.store.close()
        self.store = Store(self.temp.name)
        state = Agent(self.store, model, sources).resume(run_id)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["tool_calls"], 2)
        self.assertEqual(len(state["documents"]), 1)

    def test_budget_produces_partial_report_without_false_completion(self):
        sources = FakeSources({"broad": ["pubmed:101"]}, {"pubmed:101": document()})
        model = ScriptedModel([decision("search_pubmed", "broad")])
        state = Agent(self.store, model, sources, Config(max_steps=1)).start("Tìm nguồn phù hợp.")
        self.assertEqual(state["status"], "partial")
        self.assertEqual(state["steps"], 1)
        self.assertFalse(state["claims"])

    def test_unknown_report_reference_is_not_published(self):
        sources = FakeSources({"broad": ["pubmed:101"]}, {"pubmed:101": document()})
        bad = {"claims": [{"section": "summary", "text": "Fabricated claim", "evidence_ids": ["not-a-real-evidence-id"]}]}
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:101"), decision("finish")], [assessment()], [bad])
        state = Agent(self.store, model, sources).start("Tìm nguồn phù hợp.")
        self.assertEqual(state["status"], "partial")
        text = (self.store.run_directory(state["id"]) / "report.md").read_text()
        self.assertNotIn("Fabricated claim", text)

    def test_repeated_query_stops_without_repeating_network_call(self):
        sources = FakeSources({"broad": []}, {})
        model = ScriptedModel([decision("search_pubmed", "broad")] * 3)
        state = Agent(self.store, model, sources).start("Tìm nguồn phù hợp.")
        self.assertEqual(state["status"], "partial")
        self.assertEqual(len(sources.calls), 1)

    def test_local_ingestion_is_idempotent_and_preserves_snapshot_provenance(self):
        source = Sources(self.store)
        path = Path(self.temp.name) / "records.json"
        path.write_text(json.dumps({"fetched_at": "2020-01-01T00:00:00+00:00", "records": [{"pmid": "123", "title": "Sleep fixture", "abstract": QUOTE, "publication_year": "2024"}]}))
        source.ingest(path)
        source.ingest(path)
        self.assertEqual(self.store.document_count(), 1)
        found = source.search_local("Sleep")
        self.assertEqual(found["candidates"][0]["id"], "pubmed:123")
        doc = source.read_source("pubmed:123", offline=True)
        self.assertEqual(doc["read_mode"], "local_snapshot")
        self.assertEqual(doc["fetched_at"], "2020-01-01T00:00:00+00:00")
        self.assertEqual(doc["provenance"], "user_imported_snapshot")
        self.assertTrue(self.store.has_unread_documents([]))
        self.assertFalse(self.store.has_unread_documents(["pubmed:123"]))

    def test_explicit_narrow_scope_and_exhausted_corpus(self):
        local = document("local:fixture:0")
        self.store.put_document(local)
        sources = FakeSources({"broad": [local["id"]]}, {local["id"]: local})
        excessive = {"topic": "Synthetic research", "query": "broad", "start_year": None, "end_year": None, "target_sources": 1, "question_type": "general", "criteria": ["Requested", "Invented extra requirement"], "clarification": ""}
        model = ScriptedModel([decision("search_local", "broad"), decision("read_source", source_id=local["id"]), decision("finish")], [assessment()], [report(local["id"])], [excessive])
        state = Agent(self.store, model, sources, Config(offline=True)).start("Tóm tắt 1 bài; chỉ cần nêu kết quả trong nguồn.")
        self.assertEqual(state["plan"]["criteria"], ["nêu kết quả trong nguồn"])
        self.assertEqual(state["status"], "completed")
        last_packet = [packet for purpose, packet in model.calls if purpose == "decide"][-1]
        self.assertEqual(last_packet["available_actions"], ["finish"])

    def test_cache_expiry_and_source_block_detection(self):
        self.store.cache_put("key", {"value": 1})
        self.assertIsNotNone(self.store.cache_get("key"))
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self.store.db.execute("UPDATE response_cache SET saved_at=? WHERE key='key'", (old,))
        self.store.db.commit()
        self.assertIsNone(self.store.cache_get("key"))
        response = io.BytesIO(b"<html><title>NCBI - WWW Error Blocked Diagnostic</title></html>")
        response.headers = {"Content-Type": "text/html"}
        with patch("medical_agent.sources.urlopen", return_value=response) as network, self.assertRaises(SourceBlocked):
            Sources(self.store)._request("esearch.fcgi", {}, "json")
        self.assertEqual(network.call_count, 1)

    def test_reimport_replaces_stale_chunks_without_changing_run_snapshots(self):
        path = Path(self.temp.name) / "corpus.txt"
        path.write_text("Obsoleteunique " * 500, encoding="utf-8")
        sources = Sources(self.store)
        sources.ingest(path)
        old_ids = {row["source_id"] for row in self.store.db.execute("SELECT source_id FROM document_search")}
        self.assertGreater(len(old_ids), 1)
        old_id = sorted(old_ids)[0]
        self.store.checkpoint({"id": "deadbeef0011", "documents": {old_id: self.store.get_document(old_id)}}, "snapshot_test")
        path.write_text("Freshunique source content", encoding="utf-8")
        sources.ingest(path)
        self.assertEqual(self.store.document_count(), 1)
        self.assertFalse(sources.search_local("Obsoleteunique")["candidates"])
        self.assertEqual(len(sources.search_local("Freshunique")["candidates"]), 1)
        self.assertTrue(all(self.store.get_document(sid) is None for sid in old_ids))
        self.assertIn("Obsoleteunique", self.store.load("deadbeef0011")["documents"][old_id]["content"])

    def test_human_review_updates_checkpoint_and_report(self):
        from medical_agent.cli import main
        sources = FakeSources({"broad": ["pubmed:101"]}, {"pubmed:101": document()})
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:101"), decision("finish")], [assessment()], [report()])
        state = Agent(self.store, model, sources).start("Tìm một nguồn phù hợp.")
        with patch("sys.stdout", new=io.StringIO()):
            result = main(["--home", self.temp.name, "review", state["id"], "rejected", "--note", "Claim does not match source."])
        self.assertEqual(result, 0)
        reviewed = self.store.load(state["id"])
        self.assertEqual(reviewed["review"]["status"], "rejected")
        self.assertEqual(reviewed["claims"], state["claims"])
        report_text = (self.store.run_directory(state["id"]) / "report.md").read_text()
        self.assertIn("`rejected`", report_text)
        self.assertIn("Claim does not match source.", report_text)

    def test_multiple_chunks_cannot_count_as_multiple_independent_sources(self):
        docs = {}
        for i in range(2):
            sid = f"local:original:{i}"
            doc = document(sid)
            doc.update({"document_id": "local:original", "eligible": True, "assessment": {"evidence": [{"id": f"e{i}", "quote": QUOTE, "criteria": [1]}]}})
            docs[sid] = doc
        state = {"documents": docs, "plan": {"target_sources": 2, "criteria": ["Test criterion"]}}
        self.assertEqual(readiness(state)["usable_sources"], 1)
        self.assertFalse(readiness(state)["ready"])
        claims, errors = validate_claims({"claims": [{"section": "agreement", "text": "Agreement", "evidence_ids": ["e0", "e1"]}]}, state)
        self.assertFalse(claims)
        self.assertTrue(errors)

    def test_clarification_can_reduce_requested_source_count(self):
        sources = FakeSources({"broad": ["pubmed:101"]}, {"pubmed:101": document()})
        base = {"topic": "Synthetic research", "query": "broad", "start_year": None, "end_year": None, "target_sources": 1, "question_type": "general", "criteria": ["Relevant source"], "clarification": ""}
        model = ScriptedModel([decision("search_pubmed", "broad"), decision("read_source", source_id="pubmed:101"), decision("finish")], [assessment()], [report()], [base, base])
        agent = Agent(self.store, model, sources)
        state = agent.start("Tìm 10 bài phù hợp.")
        self.assertEqual(state["status"], "waiting_for_user")
        state = agent.resume(state["id"], "Chỉ dùng 1 bài.")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["plan"]["target_sources"], 1)

    def test_comparison_requires_two_sources_and_cloud_is_not_allowed(self):
        source = document()
        source.update({"eligible": True, "assessment": {"evidence": [{"id": "e1", "quote": QUOTE, "criteria": [1]}]}})
        state = {"documents": {source["id"]: source}}
        claims, errors = validate_claims({"claims": [{"section": "agreement", "text": "Agreement", "evidence_ids": ["e1"]}]}, state)
        self.assertFalse(claims)
        self.assertTrue(errors)
        with self.assertRaises(ValueError):
            Config(model="some-model:cloud").validate()
        with self.assertRaises(ValueError):
            OllamaModel(Config(base_url="https://remote.example.test"))


if __name__ == "__main__":
    unittest.main()
