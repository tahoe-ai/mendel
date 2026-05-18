from patch_bot.core.grouping import AlertGroup
from patch_bot.core.remediation import Remediation, direct_remediation, merge_remediations
from patch_bot.github.alerts import Alert


def _alert(ghsa, pkg, *, cvss=7.0, severity="high", fpv="1.0.0"):
    return Alert(
        number=0,
        ghsa_id=ghsa,
        severity=severity,
        summary="",
        description="",
        cvss_score=cvss,
        package_name=pkg,
        ecosystem="npm",
        manifest_path="",
        current_version_range="",
        first_patched_version=fpv,
        vulnerable_function_names=[],
        html_url="",
        raw={},
    )


def test_direct_remediation_bumps_the_flagged_package():
    group = AlertGroup("npm", "axios", [_alert("GHSA-a", "axios")], "1.15.2")
    rem = direct_remediation(group)
    assert rem.bump_package == "axios"
    assert rem.bump_version == "1.15.2"
    assert rem.root_cause is False
    assert rem.via_packages == []


def test_merge_collapses_remediations_with_same_bump_package():
    a = Remediation(
        "npm", "sanitize-html", "2.11.0",
        [_alert("GHSA-1", "postcss")], root_cause=True, via_packages=["postcss"],
    )
    b = Remediation(
        "npm", "sanitize-html", "2.13.0",
        [_alert("GHSA-2", "nanoid")], root_cause=True, via_packages=["nanoid"],
    )
    merged = merge_remediations([a, b])
    assert len(merged) == 1
    m = merged[0]
    assert m.bump_package == "sanitize-html"
    assert m.bump_version == "2.13.0"  # max of the two
    assert {x.ghsa_id for x in m.alerts} == {"GHSA-1", "GHSA-2"}
    assert set(m.via_packages) == {"postcss", "nanoid"}
    assert m.root_cause is True


def test_merge_keeps_distinct_packages_separate():
    a = Remediation("npm", "axios", "1.15.2", [_alert("GHSA-1", "axios")])
    b = Remediation("npm", "lodash", "4.17.21", [_alert("GHSA-2", "lodash")])
    assert len(merge_remediations([a, b])) == 2


def test_merge_dedups_shared_advisories():
    shared = _alert("GHSA-x", "postcss")
    a = Remediation("npm", "sh", "2.1.0", [shared])
    b = Remediation("npm", "sh", "2.2.0", [shared])
    merged = merge_remediations([a, b])
    assert len(merged) == 1
    assert len(merged[0].alerts) == 1


def test_merge_root_cause_true_if_any_member_is():
    a = Remediation("npm", "sh", "2.1.0", [_alert("GHSA-1", "sh")], root_cause=False)
    b = Remediation(
        "npm", "sh", "2.2.0", [_alert("GHSA-2", "postcss")],
        root_cause=True, via_packages=["postcss"],
    )
    merged = merge_remediations([a, b])
    assert merged[0].root_cause is True
