from __future__ import annotations

from pathlib import Path

from .base import UpdateResult, run_cmd


class NpmUpdater:
    name = "npm"
    ecosystem = "npm"

    def detect(self, repo_root: Path) -> Path | None:
        if (repo_root / "package-lock.json").exists():
            return repo_root
        return None

    async def update(self, workdir: Path, package: str, target_version: str) -> UpdateResult:
        rc, out, err = await run_cmd(
            ["npm", "install", f"{package}@{target_version}", "--package-lock-only", "--no-audit", "--no-fund"],
            cwd=workdir,
        )
        if rc != 0:
            return UpdateResult(success=False, changed_files=[], message=f"npm: {err or out}")
        return UpdateResult(
            success=True,
            changed_files=["package.json", "package-lock.json"],
            message=f"npm install {package}@{target_version} --package-lock-only",
        )
