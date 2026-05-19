from __future__ import annotations

import shutil
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
        if (workdir / ".yarnrc.yml").exists():
            # Yarn Berry — `--mode=update-lockfile` resolves and rewrites the
            # lockfile without fetching or linking node_modules.
            cmd = ["yarn", "up", "--mode=update-lockfile", f"{package}@{target_version}"]
        else:
            # Yarn Classic (1.x) has no lockfile-only mode — `yarn upgrade`
            # always links node_modules. `--ignore-scripts` skips dependency
            # lifecycle hooks (faster, and a security tool should not execute
            # arbitrary install scripts); we delete the node_modules tree
            # afterwards so the build artifact doesn't sit in memory-backed
            # /tmp for the rest of the scan.
            cmd = ["yarn", "upgrade", "--ignore-scripts", f"{package}@{target_version}"]
        rc, out, err = await run_cmd(cmd, cwd=workdir)
        if rc != 0:
            return UpdateResult(success=False, changed_files=[], message=f"yarn: {err or out}")

        # node_modules is a pure build artifact — we only commit the manifest
        # and lockfile. Dropping it keeps peak memory bounded across the scan.
        node_modules = workdir / "node_modules"
        if node_modules.exists():
            shutil.rmtree(node_modules, ignore_errors=True)

        return UpdateResult(
            success=True,
            changed_files=["package.json", "yarn.lock"],
            message=" ".join(cmd),
        )
