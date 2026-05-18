from __future__ import annotations

import logging
from importlib.resources import files
from pathlib import Path

import httpx
import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from .base import CompatVerdict

log = logging.getLogger(__name__)

_TABLE: dict | None = None


def _table() -> dict:
    global _TABLE
    if _TABLE is None:
        text = (files(__package__) / "data" / "gae_runtimes.yaml").read_text()
        _TABLE = yaml.safe_load(text) or {}
    return _TABLE


class GaePlugin:
    name = "gae"

    def detect(self, repo_root: Path) -> bool:
        return (repo_root / "app.yaml").exists()

    async def check(
        self,
        repo_root: Path,
        ecosystem: str,
        package: str,
        target_version: str,
    ) -> CompatVerdict:
        runtime_id = _read_runtime(repo_root)
        if not runtime_id:
            return CompatVerdict(None, "no-runtime", "app.yaml has no `runtime:` field")
        row = _table().get(runtime_id)
        if not row:
            return CompatVerdict(runtime_id, "unverified", f"unknown runtime {runtime_id}")
        if ecosystem == "pip" and row.get("language") == "python":
            return await _check_python(runtime_id, row, package, target_version)
        if ecosystem == "npm" and row.get("language") == "node":
            return await _check_node(runtime_id, row, package, target_version)
        return CompatVerdict(runtime_id, "no-runtime", f"ecosystem {ecosystem} not gated by GAE")


def _read_runtime(repo_root: Path) -> str | None:
    path = repo_root / "app.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return None
    runtime = data.get("runtime")
    return str(runtime) if runtime else None


async def _check_python(runtime_id: str, row: dict, package: str, target_version: str) -> CompatVerdict:
    runtime_specifier = SpecifierSet(row["pep440"])
    runtime_version = Version(row["language_version"])
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"https://pypi.org/pypi/{package}/{target_version}/json")
        except httpx.HTTPError as e:
            return CompatVerdict(runtime_id, "unverified", f"PyPI fetch failed: {e}")
    if r.status_code != 200:
        return CompatVerdict(runtime_id, "unverified", f"PyPI returned {r.status_code}")
    info = (r.json() or {}).get("info", {}) or {}
    requires_python = info.get("requires_python") or ""
    if not requires_python:
        return CompatVerdict(
            runtime_id,
            "unverified",
            f"{package} {target_version} declares no requires_python",
        )
    try:
        spec = SpecifierSet(requires_python)
    except Exception:
        return CompatVerdict(
            runtime_id,
            "unverified",
            f"unparseable requires_python {requires_python!r}",
        )
    if runtime_version in spec:
        return CompatVerdict(
            runtime_id,
            "compatible",
            f"{package} {target_version} requires_python={requires_python!r} "
            f"is satisfied by GAE {runtime_id} ({runtime_specifier})",
        )
    return CompatVerdict(
        runtime_id,
        "incompatible",
        f"{package} {target_version} requires_python={requires_python!r} "
        f"does NOT satisfy GAE {runtime_id} ({runtime_specifier})",
    )


async def _check_node(runtime_id: str, row: dict, package: str, target_version: str) -> CompatVerdict:
    """Light check via npm registry's `engines.node` field. We don't run a full semver
    intersection; we just surface what the package declares."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"https://registry.npmjs.org/{package}/{target_version}")
        except httpx.HTTPError as e:
            return CompatVerdict(runtime_id, "unverified", f"npm fetch failed: {e}")
    if r.status_code != 200:
        return CompatVerdict(runtime_id, "unverified", f"npm registry returned {r.status_code}")
    data = r.json() or {}
    engines = (data.get("engines") or {}).get("node")
    runtime_major = row["language_version"]
    if not engines:
        return CompatVerdict(
            runtime_id,
            "unverified",
            f"{package} {target_version} declares no engines.node — likely fine for GAE node{runtime_major}",
        )
    return CompatVerdict(
        runtime_id,
        "unverified",
        f"{package} {target_version} engines.node={engines!r}; "
        f"GAE {runtime_id} runs node {runtime_major} — verify in PR",
    )
