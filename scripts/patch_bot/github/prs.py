from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.config import Target
from .client import GitHubClient

LABEL_SECURITY = "patch-bot/security"
LABEL_WONTFIX = "patch-bot/wontfix"

GHSA_MARKER_RE = re.compile(r"<!--\s*patchbot:ghsa=(GHSA-[a-z0-9-]+)\s*-->")
ENRICHMENT_MARKER = "<!-- patchbot:enrichment-v1 -->"

DEPENDABOT_BRANCH_RE = re.compile(r"^dependabot/[^/]+/(.+)$")


@dataclass
class PullRequest:
    number: int
    title: str
    body: str
    head_ref: str
    base_ref: str
    labels: list[str]
    user_login: str
    state: str

    @property
    def is_dependabot(self) -> bool:
        return self.user_login == "dependabot[bot]"

    @property
    def ghsa_ids_in_body(self) -> set[str]:
        """All GHSA IDs marked in the body — a consolidated PR carries one
        marker per advisory it resolves."""
        return set(GHSA_MARKER_RE.findall(self.body or ""))


def _parse_pr(raw: dict) -> PullRequest:
    return PullRequest(
        number=raw["number"],
        title=raw.get("title", ""),
        body=raw.get("body") or "",
        head_ref=(raw.get("head") or {}).get("ref", ""),
        base_ref=(raw.get("base") or {}).get("ref", ""),
        labels=[lbl["name"] for lbl in raw.get("labels", [])],
        user_login=(raw.get("user") or {}).get("login", ""),
        state=raw.get("state", ""),
    )


async def list_open_prs(client: GitHubClient, target: Target) -> list[PullRequest]:
    items = await client.paginate(
        f"/repos/{target.full_name}/pulls",
        params={"state": "open", "per_page": 100},
    )
    return [_parse_pr(it) for it in items]


async def list_closed_wontfix_prs(client: GitHubClient, target: Target) -> list[PullRequest]:
    """Closed PRs labeled wontfix — used to suppress re-opening."""
    items = await client.paginate(
        f"/repos/{target.full_name}/issues",  # issues includes PRs and supports labels filter
        params={"state": "closed", "labels": LABEL_WONTFIX, "per_page": 100},
    )
    out: list[PullRequest] = []
    for it in items:
        if "pull_request" not in it:
            continue
        pr = await client.get_json(it["pull_request"]["url"])
        out.append(_parse_pr(pr))
    return out


def find_dependabot_prs_for_group(
    prs: list[PullRequest], ghsa_ids: set[str]
) -> list[PullRequest]:
    """Every open Dependabot PR whose body mentions any advisory in the group.
    Dependabot opens one PR per advisory, so a consolidated group can match
    several."""
    out: list[PullRequest] = []
    for pr in prs:
        if not pr.is_dependabot:
            continue
        body = pr.body or ""
        if any(g and g in body for g in ghsa_ids):
            out.append(pr)
    return out


def find_our_pr_for_group(
    prs: list[PullRequest], ghsa_ids: set[str]
) -> PullRequest | None:
    """Our existing consolidated PR if its marker set overlaps the group."""
    for pr in prs:
        if LABEL_SECURITY not in pr.labels:
            continue
        if pr.ghsa_ids_in_body & ghsa_ids:
            return pr
    return None


async def ensure_label(client: GitHubClient, target: Target, name: str, color: str = "ededed") -> None:
    try:
        await client.get_json(f"/repos/{target.full_name}/labels/{name}")
    except Exception:
        await client.request(
            "POST",
            f"/repos/{target.full_name}/labels",
            json={"name": name, "color": color},
        )


async def create_pr(
    client: GitHubClient,
    target: Target,
    *,
    title: str,
    body: str,
    head: str,
    base: str,
) -> PullRequest:
    r = await client.request(
        "POST",
        f"/repos/{target.full_name}/pulls",
        json={"title": title, "body": body, "head": head, "base": base, "maintainer_can_modify": True},
    )
    pr = _parse_pr(r.json())
    await client.request(
        "POST",
        f"/repos/{target.full_name}/issues/{pr.number}/labels",
        json={"labels": [LABEL_SECURITY]},
    )
    return pr


async def update_pr(
    client: GitHubClient, target: Target, number: int, *, title: str, body: str
) -> None:
    await client.request(
        "PATCH",
        f"/repos/{target.full_name}/pulls/{number}",
        json={"title": title, "body": body},
    )


async def close_pr(client: GitHubClient, target: Target, number: int, comment: str) -> None:
    """Comment on, then close, a PR — used to retire a Dependabot PR that our
    consolidated PR supersedes. We don't touch Dependabot's branch; it owns it."""
    await client.request(
        "POST",
        f"/repos/{target.full_name}/issues/{number}/comments",
        json={"body": comment},
    )
    await client.request(
        "PATCH",
        f"/repos/{target.full_name}/pulls/{number}",
        json={"state": "closed"},
    )


async def upsert_enrichment_comment(
    client: GitHubClient,
    target: Target,
    pr_number: int,
    body: str,
) -> None:
    """Create the enrichment comment, or edit it if it already exists (matched by marker)."""
    full_body = f"{ENRICHMENT_MARKER}\n{body}"
    comments = await client.paginate(
        f"/repos/{target.full_name}/issues/{pr_number}/comments",
    )
    for c in comments:
        if ENRICHMENT_MARKER in (c.get("body") or ""):
            await client.request(
                "PATCH",
                f"/repos/{target.full_name}/issues/comments/{c['id']}",
                json={"body": full_body},
            )
            return
    await client.request(
        "POST",
        f"/repos/{target.full_name}/issues/{pr_number}/comments",
        json={"body": full_body},
    )
