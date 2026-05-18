from __future__ import annotations

from dataclasses import dataclass

from ..github.alerts import Alert
from .versions import max_version

_SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}


@dataclass
class AlertGroup:
    """All open alerts for one package, consolidated into a single remediation."""

    ecosystem: str
    package: str
    alerts: list[Alert]
    target_version: str  # max first_patched_version across the group

    @property
    def primary(self) -> Alert:
        """The headline advisory — highest CVSS, then highest severity."""
        return max(
            self.alerts,
            key=lambda a: (a.cvss_score or -1.0, _SEVERITY_RANK.get(a.severity, -1)),
        )

    @property
    def ghsa_ids(self) -> set[str]:
        return {a.ghsa_id for a in self.alerts if a.ghsa_id}


def group_alerts(alerts: list[Alert], wontfix: set[str]) -> list[AlertGroup]:
    """Bucket patchable alerts by (ecosystem, package).

    Drops alerts with no `first_patched_version` and any whose GHSA the user
    closed with the wontfix label. A package whose alerts all drop out
    produces no group.
    """
    buckets: dict[tuple[str, str], list[Alert]] = {}
    for a in alerts:
        if not a.first_patched_version:
            continue
        if a.ghsa_id and a.ghsa_id in wontfix:
            continue
        buckets.setdefault((a.ecosystem, a.package_name), []).append(a)

    groups: list[AlertGroup] = []
    for (eco, pkg), grp in buckets.items():
        target = max_version([a.first_patched_version for a in grp if a.first_patched_version])
        groups.append(AlertGroup(ecosystem=eco, package=pkg, alerts=grp, target_version=target))
    return groups
