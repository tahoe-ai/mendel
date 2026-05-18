from __future__ import annotations

from pathlib import Path

from ..core.config import RepoConfig
from .base import CompatVerdict


class GenericPlugin:
    """Reads .patchbot.yml constraints. Always 'unverified' unless the repo
    explicitly opts in with constraints (none modeled in v1)."""

    name = "generic"

    def detect(self, repo_root: Path) -> bool:
        return (repo_root / ".patchbot.yml").exists()

    async def check(
        self,
        repo_root: Path,
        ecosystem: str,
        package: str,
        target_version: str,
    ) -> CompatVerdict:
        cfg = RepoConfig.load(repo_root)
        if not cfg.runtime or cfg.runtime == "generic":
            return CompatVerdict(
                None,
                "no-runtime",
                ".patchbot.yml declares no runtime constraints",
            )
        return CompatVerdict(
            cfg.runtime,
            "unverified",
            f"runtime {cfg.runtime!r} declared in .patchbot.yml — no plugin handles it yet",
        )
