from __future__ import annotations

from dataclasses import dataclass

from ..core.config import Target
from .client import GitHubClient


@dataclass(frozen=True)
class Alert:
    number: int
    ghsa_id: str
    severity: str  # "low" | "medium" | "high" | "critical"
    summary: str
    description: str
    cvss_score: float | None
    package_name: str
    ecosystem: str  # "pip" | "npm" | "maven" | ...
    manifest_path: str
    current_version_range: str  # e.g. ">= 0, < 2.32.0"
    first_patched_version: str | None
    vulnerable_function_names: list[str]
    html_url: str
    raw: dict


async def list_alerts(
    client: GitHubClient,
    target: Target,
    *,
    severities: tuple[str, ...] = ("high", "critical"),
) -> list[Alert]:
    """List open Dependabot alerts for the given severities."""
    items = await client.paginate(
        f"/repos/{target.full_name}/dependabot/alerts",
        params={"state": "open", "severity": ",".join(severities)},
    )
    return [_parse(item) for item in items]


def _parse(raw: dict) -> Alert:
    sec_adv = raw.get("security_advisory", {}) or {}
    sec_vuln = raw.get("security_vulnerability", {}) or {}
    pkg = sec_vuln.get("package", {}) or {}
    first_patched = (sec_vuln.get("first_patched_version") or {}).get("identifier")
    cvss = sec_adv.get("cvss") or {}
    vuln_funcs: list[str] = []
    for v in sec_adv.get("vulnerabilities") or []:
        for fn in v.get("vulnerable_functions") or []:
            vuln_funcs.append(fn)
    return Alert(
        number=raw["number"],
        ghsa_id=sec_adv.get("ghsa_id", ""),
        severity=(sec_vuln.get("severity") or sec_adv.get("severity") or "").lower(),
        summary=sec_adv.get("summary", ""),
        description=sec_adv.get("description", ""),
        cvss_score=cvss.get("score"),
        package_name=pkg.get("name", ""),
        ecosystem=(pkg.get("ecosystem") or "").lower(),
        manifest_path=(raw.get("dependency") or {}).get("manifest_path", ""),
        current_version_range=sec_vuln.get("vulnerable_version_range", ""),
        first_patched_version=first_patched,
        vulnerable_function_names=sorted(set(vuln_funcs)),
        html_url=raw.get("html_url", ""),
        raw=raw,
    )
