from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..lockfile_updaters.base import run_cmd

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DepDelta:
    added: list[tuple[str, str]] = field(default_factory=list)
    removed: list[tuple[str, str]] = field(default_factory=list)
    bumped: list[tuple[str, str, str]] = field(default_factory=list)  # name, old, new


async def snapshot(repo_root: Path, ecosystem: str) -> dict[str, str]:
    """Return {package_name: version} for resolved transitive deps."""
    if ecosystem == "pip":
        return await _snapshot_python(repo_root)
    if ecosystem == "npm":
        return await _snapshot_node(repo_root)
    return {}


def diff(before: dict[str, str], after: dict[str, str]) -> DepDelta:
    before_names = set(before)
    after_names = set(after)
    added = sorted((n, after[n]) for n in after_names - before_names)
    removed = sorted((n, before[n]) for n in before_names - after_names)
    bumped: list[tuple[str, str, str]] = []
    for n in sorted(before_names & after_names):
        if before[n] != after[n]:
            bumped.append((n, before[n], after[n]))
    return DepDelta(added=added, removed=removed, bumped=bumped)


async def _snapshot_python(repo_root: Path) -> dict[str, str]:
    if (repo_root / "poetry.lock").exists():
        rc, out, _ = await run_cmd(["poetry", "show", "--tree", "--no-ansi"], cwd=repo_root, timeout=120.0)
        if rc == 0:
            return _parse_poetry_show(out)
    if (repo_root / "Pipfile.lock").exists():
        try:
            data = json.loads((repo_root / "Pipfile.lock").read_text())
        except json.JSONDecodeError:
            return {}
        out: dict[str, str] = {}
        for section in ("default", "develop"):
            for name, info in (data.get(section) or {}).items():
                ver = (info or {}).get("version", "").lstrip("=")
                if ver:
                    out[name.lower()] = ver
        return out
    # Fall back to parsing requirements*.txt directly
    out2: dict[str, str] = {}
    for f in sorted(repo_root.glob("requirements*.txt")):
        for line in f.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            m = re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-+]+)", line)
            if m:
                out2[m.group(1).lower()] = m.group(2)
    return out2


def _parse_poetry_show(out: str) -> dict[str, str]:
    """`poetry show --tree` lines look like: 'requests 2.31.0 ...'.
    Strip tree-drawing chars, take first two tokens."""
    result: dict[str, str] = {}
    for raw in out.splitlines():
        # Strip tree characters and leading whitespace
        line = re.sub(r"^[\s│├└─]+", "", raw).strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            name, ver = parts[0], parts[1]
            # filter constraints like '*' or '>=1.0'
            if re.match(r"^[A-Za-z0-9_.\-]+$", name) and re.match(r"^[\d]", ver):
                result.setdefault(name.lower(), ver)
    return result


async def _snapshot_node(repo_root: Path) -> dict[str, str]:
    """Dispatch by lockfile type. Parses lockfiles directly — more reliable
    than invoking each package manager's `list --json` and avoids the npm/yarn
    mismatch (npm ls errors with ENOLOCK on yarn-only projects)."""
    if (repo_root / "pnpm-lock.yaml").exists():
        return _snapshot_pnpm(repo_root)
    if (repo_root / "yarn.lock").exists():
        return _snapshot_yarn(repo_root)
    if (repo_root / "package-lock.json").exists():
        return await _snapshot_npm(repo_root)
    return {}


async def _snapshot_npm(repo_root: Path) -> dict[str, str]:
    rc, out, _ = await run_cmd(
        ["npm", "ls", "--all", "--json", "--package-lock-only", "--silent"],
        cwd=repo_root,
        timeout=120.0,
    )
    if rc not in (0, 1):  # npm ls returns 1 when there are extraneous/peer warnings
        return {}
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return {}
    flat: dict[str, str] = {}

    def walk(deps: dict | None) -> None:
        if not deps:
            return
        for name, info in deps.items():
            if not isinstance(info, dict):
                continue
            ver = info.get("version")
            if ver:
                flat.setdefault(name.lower(), ver)
            walk(info.get("dependencies"))

    walk(data.get("dependencies"))
    return flat


# Matches both yarn 1.x (`version "1.2.3"`) and yarn berry (`version: 1.2.3`).
_YARN_VERSION_RE = re.compile(r'^\s+version[:\s]+"?([^"\s]+)"?')
# Header line of a yarn.lock entry starts at col 0 and ends with `:`.
# Examples:
#   axios@^1.6.0:
#   "@babel/core@^7.0.0, @babel/core@^7.1.0":
#   "@scope/pkg@npm:^1.0.0":
_YARN_HEADER_NAME_RE = re.compile(r'"?(@?[\w][\w./-]*?)@')


def _snapshot_yarn(repo_root: Path) -> dict[str, str]:
    lock = repo_root / "yarn.lock"
    flat: dict[str, str] = {}
    current_name: str | None = None
    for raw in lock.read_text(errors="replace").splitlines():
        if not raw or raw.startswith("#"):
            continue
        if not raw[0].isspace():
            # New entry header
            m = _YARN_HEADER_NAME_RE.match(raw)
            current_name = m.group(1).lower() if m else None
            continue
        if current_name:
            m = _YARN_VERSION_RE.match(raw)
            if m:
                # Overwrite — yarn `upgrade` appends a new entry rather than
                # replacing the old one when other deps still pin the old
                # constraint. The last occurrence is the most recently
                # resolved version, which is what dep-delta wants to see.
                flat[current_name] = m.group(1)
                current_name = None
    return flat


def _snapshot_pnpm(repo_root: Path) -> dict[str, str]:
    lock = repo_root / "pnpm-lock.yaml"
    try:
        data = yaml.safe_load(lock.read_text(errors="replace")) or {}
    except yaml.YAMLError:
        return {}
    flat: dict[str, str] = {}
    # pnpm-lock.yaml v6+: `packages` keys look like `/pkg-name@1.2.3` or
    # `/@scope/pkg@1.2.3(peerSomething@x.y.z)`. Strip the path prefix and any
    # peer-suffix parenthetical.
    for key in (data.get("packages") or {}):
        if not key.startswith("/"):
            continue
        body = key[1:].split("(", 1)[0]
        idx = body.rfind("@")
        if idx <= 0:
            continue
        name, ver = body[:idx].lower(), body[idx + 1:]
        if name and ver:
            flat.setdefault(name, ver)
    return flat
