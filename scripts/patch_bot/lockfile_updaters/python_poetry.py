from __future__ import annotations

from pathlib import Path

from .base import UpdateResult, run_cmd


class PoetryUpdater:
    name = "poetry"
    ecosystem = "pip"

    def detect(self, repo_root: Path) -> Path | None:
        if (repo_root / "poetry.lock").exists():
            return repo_root
        return None

    async def update(self, workdir: Path, package: str, target_version: str) -> UpdateResult:
        rc, out, err = await run_cmd(
            ["poetry", "add", "--lock", f"{package}@^{target_version}"],
            cwd=workdir,
        )
        if rc != 0:
            return UpdateResult(success=False, changed_files=[], message=f"poetry: {err or out}")
        return UpdateResult(
            success=True,
            changed_files=["pyproject.toml", "poetry.lock"],
            message=f"poetry add {package}@^{target_version}",
        )
