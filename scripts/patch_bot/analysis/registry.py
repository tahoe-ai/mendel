from __future__ import annotations

import logging
from urllib.parse import quote

import httpx
from packaging.version import InvalidVersion, Version

log = logging.getLogger(__name__)


async def latest_in_major(ecosystem: str, package: str, current: str) -> str | None:
    """Highest published version sharing `current`'s major version.

    Used for the root-cause parent bump — we deliberately stay within the
    current major to avoid surprise breaking changes. Returns None if the
    ecosystem is unsupported or nothing is found.
    """
    if ecosystem != "npm":
        return None
    try:
        cur = Version(current)
    except InvalidVersion:
        return None
    same_major: list[Version] = []
    for v in await _npm_versions(package):
        try:
            ver = Version(v)
        except InvalidVersion:
            continue
        if ver.is_prerelease:
            continue
        if ver.major == cur.major:
            same_major.append(ver)
    if not same_major:
        return None
    return str(max(same_major))


async def repo_url(ecosystem: str, package: str) -> str | None:
    """Best-effort source-repository URL for a package from registry metadata.

    Needed for the changelog section of a root-cause PR, where the bumped
    package differs from the package the advisory names.
    """
    if ecosystem != "npm":
        return None
    meta = await _npm_metadata(package)
    repo = meta.get("repository") if meta else None
    if isinstance(repo, dict):
        url = repo.get("url", "")
    elif isinstance(repo, str):
        url = repo
    else:
        url = ""
    url = url.replace("git+", "").replace("git://", "https://").removesuffix(".git")
    return url or None


async def _npm_versions(package: str) -> list[str]:
    meta = await _npm_metadata(package)
    return list((meta.get("versions") or {}).keys()) if meta else []


async def _npm_metadata(package: str) -> dict | None:
    # `quote(safe="@")` keeps the `@` of a scoped name but encodes the `/`,
    # which is what the npm registry expects (`/@scope%2Fpkg`).
    url = f"https://registry.npmjs.org/{quote(package, safe='@')}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(url)
        except httpx.HTTPError as e:
            log.warning("npm registry fetch failed for %s: %s", package, e)
            return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None
