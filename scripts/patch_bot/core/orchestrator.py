from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from packaging.version import InvalidVersion, Version

from ..analysis import changelog as changelog_mod
from ..analysis import (
    dep_tree,
    dependency_delta,
    importers,
    manifest,
    project_surface,
    registry,
)
from ..github import advisory as advisory_mod
from ..github import alerts as alerts_mod
from ..github import prs as prs_mod
from ..github.client import GitHubClient
from ..github.prs import LABEL_SECURITY, PullRequest
from ..llm.anthropic_client import LLMClient, RiskInputs
from ..llm.budget import TokenBudget
from ..lockfile_updaters import base as updater_base
from ..lockfile_updaters.base import LockfileUpdater, run_cmd
from ..render.pr_body import render_pr_body
from ..runtimes import select_plugin
from .config import Settings, Target
from .grouping import AlertGroup, group_alerts
from .remediation import Remediation, direct_remediation, merge_remediations
from .workspace import (
    cloned_repo,
    commit_and_push,
    create_branch,
    current_branch,
    read_current_version,
)

log = logging.getLogger(__name__)


@dataclass
class GroupResult:
    package: str
    ghsas: list[str]
    target_version: str | None
    # created | enriched-ours | enriched-dependabot | superseded-dependabot | skipped | failed
    action: str
    pr_number: int | None = None
    superseded: list[int] = field(default_factory=list)
    detail: str = ""


@dataclass
class ScanReport:
    target: str
    results: list[GroupResult]
    budget: dict


async def scan_all_targets() -> dict:
    settings = Settings.from_env()
    budget = TokenBudget(
        input_cap=settings.token_budget_input,
        output_cap=settings.token_budget_output,
    )
    reports: list[ScanReport] = []
    async with GitHubClient(settings.github_token) as gh:
        llm = LLMClient(api_key=settings.anthropic_api_key, budget=budget)
        try:
            for target in settings.targets:
                report = await _scan_target(settings, gh, llm, target, budget)
                reports.append(report)
        finally:
            await llm.aclose()
    return {
        "targets": [
            {
                "target": r.target,
                "results": [
                    {
                        "package": x.package,
                        "ghsas": x.ghsas,
                        "target_version": x.target_version,
                        "action": x.action,
                        "pr_number": x.pr_number,
                        "superseded": x.superseded,
                        "detail": x.detail,
                    }
                    for x in r.results
                ],
                "budget": r.budget,
            }
            for r in reports
        ],
        "budget": budget.summary(),
    }


async def _reset_workspace(workdir, base: str) -> None:
    """Return the clone to a clean checkout of the base branch."""
    try:
        await run_cmd(["git", "reset", "--hard"], cwd=workdir)
        await run_cmd(["git", "checkout", base], cwd=workdir)
    except Exception:
        log.exception("workspace reset failed")


async def _scan_target(
    settings: Settings,
    gh: GitHubClient,
    llm: LLMClient,
    target: Target,
    budget: TokenBudget,
) -> ScanReport:
    log.info("scan target %s", target.full_name)
    results: list[GroupResult] = []

    alerts = await alerts_mod.list_alerts(gh, target, severities=settings.severity_floor)
    if not alerts:
        return ScanReport(target=target.full_name, results=[], budget=budget.summary())

    open_prs = await prs_mod.list_open_prs(gh, target)
    wontfix: set[str] = set()
    for pr in await prs_mod.list_closed_wontfix_prs(gh, target):
        wontfix |= pr.ghsa_ids_in_body
    await prs_mod.ensure_label(gh, target, LABEL_SECURITY, color="d73a4a")

    groups = group_alerts(alerts, wontfix)
    if not groups:
        return ScanReport(target=target.full_name, results=[], budget=budget.summary())

    async with cloned_repo(settings, target) as workdir:
        base = await current_branch(workdir)
        updater = updater_base.select_updater(workdir)
        if updater is None:
            return ScanReport(
                target=target.full_name,
                results=[
                    GroupResult(
                        package=g.package,
                        ghsas=sorted(g.ghsa_ids),
                        target_version=g.target_version,
                        action="skipped",
                        detail="no supported lockfile detected (PDM/Hatch/conda/etc not in v1)",
                    )
                    for g in groups
                ],
                budget=budget.summary(),
            )

        # Resolve each flagged-package group into a remediation. A transitive
        # alert may redirect the bump to a first-level parent (root-cause fix).
        remediations: list[Remediation] = []
        for group in groups:
            await _reset_workspace(workdir, base)
            try:
                rem = await _resolve_remediation(group, workdir, base, updater)
            except Exception:
                log.exception("remediation resolve failed for %s", group.package)
                rem = direct_remediation(group)
            remediations.append(rem)

        # Merge remediations that bump the same package into a single PR.
        for rem in merge_remediations(remediations):
            await _reset_workspace(workdir, base)
            try:
                result = await _handle_remediation(
                    gh=gh,
                    llm=llm,
                    target=target,
                    workdir=workdir,
                    base_branch=base,
                    rem=rem,
                    updater=updater,
                    open_prs=open_prs,
                )
            except Exception as e:
                log.exception("remediation handling failed: %s", rem.bump_package)
                result = GroupResult(
                    package=rem.bump_package,
                    ghsas=sorted(rem.ghsa_ids),
                    target_version=rem.bump_version,
                    action="failed",
                    detail=str(e)[:300],
                )
            results.append(result)
    return ScanReport(target=target.full_name, results=results, budget=budget.summary())


# --------------------------------------------------------------------------
# Phase 2: remediation resolution (transitive → root-cause parent bump)
# --------------------------------------------------------------------------


def _version_ge(a: str, b: str) -> bool:
    try:
        return Version(a) >= Version(b)
    except InvalidVersion:
        return a >= b


async def _resolve_remediation(
    group: AlertGroup,
    workdir,
    base: str,
    updater: LockfileUpdater,
) -> Remediation:
    """Decide what to actually bump for a flagged-package group.

    If the flagged package is a direct dependency, bump it directly. If it's
    transitive, try bumping a first-level parent whose newer release pulls in
    a patched version — falling back to a direct bump if none does.
    """
    fallback = direct_remediation(group)
    direct_deps = manifest.read_direct_deps(workdir, group.ecosystem)
    if direct_deps is None:
        return fallback  # unsupported ecosystem/manifest — bump directly
    if group.package.lower() in direct_deps:
        return fallback  # the flagged package is itself a direct dependency

    parents = dep_tree.find_parents(workdir, group.ecosystem, group.package, direct_deps)
    if not parents:
        return fallback
    for parent in parents:
        await _reset_workspace(workdir, base)
        rem = await _try_parent_bump(group, parent, workdir, updater)
        if rem is not None:
            log.info(
                "root-cause: %s is transitive — bumping parent %s to %s resolves it",
                group.package,
                parent,
                rem.bump_version,
            )
            return rem
    await _reset_workspace(workdir, base)
    return fallback


async def _try_parent_bump(
    group: AlertGroup,
    parent: str,
    workdir,
    updater: LockfileUpdater,
) -> Remediation | None:
    """Trial-bump `parent` to its latest version within the current major and
    check whether that pulls the flagged transitive up to a patched version."""
    parent_current = await read_current_version(workdir, parent, group.ecosystem)
    if not parent_current:
        return None
    latest = await registry.latest_in_major(group.ecosystem, parent, parent_current)
    if not latest or latest == parent_current:
        return None
    result = await updater.update(workdir, parent, latest)
    if not result.success:
        return None
    snap = await dependency_delta.snapshot(workdir, group.ecosystem)
    resolved = snap.get(group.package.lower())
    if resolved and _version_ge(resolved, group.target_version):
        return Remediation(
            ecosystem=group.ecosystem,
            bump_package=parent,
            bump_version=latest,
            alerts=list(group.alerts),
            root_cause=True,
            via_packages=[group.package],
        )
    return None


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def _branch_name(package: str, version: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", package).strip("-")
    return f"patch-bot/{safe}-{version}"


def _pr_title(rem: Remediation) -> str:
    base = f"security: {rem.bump_package} → {rem.bump_version}"
    if rem.root_cause:
        return f"{base} (root-cause fix for {', '.join(rem.via_packages)})"
    if len(rem.alerts) == 1:
        return f"{base} ({rem.primary.ghsa_id})"
    return f"{base} ({len(rem.alerts)} advisories)"


def _commit_message(rem: Remediation) -> str:
    n = len(rem.alerts)
    suffix = rem.primary.ghsa_id if n == 1 else f"{n} advisories"
    msg = f"security: bump {rem.bump_package} to {rem.bump_version} ({suffix})"
    if rem.root_cause:
        msg += f" — root-cause fix for {', '.join(rem.via_packages)}"
    return msg


async def _build_body(
    *,
    gh: GitHubClient,
    llm: LLMClient,
    rem: Remediation,
    workdir,
    current_version: str | None,
    update_result,
    before: dict[str, str],
    after: dict[str, str],
) -> str:
    """Run the analysis pipeline against the bumped package and render the body."""
    delta = dependency_delta.diff(before, after) if after else dependency_delta.DepDelta()

    # Analysis runs on the package we actually bump — for a root-cause fix that
    # is the parent, which is what the reviewer needs to test.
    sites = await importers.find_imports(workdir, rem.ecosystem, rem.bump_package)
    surface = await project_surface.collect(
        workdir, rem.ecosystem, rem.bump_package, rem.bump_version, sites, gh
    )
    runtime_plugin = select_plugin(workdir)
    runtime_verdict = await runtime_plugin.check(
        workdir, rem.ecosystem, rem.bump_package, rem.bump_version
    )

    # Changelog: for a root-cause bump the advisory names the transitive, not
    # the package we're bumping — derive the bumped package's repo from the
    # registry instead.
    adv = await advisory_mod.fetch_advisory(gh, rem.primary.ghsa_id)
    if rem.root_cause:
        source_url = await registry.repo_url(rem.ecosystem, rem.bump_package)
    else:
        source_url = adv.source_repository_url
    changelog = await changelog_mod.fetch_changelog(
        gh, source_url, current_version, rem.bump_version
    )

    risk = await llm.assess(
        RiskInputs(
            rem=rem,
            current_version=current_version,
            target_version=rem.bump_version,
            runtime=runtime_verdict,
            changelog=changelog,
            dep_delta=delta,
            surface=surface,
            import_sites=sites,
        )
    )
    return render_pr_body(
        rem=rem,
        current_version=current_version,
        runtime=runtime_verdict,
        changelog=changelog,
        dep_delta=delta,
        surface=surface,
        import_sites=sites,
        risk=risk,
        changed_files=update_result.changed_files if update_result else [],
        change_message=update_result.message if update_result else "(enrichment-only — no bump applied)",
    )


async def _handle_remediation(
    *,
    gh: GitHubClient,
    llm: LLMClient,
    target: Target,
    workdir,
    base_branch: str,
    rem: Remediation,
    updater: LockfileUpdater,
    open_prs: list[PullRequest],
) -> GroupResult:
    ghsas = sorted(rem.ghsa_ids)
    bump_version = rem.bump_version

    ours = prs_mod.find_our_pr_for_group(open_prs, rem.ghsa_ids)
    dependabots = prs_mod.find_dependabot_prs_for_group(open_prs, rem.ghsa_ids)
    current_version = await read_current_version(workdir, rem.bump_package, rem.ecosystem)

    # Pure Dependabot enrichment: a Dependabot PR exists and we have none of
    # our own. We comment on theirs — we don't own a branch to push to, so no
    # bump; analysis runs against the base tree.
    if ours is None and dependabots:
        body = await _build_body(
            gh=gh,
            llm=llm,
            rem=rem,
            workdir=workdir,
            current_version=current_version,
            update_result=None,
            before={},
            after={},
        )
        for dpr in dependabots:
            await prs_mod.upsert_enrichment_comment(gh, target, dpr.number, body)
        return GroupResult(
            package=rem.bump_package,
            ghsas=ghsas,
            target_version=bump_version,
            action="enriched-dependabot",
            pr_number=dependabots[0].number,
        )

    # We will own the resulting PR. Bump onto the right branch — our existing
    # PR's branch if we have one, else a fresh branch — so the diff, title and
    # body always describe the same package and version.
    branch = ours.head_ref if ours is not None else _branch_name(rem.bump_package, bump_version)
    before = await dependency_delta.snapshot(workdir, rem.ecosystem)
    await create_branch(workdir, branch)
    update_result = await updater.update(workdir, rem.bump_package, bump_version)
    if not update_result.success:
        return GroupResult(
            package=rem.bump_package,
            ghsas=ghsas,
            target_version=bump_version,
            action="failed",
            detail=update_result.message[:300],
        )
    after = await dependency_delta.snapshot(workdir, rem.ecosystem)

    body = await _build_body(
        gh=gh,
        llm=llm,
        rem=rem,
        workdir=workdir,
        current_version=current_version,
        update_result=update_result,
        before=before,
        after=after,
    )
    status = await commit_and_push(
        workdir, branch, update_result.changed_files, _commit_message(rem)
    )

    # --- update our existing PR (its branch was re-bumped above to match) ---
    if ours is not None:
        if status == "failed":
            return GroupResult(
                package=rem.bump_package,
                ghsas=ghsas,
                target_version=bump_version,
                action="failed",
                detail="git push failed",
            )
        if status == "nothing":
            log.warning(
                "re-bump of %s produced no diff — base branch may already satisfy it",
                rem.bump_package,
            )
        await prs_mod.update_pr(gh, target, ours.number, title=_pr_title(rem), body=body)
        superseded: list[int] = []
        for dpr in dependabots:
            await prs_mod.close_pr(
                gh,
                target,
                dpr.number,
                f"Superseded by #{ours.number} — patch-bot opened a consolidated "
                f"security PR covering this advisory.",
            )
            superseded.append(dpr.number)
        return GroupResult(
            package=rem.bump_package,
            ghsas=ghsas,
            target_version=bump_version,
            action="superseded-dependabot" if superseded else "enriched-ours",
            pr_number=ours.number,
            superseded=superseded,
        )

    # --- create a new PR ---
    if status != "pushed":
        return GroupResult(
            package=rem.bump_package,
            ghsas=ghsas,
            target_version=bump_version,
            action="skipped" if status == "nothing" else "failed",
            detail=(
                "bump produced no diff (already patched?)"
                if status == "nothing"
                else "git push failed"
            ),
        )
    pr = await prs_mod.create_pr(
        gh, target, title=_pr_title(rem), body=body, head=branch, base=base_branch
    )
    return GroupResult(
        package=rem.bump_package,
        ghsas=ghsas,
        target_version=bump_version,
        action="created",
        pr_number=pr.number,
    )
