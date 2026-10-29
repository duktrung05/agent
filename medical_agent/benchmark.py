"""Offline A/B experiments and human-review scoring, with no gold labels in model context.

python3 -m medical_agent.benchmark run --help
python3 -m medical_agent.benchmark score --help
"""

import argparse
import csv
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import sqlite3
import statistics
import sys
import time

from .core import AgentError, Config, utc_now
from .corpus import file_sha256, verify_manifest, write_json_new
from .engine import Agent
from .metrics import resource_metrics, structural_eval
from .model import OllamaModel
from .sources import Sources
from .store import Store


LABELS = {"supported", "partial", "unsupported", "uncertain"}


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def code_hash():
    files = sorted(Path(__file__).parent.glob("*.py"))
    return text_hash("\n".join(f"{p.name}:{file_sha256(p)}" for p in files))


def load_cases(path, split="dev", case_ids=None):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Bộ đánh giá cần danh sách cases không rỗng.")
    seen, groups, references = set(), {}, {"dev": set(), "test": set()}
    for case in cases:
        if not isinstance(case, dict) or type(case.get("references_reviewed", False)) is not bool:
            raise ValueError("Mỗi case phải là object, references_reviewed phải là boolean.")
        identifier = case.get("id", "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identifier) or identifier in seen:
            raise ValueError("ID câu hỏi trùng hoặc không an toàn cho đường dẫn.")
        seen.add(identifier)
        if case.get("split") not in {"dev", "test"} or not isinstance(case.get("question"), str) or not 1 <= len(case["question"].strip()) <= 4000:
            raise ValueError(f"Câu hỏi/split không hợp lệ: {identifier}.")
        group = case.get("group", identifier)
        if not isinstance(group, str) or (group in groups and groups[group] != case["split"]):
            raise ValueError("Nhóm câu hỏi gần trùng xuất hiện ở cả dev và test.")
        groups[group] = case["split"]
        for field in ("clarifications", "required_outputs", "reference_source_ids"):
            values = case.get(field, [])
            if not isinstance(values, list) or not all(isinstance(x, str) and x.strip() for x in values):
                raise ValueError(f"{field} phải là danh sách chuỗi: {identifier}.")
        if case.get("references_reviewed"):
            references[case["split"]].update(case.get("reference_source_ids", []))
    if references["dev"] & references["test"]:
        raise ValueError("Nguồn tham chiếu đã gán nhãn giao nhau giữa dev/test; rà và nhóm lại câu hỏi trước khi đo.")
    if case_ids and not set(case_ids).issubset(seen):
        raise ValueError("Có case ID không tồn tại.")
    selected = [c for c in cases if (split == "all" or c["split"] == split) and (not case_ids or c["id"] in case_ids)]
    if not selected:
        raise ValueError("Không có câu hỏi phù hợp bộ lọc.")
    return selected


def review_template(record, state, case):
    return {
        "run_id": record["run_id"], "case_id": case["id"], "variant": record["variant"],
        "question": case["question"], "reviewer": "", "reviewed_at": None,
        "content_complete": None,
        "criteria": [{"id": f"criterion-{i}", "description": text, "label": None} for i, text in enumerate(case.get("required_outputs", []), 1)],
        "claims": [{"id": f"claim-{i}", "text": claim["text"], "text_sha256": text_hash(claim["text"]),
                    "evidence_ids": claim["evidence_ids"], "important": True, "label": None, "note": ""}
                   for i, claim in enumerate(state.get("claims", []), 1)],
        "note": "Đọc report và nguồn; không điền nhãn chỉ theo trạng thái completed.",
    }


def score_review(record, review):
    empty = {"content_complete": None, "claim_support": None, "evidence_coverage": None,
             "important_claims": len(record["claims"]), "reviewed_claims": 0, "supported_claims": 0}
    if review is None:
        return empty
    if review.get("run_id") != record["run_id"] or review.get("case_id") != record["case_id"]:
        raise ValueError("Nhãn review không khớp run/câu hỏi.")
    expected = {c["id"]: c for c in record["claims"]}
    claims = review.get("claims", [])
    if len(claims) != len(expected) or {c.get("id") for c in claims} != set(expected):
        raise ValueError("Review phải bao gồm đúng danh sách claim đã xuất, không được bỏ claim sai.")
    for claim in claims:
        actual = expected[claim["id"]]
        if claim.get("text_sha256") != actual["text_sha256"] or text_hash(claim.get("text", "")) != actual["text_sha256"]:
            raise ValueError("Nội dung claim đã thay đổi sau khi chạy; review bị từ chối.")
        if claim.get("label") not in LABELS | {None} or type(claim.get("important")) is not bool:
            raise ValueError("Nhãn claim hoặc important không hợp lệ.")
    criteria = review.get("criteria", [])
    if {c.get("id") for c in criteria} != set(record["required_criteria"]) or len(criteria) != len(record["required_criteria"]):
        raise ValueError("Review thiếu hoặc trùng tiêu chí đầu ra.")
    if any(c.get("label") not in {None, "covered", "missing", "uncertain"} for c in criteria):
        raise ValueError("Nhãn tiêu chí không hợp lệ.")
    complete = review.get("content_complete")
    if complete is not None and type(complete) is not bool:
        raise ValueError("content_complete cần true/false/null.")
    has_labels = complete is not None or any(c.get("label") for c in claims + criteria)
    if has_labels and not str(review.get("reviewer", "")).strip():
        raise ValueError("Review đã chấm cần ghi người chấm.")
    important = [c for c in claims if c["important"]]
    reviewed = sum(c.get("label") in LABELS for c in important)
    support = sum(c.get("label") == "supported" for c in important) / len(important) if important and reviewed == len(important) else None
    coverage = sum(c.get("label") == "covered" for c in criteria) / len(criteria) if criteria and all(c.get("label") is not None for c in criteria) else None
    if complete is True and (coverage != 1 or not important or support is None):
        raise ValueError("Không thể chấm hoàn thành khi chưa phủ tiêu chí hoặc chưa chấm claim quan trọng.")
    return {"content_complete": complete, "claim_support": support, "evidence_coverage": coverage,
            "important_claims": len(important), "reviewed_claims": reviewed,
            "supported_claims": sum(c.get("label") == "supported" for c in important)}


def retrieval_metrics(state, case):
    if not case.get("references_reviewed") or not case.get("reference_source_ids"):
        return {"precision_at_5": None, "recall_at_5": None, "candidate_recall": None}
    relevant = set(case["reference_source_ids"])
    first = list(dict.fromkeys(state.get("search_history", [{}])[0].get("candidate_ids", [])))[:5] if state.get("search_history") else []
    union = {sid for search in state.get("search_history", []) for sid in search.get("candidate_ids", [])}
    hits = len(set(first) & relevant)
    return {"precision_at_5": hits / len(first) if first else 0.0,
            "recall_at_5": hits / len(relevant), "candidate_recall": len(union & relevant) / len(relevant)}


def summarize(records, reviews=None):
    reviews = reviews or []
    ids = [r.get("run_id") for r in reviews]
    if len(ids) != len(set(ids)) or not set(ids).issubset({r["run_id"] for r in records}):
        raise ValueError("Review trùng run hoặc tham chiếu run không có trong thí nghiệm.")
    by_run = {r["run_id"]: r for r in reviews}
    rows = []
    for variant in sorted({r["variant"] for r in records}):
        batch = [r for r in records if r["variant"] == variant]
        scores = [score_review(r, by_run.get(r["run_id"])) for r in batch]
        latencies = sorted(r["wall_seconds"] for r in batch)
        completed = [s["content_complete"] for s in scores if s["content_complete"] is not None]
        supports = [s["claim_support"] for s in scores if s["claim_support"] is not None]
        coverages = [s["evidence_coverage"] for s in scores if s["evidence_coverage"] is not None]
        scored_claims = [s for s in scores if s["claim_support"] is not None]
        denominator = sum(s["important_claims"] for s in scored_claims)
        numerator = sum(s["supported_claims"] for s in scored_claims)
        row = {"variant": variant, "runs": len(batch), "questions": len({r["case_id"] for r in batch}),
               "runtime_completed": sum(r["status"] == "completed" for r in batch),
               "partial_runs": sum(r["status"] == "partial" for r in batch),
               "failed_runs": sum(r["status"] == "failed" for r in batch),
               "waiting_runs": sum(r["status"] == "waiting_for_user" for r in batch),
               "structural_pass": sum(all(r["structural"]["checks"].values()) for r in batch),
               "content_reviewed": len(completed), "content_completed": sum(completed),
               "content_completion_rate": sum(completed) / len(completed) if completed else None,
               "support_reviewed_runs": len(supports), "mean_claim_support": statistics.mean(supports) if supports else None,
               "supported_claims": numerator, "scored_important_claims": denominator,
               "claim_support": numerator / denominator if denominator else None,
               "coverage_reviewed_runs": len(coverages), "mean_evidence_coverage": statistics.mean(coverages) if coverages else None,
               "median_wall_seconds": statistics.median(latencies), "p95_wall_seconds": latencies[math.ceil(len(latencies) * .95) - 1],
               "mean_model_calls": statistics.mean(r["resources"]["model_calls"] for r in batch),
               "mean_tool_calls": statistics.mean(r["resources"]["tool_calls"] for r in batch)}
        rows.append(row)
    return rows


def write_summary(path, records, reviews=None):
    rows = summarize(records, reviews)
    if not rows:
        return
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_suite(cases, corpus, output, config, variants=("fixed", "agent"), repeats=1, model_factory=OllamaModel, cases_path=None):
    if not config.offline or repeats < 1 or repeats > 10 or not variants or len(set(variants)) != len(variants) or set(variants) - {"fixed", "agent"}:
        raise ValueError("Benchmark chỉ offline; variants agent/fixed duy nhất; repeats từ 1 đến 10.")
    manifest = verify_manifest(corpus)
    if not cases or any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", case.get("id", "")) for case in cases):
        raise ValueError("Danh sách câu hỏi rỗng hoặc ID không an toàn.")
    known_ids = {doc["id"] for doc in manifest.get("documents", [])}
    if any(set(case.get("reference_source_ids", [])) - known_ids for case in cases if case.get("references_reviewed")):
        raise ValueError("Nguồn tham chiếu đã gán nhãn không có trong corpus đóng băng.")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    experiment = {"schema_version": "1.0", "started_at": utc_now(), "status": "running", "code_sha256": code_hash(),
                  "cases_sha256": file_sha256(cases_path) if cases_path else text_hash(json.dumps(cases, sort_keys=True, ensure_ascii=False)),
                  "corpus_sha256": manifest["sha256"], "model": config.model, "config": config.to_dict(),
                  "variants": list(variants), "planned_runs": len(cases) * repeats * len(variants),
                  "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                  "platform": platform.platform(), "processor": platform.processor(),
                  "semantic_accuracy": "requires_human_review", "clinical_validation": "not_performed"}
    write_json_new(output / "experiment.json", experiment)
    records, templates = [], []
    try:
        with (output / "results.jsonl").open("x", encoding="utf-8") as results:
            for repeat in range(repeats):
                # Alternate ordering to expose/reduce systematic cold-start advantage.
                order = tuple(variants) if repeat % 2 == 0 else tuple(reversed(variants))
                for case in cases:
                    for variant in order:
                        home = output / variant / case["id"] / str(repeat + 1)
                        store = Store(home)
                        try:
                            sources = Sources(store)
                            sources.ingest(corpus)
                            run_config = replace(config, strategy=variant).validate()
                            model = model_factory(run_config)
                            if isinstance(model, OllamaModel) and "model_snapshot" not in experiment:
                                tags = model.health().get("models", [])
                                experiment["model_snapshot"] = next((tag for tag in tags if tag.get("name") in {config.model, config.model + ":latest"}), {"name": config.model, "digest": None})
                            agent = Agent(store, model, sources, run_config)
                            started = time.monotonic()
                            try:
                                state = agent.start(case["question"])
                                for answer in case.get("clarifications", []):
                                    if state["status"] != "waiting_for_user":
                                        break
                                    state = agent.resume(state["id"], answer)
                            except KeyboardInterrupt:
                                if getattr(agent, "current_run_id", None):
                                    interrupted = store.load(agent.current_run_id)
                                    interrupted["status"] = "interrupted"
                                    store.checkpoint(interrupted, "run_interrupted")
                                    store.write_report(interrupted)
                                raise
                            row = {"case_id": case["id"], "split": case["split"], "topic": case.get("topic"), "variant": variant,
                                   "repeat": repeat + 1, "run_id": state["id"], "home": str(home),
                                   "status": state["status"], "stop_reason": state.get("stop_reason"),
                                   "wall_seconds": round(time.monotonic() - started, 3),
                                   "structural": structural_eval(state), "resources": resource_metrics(state, store.events(state["id"])),
                                   "retrieval": retrieval_metrics(state, case), "corpus_sha256": manifest["sha256"],
                                   "code_sha256": experiment["code_sha256"], "config": state["config"]}
                            template = review_template(row, state, case)
                            row["claims"] = [{"id": c["id"], "text_sha256": c["text_sha256"]} for c in template["claims"]]
                            row["required_criteria"] = [c["id"] for c in template["criteria"]]
                            results.write(json.dumps(row, ensure_ascii=False) + "\n")
                            results.flush()
                            records.append(row)
                            templates.append(template)
                            print(f"{case['id']} {variant} {state['status']} {row['wall_seconds']:.1f}s", flush=True)
                        finally:
                            store.close()
        experiment["status"] = "finished_requires_review"
    except BaseException:
        experiment["status"] = "interrupted_or_failed"
        raise
    finally:
        experiment["finished_at"] = utc_now()
        experiment["working_tree_changed_during_run"] = experiment["code_sha256"] != code_hash()
        experiment["recorded_runs"] = len(records)
        (output / "experiment.json").write_text(json.dumps(experiment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_json_new(output / "reviews.template.json", {"status": "pending_human_review", "reviews": templates})
        write_summary(output / "summary.csv", records)
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chạy A/B offline và chấm review thủ công; không tự chấm tính đúng đắn y khoa.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--cases", default="evals/questions.json")
    run.add_argument("--corpus", required=True)
    run.add_argument("--output", required=True, help="Thư mục thí nghiệm mới, chưa tồn tại.")
    run.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    run.add_argument("--case", action="append")
    run.add_argument("--variants", nargs="+", choices=["fixed", "agent"], default=["fixed", "agent"])
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--model", default="qwen3:1.7b")
    run.add_argument("--context", type=int, default=8192)
    run.add_argument("--max-seconds", type=int, default=600)
    run.add_argument("--max-sources", type=int, default=6)
    run.add_argument("--max-model-calls", type=int, default=16)
    run.add_argument("--max-tool-calls", type=int, default=8)
    run.add_argument("--max-steps", type=int, default=10)
    run.add_argument("--top-k", type=int, default=5)
    score = sub.add_parser("score")
    score.add_argument("--results", required=True)
    score.add_argument("--reviews", required=True)
    score.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "score":
            records = [json.loads(line) for line in Path(args.results).read_text(encoding="utf-8").splitlines() if line.strip()]
            reviews = json.loads(Path(args.reviews).read_text(encoding="utf-8"))["reviews"]
            write_summary(args.output, records, reviews)
            print(json.dumps(summarize(records, reviews), ensure_ascii=False, indent=2))
        else:
            cases = load_cases(args.cases, args.split, args.case)
            config = Config(offline=True, model=args.model, num_ctx=args.context, max_seconds=args.max_seconds,
                            max_sources=args.max_sources, max_model_calls=args.max_model_calls,
                            max_tool_calls=args.max_tool_calls, max_steps=args.max_steps, top_k=args.top_k).validate()
            run_suite(cases, args.corpus, args.output, config, args.variants, args.repeats, cases_path=args.cases)
        return 0
    except KeyboardInterrupt:
        print("Đã ngắt benchmark; các run đã hoàn tất và checkpoint được giữ lại.", file=sys.stderr)
        return 130
    except (AgentError, ValueError, OSError, sqlite3.Error, KeyError) as exc:
        print("Lỗi benchmark:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
