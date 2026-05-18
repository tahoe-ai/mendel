from __future__ import annotations

from pathlib import Path

from .base import UpdateResult, run_cmd


class YarnUpdater:
    name = "yarn"
    ecosystem = "npm"

    def detect(self, repo_root: Path) -> Path | None:
        if (repo_root / "yarn.lock").exists():
            return repo_root
        return None

    async def update(self, workdir: Path, package: str, target_version: str) -> UpdateResult:
        # `yarn up` is Berry; classic Yarn uses `yarn upgrade`.
        # Detect Berry by presence of .yarnrc.yml or "packageManager: yarn@" in package.json.
        if (workdir / ".yarnrc.yml").exists():
            cmd = ["yarn", "up", f"{package}@{target_version}"]
        else:
            cmd = ["yarn", "upgrade", f"{package}@{target_version}"]
        rc, out, err = await run_cmd(cmd, cwd=workdir)
        if rc != 0:
            return UpdateResult(success=False, changed_files=[], message=f"yarn: {err or out}")
        return UpdateResult(
            success=True,
            changed_files=["package.json", "yarn.lock"],
            message=" ".join(cmd),
        )
