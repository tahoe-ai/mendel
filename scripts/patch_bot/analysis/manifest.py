from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def read_direct_deps(repo_root: Path, ecosystem: str) -> set[str] | None:
    """Names of the first-level (directly-declared) dependencies.

    Returns None when the ecosystem/manifest can't be read — callers should
    treat None as "can't determine" and skip root-cause analysis.
    """
    if ecosystem == "npm":
        return _npm_direct_deps(repo_root)
    # pip manifests vary too much (poetry / PEP621 / requirements.in / bare
    # requirements.txt); root-cause analysis is npm-only in this version.
    return None


def _npm_direct_deps(repo_root: Path) -> set[str] | None:
    path = repo_root / "package.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(errors="replace"))
    except json.JSONDecodeError:
        log.warning("package.json is not valid JSON")
        return None
    names: set[str] = set()
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = data.get(field)
        if isinstance(section, dict):
            names |= {k.lower() for k in section}
    return names
