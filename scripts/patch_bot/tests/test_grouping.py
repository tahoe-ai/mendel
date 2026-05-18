from patch_bot.core.grouping import group_alerts
from patch_bot.github.alerts import Alert


def _alert(ghsa, pkg, version, *, ecosystem="npm", severity="high", cvss=7.0):
    return Alert(
        number=0,
        ghsa_id=ghsa,
        severity=severity,
        summary="",
        description="",
        cvss_score=cvss,
        package_name=pkg,
        ecosystem=ecosystem,
        manifest_path="",
        current_version_range="",
        first_patched_version=version,
        vulnerable_function_names=[],
        html_url="",
        raw={},
    )


def test_groups_by_package_and_picks_max_version():
    alerts = [
        _alert("GHSA-a", "axios", "1.13.5"),
        _alert("GHSA-b", "axios", "1.15.2"),
        _alert("GHSA-c", "axios", "1.15.1"),
        _alert("GHSA-d", "lodash", "4.17.21"),
    ]
    by_pkg = {g.package: g for g in group_alerts(alerts, wontfix=set())}
    assert set(by_pkg) == {"axios", "lodash"}
    assert by_pkg["axios"].target_version == "1.15.2"
    assert len(by_pkg["axios"].alerts) == 3
    assert by_pkg["lodash"].target_version == "4.17.21"


def test_drops_alerts_without_first_patched_version():
    alerts = [
        _alert("GHSA-a", "axios", "1.15.2"),
        _alert("GHSA-b", "axios", None),
    ]
    groups = group_alerts(alerts, wontfix=set())
    assert len(groups) == 1
    assert len(groups[0].alerts) == 1


def test_drops_wontfix_alerts_but_keeps_siblings():
    alerts = [
        _alert("GHSA-keep", "axios", "1.15.2"),
        _alert("GHSA-skip", "axios", "1.15.1"),
    ]
    groups = group_alerts(alerts, wontfix={"GHSA-skip"})
    assert len(groups) == 1
    assert [a.ghsa_id for a in groups[0].alerts] == ["GHSA-keep"]


def test_package_fully_wontfixed_produces_no_group():
    alerts = [_alert("GHSA-skip", "axios", "1.15.2")]
    assert group_alerts(alerts, wontfix={"GHSA-skip"}) == []


def test_primary_is_highest_cvss():
    alerts = [
        _alert("GHSA-low", "axios", "1.15.2", cvss=4.0, severity="medium"),
        _alert("GHSA-high", "axios", "1.15.1", cvss=9.8, severity="critical"),
    ]
    groups = group_alerts(alerts, wontfix=set())
    assert groups[0].primary.ghsa_id == "GHSA-high"


def test_same_package_different_ecosystem_stays_separate():
    alerts = [
        _alert("GHSA-a", "foo", "1.0.0", ecosystem="npm"),
        _alert("GHSA-b", "foo", "2.0.0", ecosystem="pip"),
    ]
    assert len(group_alerts(alerts, wontfix=set())) == 2


def test_ghsa_ids_property():
    alerts = [_alert("GHSA-a", "axios", "1.15.2"), _alert("GHSA-b", "axios", "1.15.1")]
    groups = group_alerts(alerts, wontfix=set())
    assert groups[0].ghsa_ids == {"GHSA-a", "GHSA-b"}
