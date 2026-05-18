from patch_bot.analysis.changelog import ChangelogSummary
from patch_bot.analysis.dependency_delta import DepDelta
from patch_bot.analysis.importers import ImportSite
from patch_bot.analysis.project_surface import OpenIssue, ProjectSurface
from patch_bot.core.remediation import Remediation
from patch_bot.github.alerts import Alert
from patch_bot.llm.anthropic_client import RiskAssessment
from patch_bot.render.pr_body import render_pr_body
from patch_bot.runtimes.base import CompatVerdict


def _alert(ghsa, *, package="foo", severity="critical", cvss=9.8, description="A crafted input leads to RCE."):
    return Alert(
        number=42,
        ghsa_id=ghsa,
        severity=severity,
        summary=f"Vulnerability in {package} ({ghsa})",
        description=description,
        cvss_score=cvss,
        package_name=package,
        ecosystem="npm",
        manifest_path="package.json",
        current_version_range=">= 0, < 1.2.3",
        first_patched_version="1.2.3",
        vulnerable_function_names=[],
        html_url=f"https://github.com/advisories/{ghsa}",
        raw={},
    )


def _rem(alerts, *, bump_package="foo", bump_version="1.2.3", root_cause=False, via=None):
    return Remediation(
        ecosystem="npm",
        bump_package=bump_package,
        bump_version=bump_version,
        alerts=alerts,
        root_cause=root_cause,
        via_packages=via or [],
    )


def _surface():
    return ProjectSurface(
        direct_import_files=2,
        spread_or_wrapped="spread",
        monkey_patch_sites=[],
        open_issues=[OpenIssue(title="1.2.3 breaks parse", url="https://x", reactions=12)],
        uncovered_call_sites=[ImportSite("src/a.py", 88, "from foo import parse")],
    )


def _risk():
    return RiskAssessment(
        bump_category="patch",
        breaking_change_uncertainty="medium",
        key_findings=["parse() now rejects empty strings"],
        deprecation_to_error=["empty input now raises ValueError"],
        default_changes=[],
        new_required_config=[],
        risk_score="medium",
        test_plan=["Verify src/a.py:88 still passes non-empty input"],
        summary="Patch bump with one behavior change.",
    )


def _render(rem, risk):
    return render_pr_body(
        rem=rem,
        current_version="1.2.0",
        runtime=CompatVerdict("python312", "compatible", "OK"),
        changelog=ChangelogSummary(
            source_url="https://github.com/example/foo",
            commit_count=4,
            pr_titles=["fix: validate input"],
            release_notes=[],
            breaking_change_signals=["BREAKING: parse() now raises on bad input"],
        ),
        dep_delta=DepDelta(bumped=[("idna", "3.3", "3.7")]),
        surface=_surface(),
        import_sites=[ImportSite("src/a.py", 1, "import foo")],
        risk=risk,
        changed_files=["package.json", "yarn.lock"],
        change_message="yarn upgrade foo@1.2.3",
    )


def test_single_advisory_body():
    body = _render(_rem([_alert("GHSA-xxxx-1111-aaaa")]), _risk())
    assert "Resolves [GHSA-xxxx-1111-aaaa]" in body
    assert "<!-- patchbot:ghsa=GHSA-xxxx-1111-aaaa -->" in body
    assert "<!-- patchbot:enrichment-v1 -->" in body
    assert "### GHSA-xxxx-1111-aaaa" in body
    assert "Code Impact" in body
    assert "Risk Assessment" in body
    assert "Root-cause patch" not in body


def test_consolidated_body_has_all_markers_and_cve_sections():
    alerts = [
        _alert("GHSA-xxxx-1111-aaaa"),
        _alert("GHSA-yyyy-2222-bbbb", severity="high", cvss=7.5),
        _alert("GHSA-zzzz-3333-cccc", severity="high", cvss=8.1),
    ]
    body = _render(_rem(alerts), _risk())
    assert "3 advisories" in body
    for ghsa in ("GHSA-xxxx-1111-aaaa", "GHSA-yyyy-2222-bbbb", "GHSA-zzzz-3333-cccc"):
        assert f"<!-- patchbot:ghsa={ghsa} -->" in body
        assert f"### {ghsa}" in body


def test_root_cause_body_has_callout():
    # advisory names postcss, but the PR bumps sanitize-html
    alerts = [_alert("GHSA-pppp-1111-cccc", package="postcss")]
    rem = _rem(
        alerts,
        bump_package="sanitize-html",
        bump_version="2.13.1",
        root_cause=True,
        via=["postcss"],
    )
    body = _render(rem, _risk())
    assert "Root-cause patch" in body
    assert "**postcss**" in body  # the flagged transitive named in the callout
    assert "**sanitize-html**" in body  # the package actually bumped
    assert "<!-- patchbot:ghsa=GHSA-pppp-1111-cccc -->" in body


def test_body_when_risk_unavailable():
    body = _render(_rem([_alert("GHSA-xxxx-1111-aaaa")]), None)
    assert "LLM risk summary unavailable" in body


def test_large_group_body_stays_under_github_limit():
    big_desc = "x" * 8000
    alerts = [_alert(f"GHSA-{i:04d}-aaaa-bbbb", description=big_desc) for i in range(15)]
    body = _render(_rem(alerts), _risk())
    assert len(body) < 65536
    for i in range(15):
        assert f"<!-- patchbot:ghsa=GHSA-{i:04d}-aaaa-bbbb -->" in body
    assert "<!-- patchbot:enrichment-v1 -->" in body
