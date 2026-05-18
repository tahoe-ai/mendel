from __future__ import annotations

from pathlib import Path

from .base import UpdateResult, run_cmd


class PipToolsUpdater:
    name = "pip-tools"
    ecosystem = "pip"

    def detect(self, repo_root: Path) -> Path | None:
        if any(repo_root.glob("requirements*.in")):
            return repo_root
        return None

    async def update(self, workdir: Path, package: str, target_version: str) -> UpdateResult:
        in_files = sorted(workdir.glob("requirements*.in"))
        if not in_files:
            return UpdateResult(success=False, changed_files=[], message="no requirements*.in found")

        changed: list[str] = []
        for in_file in in_files:
            rc, out, err = await run_cmd(
                [
                    "pip-compile",
                    "--upgrade-package",
                    f"{package}=={target_version}",
                    "--quiet",
                    in_file.name,
                ],
                cwd=workdir,
            )
            if rc != 0:
                return UpdateResult(
                    success=False,
                    changed_files=[],
                    message=f"pip-compile {in_file.name}: {err or out}",
                )
            txt_file = in_file.with_suffix(".txt").name
            if (workdir / txt_file).exists():
                changed.append(txt_file)
        return UpdateResult(
            success=True,
            changed_files=changed,
            message=f"pip-compile --upgrade-package {package}=={target_version}",
        )
