from __future__ import annotations

from pathlib import Path

from .base import UpdateResult, run_cmd


class PnpmUpdater:
    name = "pnpm"
    ecosystem = "npm"

    def detect(self, repo_root: Path) -> Path | None:
        if (repo_root / "pnpm-lock.yaml").exists():
            return repo_root
        return None

    async def update(self, workdir: Path, package: str, target_version: str) -> UpdateResult:
        rc, out, err = await run_cmd(
            ["pnpm", "up", f"{package}@{target_version}", "--lockfile-only"],
            cwd=workdir,
        )
        if rc != 0:
            return UpdateResult(success=False, changed_files=[], message=f"pnpm: {err or out}")
        return UpdateResult(
            success=True,
            changed_files=["package.json", "pnpm-lock.yaml"],
            message=f"pnpm up {package}@{target_version} --lockfile-only",
        )
