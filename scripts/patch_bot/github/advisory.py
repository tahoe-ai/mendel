from __future__ import annotations

from dataclasses import dataclass

from .client import GitHubClient


@dataclass(frozen=True)
class AdvisoryDetail:
    ghsa_id: str
    cve_id: str | None
    references: list[str]
    vulnerable_function_names: list[str]
    source_repository_url: str | None  # e.g. https://github.com/psf/requests


async def fetch_advisory(client: GitHubClient, ghsa_id: str) -> AdvisoryDetail:
    raw = await client.get_json(f"/advisories/{ghsa_id}")
    # `references` is documented as `string[]`, but defensively accept the
    # `{url: ...}` object form too in case the schema ever shifts.
    refs: list[str] = []
    for r in raw.get("references") or []:
        if isinstance(r, str):
            refs.append(r)
        elif isinstance(r, dict) and r.get("url"):
            refs.append(r["url"])
    vuln_funcs: list[str] = []
    for v in raw.get("vulnerabilities") or []:
        for fn in v.get("vulnerable_functions") or []:
            vuln_funcs.append(fn)
    source = raw.get("source_code_location")
    return AdvisoryDetail(
        ghsa_id=raw.get("ghsa_id", ghsa_id),
        cve_id=raw.get("cve_id"),
        references=refs,
        vulnerable_function_names=sorted(set(vuln_funcs)),
        source_repository_url=source,
    )
