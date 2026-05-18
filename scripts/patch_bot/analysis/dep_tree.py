from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

_YARN_HEADER_NAME_RE = re.compile(r'"?(@?[\w][\w./-]*?)@')


def find_parents(
    repo_root: Path, ecosystem: str, package: str, direct_deps: set[str]
) -> list[str]:
    """Direct dependencies whose dependency subtree contains `package`.

    These are *candidates* — the caller confirms a candidate actually fixes
    the vulnerability by trial-bumping it. Capped at 3 to bound trial-bump
    runtime. npm ecosystem only.
    """
    if ecosystem != "npm":
        return []
    graph = _build_graph(repo_root)
    if not graph:
        return []

    # Reverse the parent→children graph so we can walk up from the vuln package.
    reverse: dict[str, set[str]] = {}
    for parent, children in graph.items():
        for child in children:
            reverse.setdefault(child, set()).add(parent)

    target = package.lower()
    seen: set[str] = set()
    queue: list[str] = [target]
    candidates: list[str] = []
    while queue:
        node = queue.pop()
        for parent in reverse.get(node, ()):
            if parent in seen:
                continue
            seen.add(parent)
            if parent in direct_deps and parent not in candidates:
                candidates.append(parent)
            queue.append(parent)
    return candidates[:3]


def _build_graph(repo_root: Path) -> dict[str, set[str]]:
    """Map of package-name → set of direct-child names, from the lockfile."""
    if (repo_root / "pnpm-lock.yaml").exists():
        return _graph_pnpm(repo_root / "pnpm-lock.yaml")
    if (repo_root / "yarn.lock").exists():
        return _graph_yarn(repo_root / "yarn.lock")
    if (repo_root / "package-lock.json").exists():
        return _graph_npm(repo_root / "package-lock.json")
    return {}


def _graph_yarn(path: Path) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    current: str | None = None
    in_deps = False
    for raw in path.read_text(errors="replace").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent == 0:
            m = _YARN_HEADER_NAME_RE.match(raw)
            current = m.group(1).lower() if m else None
            in_deps = False
            if current:
                graph.setdefault(current, set())
        elif indent == 2 and current:
            in_deps = raw.strip().rstrip(":") in ("dependencies", "optionalDependencies")
        elif indent >= 4 and in_deps and current:
            child = raw.strip().split(" ", 1)[0].strip().strip('"').lower()
            if child:
                graph[current].add(child)
    return graph


def _graph_npm(path: Path) -> dict[str, set[str]]:
    try:
        data = json.loads(path.read_text(errors="replace"))
    except json.JSONDecodeError:
        return {}
    graph: dict[str, set[str]] = {}
    for pkg_path, entry in (data.get("packages") or {}).items():
        if not isinstance(entry, dict):
            continue
        name = _npm_path_name(pkg_path)
        if not name:
            continue
        children: set[str] = set()
        for field in ("dependencies", "optionalDependencies", "peerDependencies"):
            section = entry.get(field)
            if isinstance(section, dict):
                children |= {k.lower() for k in section}
        graph.setdefault(name, set()).update(children)
    return graph


def _npm_path_name(pkg_path: str) -> str | None:
    if not pkg_path:  # the root package entry has an empty key
        return None
    marker = "node_modules/"
    idx = pkg_path.rfind(marker)
    seg = pkg_path[idx + len(marker):] if idx != -1 else pkg_path
    return seg.lower() or None


def _graph_pnpm(path: Path) -> dict[str, set[str]]:
    try:
        data = yaml.safe_load(path.read_text(errors="replace")) or {}
    except yaml.YAMLError:
        return {}
    graph: dict[str, set[str]] = {}
    for key, entry in (data.get("packages") or {}).items():
        if not isinstance(entry, dict):
            continue
        name = _pnpm_key_name(key)
        if not name:
            continue
        children: set[str] = set()
        for field in ("dependencies", "optionalDependencies"):
            section = entry.get(field)
            if isinstance(section, dict):
                children |= {k.lower() for k in section}
        graph.setdefault(name, set()).update(children)
    return graph


def _pnpm_key_name(key: str) -> str | None:
    body = key.lstrip("/").split("(", 1)[0]
    idx = body.rfind("@")
    if idx <= 0:
        return None
    return body[:idx].lower() or None
