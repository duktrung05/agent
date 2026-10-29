from dataclasses import asdict, dataclass
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class AgentError(RuntimeError):
    pass


class SourceBlocked(AgentError):
    pass


class BudgetExceeded(AgentError):
    pass


@dataclass
class Config:
    model: str = "qwen3:1.7b"
    base_url: str = "http://127.0.0.1:11434"
    offline: bool = False
    max_steps: int = 10
    max_model_calls: int = 16
    max_tool_calls: int = 8
    max_sources: int = 6
    max_searches: int = 3
    max_seconds: int = 600
    num_ctx: int = 8192
    max_output: int = 700
    request_timeout: int = 180
    strategy: str = "agent"
    top_k: int = 5

    def validate(self):
        for name in ("max_steps", "max_model_calls", "max_tool_calls", "max_sources", "max_searches", "max_seconds", "num_ctx", "max_output", "request_timeout", "top_k"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} phải là số nguyên dương.")
        if self.max_sources > 20 or self.max_steps > 50 or self.max_model_calls > 100:
            raise ValueError("Cấu hình vượt giới hạn của bản local (20 nguồn, 50 bước, 100 model calls).")
        if not self.model.strip():
            raise ValueError("Tên model không được rỗng.")
        if self.strategy not in {"agent", "fixed"} or self.top_k > 5:
            raise ValueError("strategy phải là agent/fixed; top_k từ 1 đến 5 (giới hạn truy xuất hiện tại).")
        if ":cloud" in self.model.lower() or self.model.lower().endswith("-cloud"):
            raise ValueError("Bản local không cho dùng model tag cloud hoặc tự phát sinh phí API.")
        return self

    def to_dict(self):
        return asdict(self)
