import argparse
import json
import sqlite3
import sys

from .core import AgentError, Config
from .engine import Agent
from .model import OllamaModel
from .report import readiness
from .metrics import structural_eval
from .sources import Sources
from .store import Store


def display_event(kind, payload):
    if kind == "run_created":
        print("\n[MỤC TIÊU]", payload["goal"], flush=True)
    elif kind == "model_started":
        print(f"[MODEL] {payload['purpose']}...", flush=True)
    elif kind == "plan_created":
        plan = payload["plan"]
        print(f"[KẾ HOẠCH] {plan['topic']} · cần {plan['target_sources']} nguồn", flush=True)
        for i, item in enumerate(plan["criteria"], 1):
            print(f"  {i}. {item}", flush=True)
    elif kind == "decision":
        action = payload.get("action")
        arguments = payload.get("query") if str(action).startswith("search_") else payload.get("source_ids") if action == "read_source" else ""
        print(f"[CHỌN] {action} {arguments or ''}", flush=True)
    elif kind == "tool_completed":
        result = payload["result"]
        if payload["action"].startswith("search_"):
            print(f"[TOOL] {len(result['candidates'])} ứng viên · {result.get('source_mode')}", flush=True)
        else:
            print(f"[ĐÃ ĐỌC] {result['source_id']} · {result.get('title')}", flush=True)
    elif kind == "source_assessed":
        print(f"[{'GIỮ' if payload['eligible'] else 'LOẠI'}] {payload['source_id']} · {payload['reason']}", flush=True)
    elif kind in {"tool_error", "source_blocked", "policy_rejection", "run_error"}:
        print(f"[{kind.upper()}] {payload.get('error')}", flush=True)
    elif kind == "waiting_for_user":
        print("[CẦN BỔ SUNG]", payload["question"], flush=True)


def show_result(store, state):
    ready = readiness(state)
    print(f"\nRun {state['id']} · {state['status']} · nguồn dùng được {ready['usable_sources']}/{ready['target_sources']}")
    print("Báo cáo:", store.run_directory(state["id"]) / "report.md")
    print("Trace:  ", store.run_directory(state["id"]) / "trace.jsonl")
    print("Review:", state.get("review", {}).get("status", "pending"))
    if state.get("review", {}).get("note"):
        print("Ghi chú:", state["review"]["note"])
    if state.get("claims"):
        print("Tổng hợp do model tạo, cần đối chiếu nguồn:")
        for item in state["claims"]:
            print("-", item["text"])
    elif state.get("stop_reason"):
        print(state["stop_reason"])
    if state["status"] == "waiting_for_user":
        print("Câu hỏi:", state["question"])


def parser():
    result = argparse.ArgumentParser(description="Agent nghiên cứu local: mục tiêu → quyết định → tool → đánh giá → quyết định tiếp.")
    result.add_argument("--home", default="outputs", help="Thư mục SQLite, corpus và các run.")
    result.add_argument("--model", default="qwen3:1.7b")
    result.add_argument("--question", help="Chạy một mục tiêu rồi thoát.")
    result.add_argument("--resume", metavar="RUN_ID")
    result.add_argument("--answer", help="Bổ sung phạm vi cho run đang chờ, dùng cùng --resume.")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", default=None, help="Chỉ dùng corpus/snapshot local; model vẫn chạy Ollama local.")
    mode.add_argument("--online", dest="offline", action="store_false")
    result.add_argument("--max-steps", type=int, default=10)
    result.add_argument("--max-seconds", type=int, default=600)
    result.add_argument("--max-sources", type=int, default=6)
    result.add_argument("--max-model-calls", type=int, default=16)
    result.add_argument("--max-tool-calls", type=int, default=8)
    result.add_argument("--max-searches", type=int, default=3)
    result.add_argument("--context", type=int, default=8192)
    result.add_argument("--quiet", action="store_true")
    result.add_argument("--strategy", choices=["agent", "fixed"], default="agent", help="Agent điều chỉnh hoặc baseline một query/top-k.")
    result.add_argument("--top-k", type=int, default=5, help="Số ứng viên baseline đọc, tối đa 5.")
    commands = result.add_subparsers(dest="command")
    commands.add_parser("doctor", help="Kiểm tra Ollama và kho local.")
    ingest = commands.add_parser("ingest", help="Nạp txt/md/json/jsonl hoặc JSON output của bài 4.")
    ingest.add_argument("path")
    commands.add_parser("runs", help="Liệt kê các run gần nhất.")
    for command in ("show", "trace", "eval", "rebuild"):
        sub = commands.add_parser(command)
        sub.add_argument("run_id")
    review = commands.add_parser("review", help="Ghi nhận quyết định review của người dùng, không phải chứng nhận y khoa.")
    review.add_argument("run_id")
    review.add_argument("decision", choices=["approved", "rejected"])
    review.add_argument("--note", default="", help="Nhận xét khi đối chiếu kết quả với nguồn.")
    review.add_argument("--reviewer", default="local_user", help="Tên/nhãn người hoặc công cụ review; không xác thực danh tính.")
    review.add_argument("--kind", choices=["human", "assisted"], default="human", help="Phân biệt review của người với kiểm tra hỗ trợ tự động.")
    for name in ("backup", "restore", "manifest"):
        sub = commands.add_parser(name)
        sub.add_argument("path", help="Thư mục backup/restore hoặc file corpus cần đóng băng manifest.")
    collect = commands.add_parser("collect", help="Lấy snapshot PubMed thật và tạo manifest, chưa gán nhãn y khoa.")
    collect.add_argument("--query", action="append", required=True)
    collect.add_argument("--limit", type=int, default=20, help="Số kết quả mỗi query, 1–100.")
    collect.add_argument("--output", required=True)
    collect.add_argument("--start-year", type=int)
    collect.add_argument("--end-year", type=int)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    agent = None
    try:
        if args.answer and not args.resume:
            raise ValueError("--answer cần đi cùng --resume RUN_ID.")
        if args.command == "restore":
            from .operations import restore_store
            print(json.dumps(restore_store(args.path, args.home), ensure_ascii=False, indent=2))
            return 0
        if args.command == "manifest":
            from .corpus import freeze_manifest
            print(json.dumps(freeze_manifest(args.path), ensure_ascii=False, indent=2))
            return 0
        config = Config(model=args.model, offline=bool(args.offline), max_steps=args.max_steps, max_seconds=args.max_seconds, max_sources=args.max_sources, max_model_calls=args.max_model_calls, max_tool_calls=args.max_tool_calls, max_searches=args.max_searches, num_ctx=args.context, strategy=args.strategy, top_k=args.top_k).validate()
        store = Store(args.home)
        sources = Sources(store)
        if args.command == "backup":
            from .operations import backup_store
            print(json.dumps(backup_store(store, args.path), ensure_ascii=False, indent=2))
            return 0
        if args.command == "collect":
            from .corpus import collect_pubmed, freeze_manifest
            if args.offline:
                raise ValueError("collect cần online; không dùng --offline.")
            if (args.start_year is not None and not 1800 <= args.start_year <= 2100) or (args.end_year is not None and not 1800 <= args.end_year <= 2100) or (args.start_year and args.end_year and args.start_year > args.end_year):
                raise ValueError("Khoảng năm thu thập không hợp lệ.")
            payload = collect_pubmed(sources, args.query, args.output, args.limit, args.start_year, args.end_year)
            manifest = freeze_manifest(args.output)
            print(json.dumps({"path": args.output, "records": len(payload["records"]), "status": payload["status"], "sha256": manifest["sha256"]}, ensure_ascii=False, indent=2))
            return 0 if not payload["errors"] else 2
        if args.command == "ingest":
            print(f"Đã nạp {sources.ingest(args.path)} tài liệu/chunk. Tổng có nội dung: {store.document_count()}.")
            return 0
        if args.command == "runs":
            for state in store.recent():
                print(state["id"], state["status"], state["goal"][:100])
            return 0
        if args.command in {"show", "trace", "eval", "review", "rebuild"}:
            state = store.load(args.run_id)
            if args.command == "trace":
                for event in store.events(state["id"]):
                    print(json.dumps(event, ensure_ascii=False))
            elif args.command == "eval":
                print(json.dumps(structural_eval(state), ensure_ascii=False, indent=2))
            elif args.command == "rebuild":
                store.export(state)
                store.write_report(state)
                print("Đã xuất lại report/state/trace từ SQLite:", store.run_directory(state["id"]))
            elif args.command == "review":
                if not args.reviewer.strip():
                    raise ValueError("Cần tên/nhãn reviewer.")
                state["review"] = {"status": args.decision, "reviewer": args.reviewer.strip(), "kind": args.kind, "note": args.note}
                store.checkpoint(state, "human_review", state["review"])
                store.write_report(state)
                print("Đã ghi nhận review:", args.decision)
            else:
                show_result(store, state)
            return 0
        model = OllamaModel(config)
        if args.command == "doctor":
            data = model.health()
            print(json.dumps({"local_models": [m.get("name") for m in data.get("models", [])], "selected_model": config.model, "indexed_sources": store.document_count(), "database": str(store.directory / "agent.sqlite3"), "offline": config.offline}, ensure_ascii=False, indent=2))
            return 0
        agent = Agent(store, model, sources, config, on_event=None if args.quiet else display_event)
        if args.resume:
            state = agent.resume(args.resume, args.answer, offline=args.offline)
            show_result(store, state)
            return 0 if state["status"] == "completed" else 2
        if args.question:
            state = agent.start(args.question)
            show_result(store, state)
            return 0 if state["status"] == "completed" else 2
        print("Medical Research Agent · model local · /runs, /resume ID, /bye")
        print("Nhập mục tiêu nghiên cứu; nếu cần làm rõ, agent sẽ hỏi trong cùng run.")
        while True:
            goal = input("\nMục tiêu> ").strip()
            if goal == "/bye":
                return 0
            if goal == "/runs":
                for state in store.recent(5):
                    print(state["id"], state["status"], state["goal"][:90])
                continue
            if not goal:
                continue
            try:
                state = agent.resume(goal.split(maxsplit=1)[1]) if goal.startswith("/resume ") else agent.start(goal)
                while state["status"] == "waiting_for_user":
                    answer = input(f"Bổ sung ({state['question']})> ").strip()
                    if answer in {"/bye", "/skip"}:
                        break
                    if answer:
                        state = agent.resume(state["id"], answer)
                show_result(store, state)
            except (AgentError, ValueError) as exc:
                print("Lỗi:", exc, file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        if store and agent and getattr(agent, "current_run_id", None):
            state = store.load(agent.current_run_id)
            if state["status"] in {"planning", "running"}:
                state["status"] = "interrupted"
                store.checkpoint(state, "run_interrupted")
                print(f"\nĐã lưu. Chạy tiếp: python3 -m medical_agent --resume {state['id']}")
        print("Đã thoát.")
        return 130
    except (AgentError, ValueError, OSError, sqlite3.Error) as exc:
        print("Lỗi:", exc, file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
