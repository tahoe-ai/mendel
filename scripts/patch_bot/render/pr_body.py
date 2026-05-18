from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from jinja2 import Environment, FunctionLoader, StrictUndefined

from ..analysis.changelog import ChangelogSummary
from ..analysis.dependency_delta import DepDelta
from ..analysis.importers import ImportSite
from ..analysis.project_surface import ProjectSurface
from ..core.remediation import Remediation
from ..llm.anthropic_client import RiskAssessment
from ..runtimes.base import CompatVerdict

# GitHub rejects PR bodies over 65536 chars. Stay safely under, and split a
# total budget across the per-advisory CVE descriptions (the only unbounded
# part once a group consolidates many advisories).
_BODY_SAFE_LIMIT = 65000
_DESC_TOTAL_BUDGET = 45000
_DESC_MIN_BUDGET = 800


@dataclass
class _AdvisoryView:
    """A single advisory with its description pre-truncated to fit the body budget."""

    ghsa_id: str
    severity: str
    cvss_score: float | None
    summary: str
    description: str
    html_url: str


def _load_template(name: str) -> str | None:
    if name == "pr_body.md.j2":
        return (files(__package__) / "templates" / "pr_body.md.j2").read_text()
    return None


_env = Environment(
    loader=FunctionLoader(_load_template),
    undefined=StrictUndefined,
    trim_blocks=False,
    lstrip_blocks=False,
)


def _truncate(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    return text[:budget].rstrip() + "\n\n*…description truncated.*"


def _enforce_limit(body: str) -> str:
    """Final guard: if the rendered body still exceeds GitHub's limit, trim the
    head but keep the trailing hidden markers — losing those would break PR
    dedup on the next scan."""
    if len(body) <= _BODY_SAFE_LIMIT:
        return body
    marker_idx = body.find("<!-- patchbot:ghsa=")
    markers = body[marker_idx:] if marker_idx != -1 else ""
    notice = "\n\n*…PR body truncated to fit GitHub's 65 KB limit.*\n\n"
    head = body[: _BODY_SAFE_LIMIT - len(markers) - len(notice)]
    return head + notice + markers


def render_pr_body(
    *,
    rem: Remediation,
    current_version: str | None,
    runtime: CompatVerdict,
    changelog: ChangelogSummary,
    dep_delta: DepDelta,
    surface: ProjectSurface,
    import_sites: list[ImportSite],
    risk: RiskAssessment | None,
    changed_files: list[str],
    change_message: str,
) -> str:
    per_desc = max(_DESC_MIN_BUDGET, _DESC_TOTAL_BUDGET // max(1, len(rem.alerts)))
    advisories = [
        _AdvisoryView(
            ghsa_id=a.ghsa_id,
            severity=a.severity,
            cvss_score=a.cvss_score,
            summary=a.summary,
            description=_truncate(a.description, per_desc),
            html_url=a.html_url,
        )
        for a in rem.alerts
    ]
    template = _env.get_template("pr_body.md.j2")
    body = template.render(
        package=rem.bump_package,
        primary=rem.primary,
        advisories=advisories,
        root_cause=rem.root_cause,
        via_packages=rem.via_packages,
        current_version=current_version,
        target_version=rem.bump_version,
        runtime=runtime,
        changelog=changelog,
        dep_delta=dep_delta,
        surface=surface,
        import_sites=import_sites,
        risk=risk,
        changed_files=changed_files,
        change_message=change_message,
    )
    return _enforce_limit(body)
