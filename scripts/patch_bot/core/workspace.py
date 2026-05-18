from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from ..core.config import Settings, Target
from ..lockfile_updaters.base import run_cmd

log = logging.getLogger(__name__)


@asynccontextmanager
async def cloned_repo(settings: Settings, target: Target):
    """Shallow-clone the target repo to a temp dir; clean up on exit."""
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    workdir = settings.work_dir / f"{target.owner}-{target.name}"
    if workdir.exists():
        shutil.rmtree(workdir)

    auth_url = f"https://x-access-token:{settings.github_token}@github.com/{target.full_name}.git"
    rc, out, err = await run_cmd(
        ["git", "clone", "--depth=50", "--filter=blob:none", auth_url, str(workdir)],
        cwd=settings.work_dir,
        timeout=300.0,
    )
    if rc != 0:
        raise RuntimeError(f"clone failed: {err or out}")

    # Configure git user for commits made later
    await run_cmd(["git", "config", "user.email", "patch-bot@users.noreply.github.com"], cwd=workdir)
    await run_cmd(["git", "config", "user.name", "patch-bot"], cwd=workdir)

    try:
        yield workdir
    finally:
        try:
            shutil.rmtree(workdir)
        except OSError as e:
            log.warning("cleanup failed for %s: %s", workdir, e)


async def current_branch(workdir: Path) -> str:
    """Return the freshly-cloned repo's default branch.

    `git clone` leaves HEAD on the remote's default branch, so `rev-parse HEAD`
    is authoritative. We don't fall back to a guess like 'main' — silently
    using the wrong branch causes a 422 on PR creation with a confusing error.
    """
    rc, out, err = await run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=workdir)
    branch = out.strip()
    if rc != 0 or not branch or branch == "HEAD":
        raise RuntimeError(f"could not detect default branch in {workdir}: {err or out!r}")
    return branch


async def create_branch(workdir: Path, branch: str) -> None:
    await run_cmd(["git", "checkout", "-B", branch], cwd=workdir)


async def commit_and_push(
    workdir: Path,
    branch: str,
    files: list[str],
    message: str,
) -> str:
    """Commit `files` and force-push `branch`.

    Returns one of:
      "pushed"  — committed and pushed
      "nothing" — the working tree was clean (the bump was a no-op)
      "failed"  — the push was rejected
    """
    if not files:
        return "nothing"
    await run_cmd(["git", "add", *files], cwd=workdir)
    rc, out, err = await run_cmd(
        ["git", "commit", "-m", message],
        cwd=workdir,
    )
    if rc != 0:
        # `git commit` exits non-zero when there is nothing staged — the
        # updater reported files but they ended up unchanged.
        log.info("nothing to commit on %s: %s", branch, (err or out).strip())
        return "nothing"

    # patch-bot owns the `patch-bot/*` branch namespace. We only push when
    # cutting a brand-new PR — the enrich path never pushes — so the branch is
    # either absent or a leftover from an interrupted/raced run. A plain
    # `--force` is therefore safe and idempotent: a concurrent instance bumping
    # the same package to the same version produces identical content, and
    # `--force` overwrites a leftover without the `--force-with-lease`
    # "stale info" rejection that previously dropped whole consolidated PRs.
    rc, out, err = await run_cmd(
        ["git", "push", "--force", "--set-upstream", "origin", branch],
        cwd=workdir,
        timeout=120.0,
    )
    if rc != 0:
        log.error("push failed: %s", err or out)
        return "failed"
    return "pushed"


async def read_current_version(workdir: Path, package: str, ecosystem: str) -> str | None:
    """Best-effort lookup from the repo's lockfile. Delegates to the snapshot
    helpers in analysis.dependency_delta so the lockfile-type dispatch (yarn /
    pnpm / npm / poetry / Pipfile / requirements.txt) is shared."""
    from ..analysis.dependency_delta import snapshot

    snap = await snapshot(workdir, ecosystem)
    return snap.get(package.lower())
