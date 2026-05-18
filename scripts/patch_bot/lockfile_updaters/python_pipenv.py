from __future__ import annotations

from pathlib import Path

from .base import UpdateResult, run_cmd


class PipenvUpdater:
    name = "pipenv"
    ecosystem = "pip"

    def detect(self, repo_root: Path) -> Path | None:
        if (repo_root / "Pipfile.lock").exists():
            return repo_root
        return None

    async def update(self, workdir: Path, package: str, target_version: str) -> UpdateResult:
        rc, out, err = await run_cmd(
            ["pipenv", "update", f"{package}~={target_version}"],
            cwd=workdir,
            timeout=900.0,
        )
        if rc != 0:
            return UpdateResult(success=False, changed_files=[], message=f"pipenv: {err or out}")
        return UpdateResult(
            success=True,
            changed_files=["Pipfile", "Pipfile.lock"],
            message=f"pipenv update {package}~={target_version}",
        )
