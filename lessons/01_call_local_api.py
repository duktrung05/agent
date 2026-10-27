"""Bài 1: gửi một HTTP request tới model local qua Ollama.

Chỉ dùng thư viện chuẩn Python. Chạy với --dry-run để xem request mà không gọi model.
"""

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


ENDPOINT = "http://127.0.0.1:11434/api/chat"


def call_model(payload: dict, timeout: float) -> dict:
    # Python dict -> JSON text -> bytes: đây là body thực sự gửi qua HTTP.
    request = Request(
        ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Request loopback đi thẳng tới Ollama, không qua proxy của môi trường.
    opener = build_opener(ProxyHandler({}))
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        with exc:
            detail = exc.read(2000).decode("utf-8", errors="replace")
        hint = " Kiểm tra tên model và ollama list." if exc.code == 404 else ""
        raise RuntimeError(f"HTTP {exc.code}: {detail}.{hint}") from exc
    except TimeoutError as exc:
        raise RuntimeError(
            "Hết thời gian chờ. Kiểm tra tải máy, log Ollama và giới hạn output. "
            "Client timeout không bảo đảm server đã dừng sinh token."
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Không gọi được Ollama: {exc.reason}. "
            "Kiểm tra server tại 127.0.0.1:11434 và quyền kết nối local."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Lỗi kết nối/đọc response local: {exc}") from exc

    elapsed = time.perf_counter() - started
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "Response không phải một JSON hợp lệ. Kiểm tra endpoint và stream=false."
        ) from exc

    if not isinstance(result, dict):
        raise RuntimeError("Response phải là một JSON object.")
    if result.get("error"):
        raise RuntimeError(f"Ollama báo lỗi: {result['error']}")
    if result.get("done") is not True:
        raise RuntimeError("Response chưa hoàn tất: done không bằng true.")
    message = result.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(
            "Response thiếu message.content hoặc nội dung rỗng; không coi là thành công."
        )

    truncated = result.get("done_reason") == "length"
    return {
        "status": "truncated" if truncated else "completed",
        "model": result.get("model"),
        "answer": content,
        "done_reason": result.get("done_reason"),
        "input_tokens": result.get("prompt_eval_count"),
        "output_tokens": result.get("eval_count"),
        "elapsed_seconds": round(elapsed, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument(
        "--prompt", default="Giải thích HTTP request và HTTP response trong hai câu ngắn."
    )
    parser.add_argument("--max-output", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.model.strip() or not args.prompt.strip():
        parser.error("model và prompt không được rỗng")
    if not 1 <= args.max_output <= 512:
        parser.error("--max-output phải từ 1 đến 512 token trong bài thực hành này")
    if not 0 < args.timeout <= 600:
        parser.error("--timeout phải lớn hơn 0 và không quá 600 giây")

    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "Trả lời bằng tiếng Việt, ngắn gọn và rõ ràng."},
            {"role": "user", "content": args.prompt},
        ],
        "stream": False,
        "think": False,
        "keep_alive": 0,
        "options": {"num_ctx": 2048, "num_predict": args.max_output},
    }
    if args.dry_run:
        print(json.dumps({"endpoint": ENDPOINT, "payload": payload}, ensure_ascii=False, indent=2))
        return 0
    try:
        result = call_model(payload, args.timeout)
    except RuntimeError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["status"] == "truncated" else 0


if __name__ == "__main__":
    sys.exit(main())
