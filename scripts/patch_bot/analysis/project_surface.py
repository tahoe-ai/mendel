from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import RepoConfig
from ..github.client import GitHubClient
from ..lockfile_updaters.base import run_cmd
from .importers import ImportSite

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenIssue:
    title: str
    url: str
    reactions: int


@dataclass(frozen=True)
class ProjectSurface:
    direct_import_files: int
    spread_or_wrapped: str  # "wrapped" | "spread" | "none"
    monkey_patch_sites: list[ImportSite] = field(default_factory=list)
    open_issues: list[OpenIssue] = field(default_factory=list)
    uncovered_call_sites: list[ImportSite] = field(default_factory=list)


async def collect(
    repo_root: Path,
    ecosystem: str,
    package: str,
    target_version: str,
    import_sites: list[ImportSite],
    client: GitHubClient,
) -> ProjectSurface:
    files = sorted({s.file for s in import_sites})
    if len(files) == 0:
        spread = "none"
    elif len(files) == 1:
        spread = "wrapped"
    else:
        spread = "spread"

    monkey = await _monkey_patches(repo_root, package)
    issues = await _open_issues(client, package, target_version)
    uncovered = _uncovered_call_sites(repo_root, import_sites)

    return ProjectSurface(
        direct_import_files=len(files),
        spread_or_wrapped=spread,
        monkey_patch_sites=monkey,
        open_issues=issues,
        uncovered_call_sites=uncovered,
    )


async def _monkey_patches(repo_root: Path, package: str) -> list[ImportSite]:
    pkg = re.escape(package)
    pattern = rf"\b{pkg}\.[A-Za-z_][A-Za-z0-9_]*\s*="
    rc, out, _ = await run_cmd(
        ["rg", "--json", "-n", "-e", pattern, "."],
        cwd=repo_root,
        timeout=60.0,
    )
    if rc not in (0, 1):
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
    return sites[:20]


_ISSUE_NOISE_PATTERNS = (
    "dependabot",         # CI/dependabot-config housekeeping issues
    "renovate",           # same for Renovate
    "review round",       # generic CI status issues
    "vulnerabilities found",  # mass advisory roll-ups, not specific bug reports
    "audit fix",          # `npm audit` boilerplate issues
)


async def _open_issues(client: GitHubClient, package: str, version: str) -> list[OpenIssue]:
    """Surface up to 3 open issues across GitHub that mention the target
    version — useful for spotting regressions other projects have already hit
    with this release."""
    q = f'"{package}" "{version}" is:issue is:open in:title,body'
    try:
        data = await client.get_json(
            "/search/issues",
            params={"q": q, "sort": "reactions", "order": "desc", "per_page": 15},
        )
    except Exception as e:
        log.info("issue search failed: %s", e)
        return []
    out: list[OpenIssue] = []
    for it in data.get("items", []):
        title = it.get("title", "")
        reactions = (it.get("reactions") or {}).get("total_count", 0)
        # Reactions floor — filters dead-on-arrival issues that match the
        # search by coincidence (e.g. package name in a long dependency list).
        if reactions < 1:
            continue
        # Title-based noise filter — drop the recurring offenders.
        title_lower = title.lower()
        if any(p in title_lower for p in _ISSUE_NOISE_PATTERNS):
            continue
        out.append(
            OpenIssue(title=title, url=it.get("html_url", ""), reactions=reactions)
        )
        if len(out) >= 3:
            break
    return out


def _normalize_path(p: str, repo_root: Path) -> str:
    """Normalize a path to a repo-relative POSIX string for set-membership matching.
    Absolute paths under repo_root are made relative; leading './' is stripped."""
    if not p:
        return ""
    pp = Path(p)
    if pp.is_absolute():
        try:
            pp = pp.relative_to(repo_root.resolve())
        except ValueError:
            # Coverage file references something outside the repo (build dir,
            # site-packages); leave as-is — won't match an import site, that's fine.
            return pp.as_posix()
    s = pp.as_posix()
    return s[2:] if s.startswith("./") else s


def _uncovered_call_sites(repo_root: Path, sites: list[ImportSite]) -> list[ImportSite]:
    cfg = RepoConfig.load(repo_root)
    coverage_paths = list(cfg.coverage_paths) or ["coverage.xml", "lcov.info"]
    covered: set[tuple[str, int]] = set()
    for rel in coverage_paths:
        path = repo_root / rel
        if not path.exists():
            continue
        if path.suffix == ".xml":
            covered |= _parse_cobertura(path, repo_root)
        elif path.name == "lcov.info" or path.suffix == ".info":
            covered |= _parse_lcov(path, repo_root)
    if not covered:
        return []
    return [
        s for s in sites
        if (_normalize_path(s.file, repo_root), s.line) not in covered
    ]


def _parse_cobertura(path: Path, repo_root: Path) -> set[tuple[str, int]]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return set()
    out: set[tuple[str, int]] = set()
    for cls in root.iter("class"):
        filename = _normalize_path(cls.attrib.get("filename", ""), repo_root)
        for line in cls.iter("line"):
            if int(line.attrib.get("hits", "0")) > 0:
                out.add((filename, int(line.attrib.get("number", "0"))))
    return out


def _parse_lcov(path: Path, repo_root: Path) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    current_file: str | None = None
    for raw in path.read_text(errors="replace").splitlines():
        if raw.startswith("SF:"):
            current_file = _normalize_path(raw[3:].strip(), repo_root)
        elif raw.startswith("DA:") and current_file:
            try:
                line_no, hits = raw[3:].split(",", 1)
                if int(hits) > 0:
                    out.add((current_file, int(line_no)))
            except ValueError:
                continue
        elif raw.strip() == "end_of_record":
            current_file = None
    return out
