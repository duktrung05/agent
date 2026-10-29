import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from .core import AgentError


class OllamaModel:
    def __init__(self, config):
        self.config = config
        parsed = urlparse(config.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
            raise ValueError("Bản này chỉ cho phép Ollama HTTP trên loopback, không tự gọi cloud.")
        self.base_url = config.base_url.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))

    def health(self):
        try:
            with self.opener.open(self.base_url + "/api/tags", timeout=5) as response:
                data = json.load(response)
        except (OSError, URLError, ValueError) as exc:
            raise AgentError(f"Không đọc được Ollama local: {exc}") from exc
        return data

    def generate(self, system, packet, schema, timeout):
        payload = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(packet, ensure_ascii=False)}],
            "format": schema, "stream": False, "keep_alive": "2m",
            "options": {"num_ctx": self.config.num_ctx, "num_predict": self.config.max_output, "temperature": 0},
        }
        if self.config.model.startswith("qwen3"):
            payload["think"] = False
        request = Request(self.base_url + "/api/chat", data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=timeout) as response:
                data = json.load(response)
        except HTTPError as exc:
            with exc:
                detail = exc.read(800).decode("utf-8", errors="replace")
            raise AgentError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except (OSError, URLError, ValueError) as exc:
            raise AgentError(f"Lỗi Ollama local: {exc}") from exc
        if not isinstance(data, dict) or data.get("error") or data.get("done") is not True:
            raise AgentError("Ollama trả lỗi hoặc response chưa hoàn tất.")
        if data.get("done_reason") == "length":
            raise AgentError("Output model bị cắt; không thực thi quyết định chưa hoàn chỉnh.")
        try:
            result = json.loads(data["message"]["content"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentError("Model không trả JSON theo hợp đồng.") from exc
        if not isinstance(result, dict):
            raise AgentError("Kết quả model phải là JSON object.")
        usage = {"input_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count"), "done_reason": data.get("done_reason")}
        return result, usage
