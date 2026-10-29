"""Structural/resource metrics. Semantic labels are exclusively human input."""

from .report import evidence_ledger, normalized, readiness, validate_claims


def structural_eval(state):
    ledger = evidence_ledger(state)
    _, claim_errors = validate_claims({"claims": state.get("claims", [])}, state)
    quotes_match = all(e.get("quote") and e["quote"] in normalized(state["documents"][e["source_id"]].get("content", "")) for e in ledger.values())
    return {
        "run_id": state["id"], "status": state["status"], "readiness": readiness(state),
        "checks": {
            "completed_only_when_ready": state["status"] != "completed" or readiness(state)["ready"],
            "completed_has_claims": state["status"] != "completed" or bool(state.get("claims")),
            "claims_reference_existing_evidence": not claim_errors,
            "evidence_has_known_source": all(e["source_id"] in state["documents"] for e in ledger.values()),
            "evidence_matches_source": quotes_match,
            "model_budget_respected": state["model_calls"] <= state["config"]["max_model_calls"],
            "tool_budget_respected": state["tool_calls"] <= state["config"]["max_tool_calls"],
            "steps_budget_respected": state["steps"] <= state["config"]["max_steps"],
            "search_budget_respected": state["searches"] <= state["config"]["max_searches"],
        },
        "semantic_accuracy": "not_independently_measured", "clinical_validation": "not_performed",
    }


def resource_metrics(state, events):
    events = list(events)
    started = sum(e["kind"] == "model_started" for e in events)
    completed = [e["payload"].get("usage", {}) for e in events if e["kind"] == "model_completed"]
    tokens = {}
    for field in ("input_tokens", "output_tokens"):
        values = [usage.get(field) for usage in completed]
        tokens[field] = sum(values) if len(completed) == started and all(type(v) is int and v >= 0 for v in values) else None
    return {"active_seconds": state["elapsed_seconds"], "model_calls": state["model_calls"],
            "tool_calls": state["tool_calls"], "searches": state["searches"], "steps": state["steps"],
            "sources_read": len(state["documents"]), "usable_sources": readiness(state)["usable_sources"],
            "source_errors": len(state.get("errors", [])), **tokens}
