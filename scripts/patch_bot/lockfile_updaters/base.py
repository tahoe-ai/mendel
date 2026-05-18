from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

# Strip credentials embedded in URLs (e.g. `https://x-access-token:<PAT>@github.com/...`)
# before anything gets logged. Catches the GitHub PAT pattern plus generic
# `user:password@host` URLs.
_URL_CRED_RE = re.compile(r"(https?://)[^/\s@]+:[^/\s@]+@")


def _redact(s: str) -> str:
    return _URL_CRED_RE.sub(r"\1***:***@", s)


@dataclass(frozen=True)
class UpdateResult:
    success: bool
    changed_files: list[str]
    message: str


class LockfileUpdater(Protocol):
    name: str
    ecosystem: str  # "pip" | "npm"

    def detect(self, repo_root: Path) -> Path | None:
        """Return the directory the updater applies to, or None."""
        ...

    async def update(
        self,
        workdir: Path,
        package: str,
        target_version: str,
    ) -> UpdateResult: ...


async def run_cmd(
    cmd: list[str],
    cwd: Path,
    *,
    timeout: float = 600.0,
) -> tuple[int, str, str]:
    log.info("$ %s (cwd=%s)", _redact(" ".join(cmd)), cwd)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return 124, "", "timeout"
    return (
        proc.returncode or 0,
        _redact(stdout.decode(errors="replace")),
        _redact(stderr.decode(errors="replace")),
    )


def select_updater(repo_root: Path) -> LockfileUpdater | None:
    """First-match-wins detection across all updaters."""
    from . import (
        node_npm,
        node_pnpm,
        node_yarn,
        python_pip_tools,
        python_pipenv,
        python_poetry,
        python_requirements,
    )

    candidates: list[LockfileUpdater] = [
        python_poetry.PoetryUpdater(),
        python_pipenv.PipenvUpdater(),
        python_pip_tools.PipToolsUpdater(),
        python_requirements.RequirementsUpdater(),
        node_pnpm.PnpmUpdater(),
        node_yarn.YarnUpdater(),
        node_npm.NpmUpdater(),
    ]
    for c in candidates:
        if c.detect(repo_root) is not None:
            return c
    return None
