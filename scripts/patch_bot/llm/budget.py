from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    """Raised when the per-run token ceiling has been hit."""


@dataclass
class TokenBudget:
    input_cap: int
    output_cap: int
    input_used: int = 0
    output_used: int = 0
    cache_read: int = 0
    cache_write: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)

    def check(self) -> None:
        if self.input_used > self.input_cap or self.output_used > self.output_cap:
            raise BudgetExceeded(
                f"input={self.input_used}/{self.input_cap} "
                f"output={self.output_used}/{self.output_cap}"
            )

    def record(self, model: str, usage: object) -> None:
        # `usage` is the anthropic Usage Pydantic model; access fields permissively.
        in_t = getattr(usage, "input_tokens", 0) or 0
        out_t = getattr(usage, "output_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.input_used += in_t
        self.output_used += out_t
        self.cache_read += cr
        self.cache_write += cw
        slot = self.by_model.setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        slot["input"] += in_t
        slot["output"] += out_t
        slot["cache_read"] += cr
        slot["cache_write"] += cw

    def summary(self) -> dict:
        return {
            "input_used": self.input_used,
            "output_used": self.output_used,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "by_model": self.by_model,
        }
