from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..github.client import GitHubClient

log = logging.getLogger(__name__)

_SOURCE_HOST_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


@dataclass(frozen=True)
class ChangelogSummary:
    source_url: str | None
    commit_count: int
    pr_titles: list[str]
    release_notes: list[str]  # one entry per release between current..target
    breaking_change_signals: list[str]  # raw lines that look load-bearing for the LLM


async def fetch_changelog(
    client: GitHubClient,
    package_source_url: str | None,
    current_version: str | None,
    target_version: str,
) -> ChangelogSummary:
    if not package_source_url:
        return ChangelogSummary(None, 0, [], [], [])
    m = _SOURCE_HOST_RE.match(package_source_url)
    if not m:
        return ChangelogSummary(package_source_url, 0, [], [], [])
    owner, repo = m.group(1), m.group(2)

    cur_tag = await _resolve_tag(client, owner, repo, current_version) if current_version else None
    tgt_tag = await _resolve_tag(client, owner, repo, target_version)

    pr_titles: list[str] = []
    commit_count = 0
    if cur_tag and tgt_tag:
        try:
            cmp_data = await client.get_json(f"/repos/{owner}/{repo}/compare/{cur_tag}...{tgt_tag}")
            commit_count = cmp_data.get("total_commits", 0)
            for c in cmp_data.get("commits") or []:
                msg = (c.get("commit") or {}).get("message", "").splitlines()[0]
                pr_titles.append(msg)
        except Exception as e:
            log.warning("compare failed for %s/%s %s..%s: %s", owner, repo, cur_tag, tgt_tag, e)

    release_notes = await _release_notes_between(client, owner, repo, current_version, target_version)
    signals = _scan_for_breaking_signals(pr_titles + release_notes)

    return ChangelogSummary(
        source_url=package_source_url,
        commit_count=commit_count,
        pr_titles=pr_titles[:50],
        release_notes=release_notes,
        breaking_change_signals=signals[:30],
    )


async def _resolve_tag(client: GitHubClient, owner: str, repo: str, version: str) -> str | None:
    """Best-effort: try `vX.Y.Z`, `X.Y.Z`, and the release-tag from the releases API."""
    candidates = [f"v{version}", version]
    for tag in candidates:
        try:
            await client.get_json(f"/repos/{owner}/{repo}/git/refs/tags/{tag}")
            return tag
        except Exception:
            continue
    try:
        rel = await client.get_json(f"/repos/{owner}/{repo}/releases/tags/v{version}")
        return rel.get("tag_name")
    except Exception:
        pass
    try:
        rel = await client.get_json(f"/repos/{owner}/{repo}/releases/tags/{version}")
        return rel.get("tag_name")
    except Exception:
        return None


async def _release_notes_between(
    client: GitHubClient,
    owner: str,
    repo: str,
    current: str | None,
    target: str,
) -> list[str]:
    try:
        items = await client.paginate(f"/repos/{owner}/{repo}/releases", params={"per_page": 50})
    except Exception:
        return []
    notes: list[str] = []
    found_target = False
    for r in items:
        tag = (r.get("tag_name") or "").lstrip("v")
        if not found_target and tag == target.lstrip("v"):
            found_target = True
        if not found_target:
            continue
        if current and tag == current.lstrip("v"):
            break
        body = r.get("body") or ""
        if body.strip():
            notes.append(f"## {r.get('tag_name')}\n{body.strip()[:4000]}")
    return notes


_SIGNAL_PATTERNS = [
    re.compile(r"\bbreaking\b", re.I),
    re.compile(r"\bdeprecat", re.I),
    re.compile(r"\bremov(e|ed|ing)\b", re.I),
    re.compile(r"\bdefault\s+(now|changed|to)\b", re.I),
    re.compile(r"\brequire(s|d)?\s+(node|python|new)\b", re.I),
    re.compile(r"\bno\s+longer\b", re.I),
    re.compile(r"\bnow\s+raises?\b", re.I),
    re.compile(r"\bnow\s+(returns?|errors?|throws?)\b", re.I),
]


def _scan_for_breaking_signals(lines: list[str]) -> list[str]:
    out: list[str] = []
    for raw in lines:
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            for pat in _SIGNAL_PATTERNS:
                if pat.search(stripped):
                    out.append(stripped[:300])
                    break
    return out
