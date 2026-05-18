from pathlib import Path

import httpx
import pytest
import respx
from patch_bot.runtimes.gae import GaePlugin


@pytest.fixture
def gae_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.yaml").write_text("runtime: python312\n")
    return tmp_path


@pytest.mark.asyncio
@respx.mock
async def test_compatible_when_requires_python_satisfies(gae_repo: Path):
    respx.get("https://pypi.org/pypi/requests/2.32.0/json").mock(
        return_value=httpx.Response(200, json={"info": {"requires_python": ">=3.8"}})
    )
    v = await GaePlugin().check(gae_repo, "pip", "requests", "2.32.0")
    assert v.runtime_id == "python312"
    assert v.verdict == "compatible"


@pytest.mark.asyncio
@respx.mock
async def test_incompatible_when_requires_newer_python(gae_repo: Path):
    respx.get("https://pypi.org/pypi/requests/2.32.0/json").mock(
        return_value=httpx.Response(200, json={"info": {"requires_python": ">=3.13"}})
    )
    v = await GaePlugin().check(gae_repo, "pip", "requests", "2.32.0")
    assert v.verdict == "incompatible"


@pytest.mark.asyncio
@respx.mock
async def test_unverified_when_no_requires_python(gae_repo: Path):
    respx.get("https://pypi.org/pypi/requests/2.32.0/json").mock(
        return_value=httpx.Response(200, json={"info": {"requires_python": None}})
    )
    v = await GaePlugin().check(gae_repo, "pip", "requests", "2.32.0")
    assert v.verdict == "unverified"


@pytest.mark.asyncio
async def test_no_app_yaml_no_runtime(tmp_path: Path):
    v = await GaePlugin().check(tmp_path, "pip", "requests", "2.32.0")
    assert v.verdict == "no-runtime"


@pytest.mark.asyncio
async def test_unknown_runtime_id(tmp_path: Path):
    (tmp_path / "app.yaml").write_text("runtime: python999\n")
    v = await GaePlugin().check(tmp_path, "pip", "requests", "2.32.0")
    assert v.runtime_id == "python999"
    assert v.verdict == "unverified"


@pytest.mark.asyncio
async def test_ecosystem_not_gated(gae_repo: Path):
    # pip alert against a node runtime would be no-runtime; here app.yaml is python so npm is no-runtime.
    v = await GaePlugin().check(gae_repo, "npm", "lodash", "4.17.21")
    assert v.verdict == "no-runtime"
