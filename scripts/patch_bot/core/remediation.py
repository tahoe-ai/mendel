from __future__ import annotations

from dataclasses import dataclass, field

from ..github.alerts import Alert
from .grouping import AlertGroup
from .versions import max_version

_SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}


@dataclass
class Remediation:
    """The resolved unit of work: which package to actually bump, and the
    advisories that bump resolves.

    For a direct dependency this is just the flagged package. For a transitive
    dependency it may instead bump a first-level *parent* whose newer release
    pulls in a patched version of the flagged package (root-cause fix).
    """

    ecosystem: str
    bump_package: str
    bump_version: str
    alerts: list[Alert]
    root_cause: bool = False
    via_packages: list[str] = field(default_factory=list)  # vulnerable transitives

    @property
    def primary(self) -> Alert:
        return max(
            self.alerts,
            key=lambda a: (a.cvss_score or -1.0, _SEVERITY_RANK.get(a.severity, -1)),
        )

    @property
    def ghsa_ids(self) -> set[str]:
        return {a.ghsa_id for a in self.alerts if a.ghsa_id}


def direct_remediation(group: AlertGroup) -> Remediation:
    """The Phase-1 behaviour: bump the flagged package itself."""
    return Remediation(
        ecosystem=group.ecosystem,
        bump_package=group.package,
        bump_version=group.target_version,
        alerts=list(group.alerts),
        root_cause=False,
    )


def merge_remediations(remediations: list[Remediation]) -> list[Remediation]:
    """Collapse remediations that bump the same package into a single PR.

    Two transitive advisories whose root cause is the same parent package
    become one PR; a parent that also has its own advisory merges in too.
    """
    buckets: dict[tuple[str, str], list[Remediation]] = {}
    for r in remediations:
        buckets.setdefault((r.ecosystem, r.bump_package), []).append(r)

    out: list[Remediation] = []
    for (ecosystem, pkg), bucket in buckets.items():
        if len(bucket) == 1:
            out.append(bucket[0])
            continue
        alerts: list[Alert] = []
        seen: set[str] = set()
        via: list[str] = []
        for r in bucket:
            for a in r.alerts:
                if a.ghsa_id not in seen:
                    seen.add(a.ghsa_id)
                    alerts.append(a)
            for v in r.via_packages:
                if v not in via:
                    via.append(v)
        out.append(
            Remediation(
                ecosystem=ecosystem,
                bump_package=pkg,
                bump_version=max_version([r.bump_version for r in bucket]),
                alerts=alerts,
                root_cause=any(r.root_cause for r in bucket),
                via_packages=via,
            )
        )
    return out
