from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CompatVerdict:
    runtime_id: str | None  # e.g. "python312" — None when no runtime constraint applies
    verdict: str  # "compatible" | "incompatible" | "unverified" | "no-runtime"
    detail: str


class RuntimePlugin(Protocol):
    name: str

    def detect(self, repo_root: Path) -> bool: ...

    async def check(
        self,
        repo_root: Path,
        ecosystem: str,
        package: str,
        target_version: str,
    ) -> CompatVerdict: ...
