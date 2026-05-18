from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..lockfile_updaters.base import run_cmd

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportSite:
    file: str  # relative to repo root
    line: int
    text: str


async def find_imports(repo_root: Path, ecosystem: str, package: str) -> list[ImportSite]:
    patterns = _patterns_for(ecosystem, package)
    if not patterns:
        return []
    pattern = "|".join(patterns)
    rc, out, err = await run_cmd(
        ["rg", "--json", "-n", "--no-heading", "-e", pattern, "."],
        cwd=repo_root,
        timeout=60.0,
    )
    if rc not in (0, 1):  # 1 = no matches; both fine
        log.warning("ripgrep failed (rc=%s): %s", rc, err)
        return []
    sites: list[ImportSite] = []
    for line in out.splitlines():
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") != "match":
            continue
        data = evt.get("data") or {}
        path = (data.get("path") or {}).get("text")
        line_no = data.get("line_number")
        text = (data.get("lines") or {}).get("text", "").rstrip()
        if path and line_no:
            sites.append(ImportSite(file=path, line=int(line_no), text=text))
    return sites


def _patterns_for(ecosystem: str, package: str) -> list[str]:
    pkg = _re_escape(package)
    if ecosystem == "pip":
        # Python: package may be installed under a different import name (rare); v1 uses literal.
        return [
            rf"^\s*import\s+{pkg}(\.|\s|$)",
            rf"^\s*from\s+{pkg}(\.|\s)",
        ]
    if ecosystem == "npm":
        return [
            rf"require\([\"']{pkg}([/\"']|$)",
            rf"from\s+[\"']{pkg}([/\"'])",
            rf"import\s+[\"']{pkg}[\"']",
        ]
    return []


def _re_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(".", "\\.").replace("-", "\\-")
