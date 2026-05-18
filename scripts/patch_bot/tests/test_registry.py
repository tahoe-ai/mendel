import httpx
import pytest
import respx
from patch_bot.analysis.registry import latest_in_major, repo_url


@pytest.mark.asyncio
@respx.mock
async def test_latest_in_major_picks_highest_same_major():
    respx.get("https://registry.npmjs.org/sanitize-html").mock(
        return_value=httpx.Response(
            200,
            json={
                "versions": {
                    "1.27.5": {},
                    "2.7.0": {},
                    "2.11.0": {},
                    "2.13.1": {},
                    "3.0.0": {},
                }
            },
        )
    )
    assert await latest_in_major("npm", "sanitize-html", "2.7.0") == "2.13.1"


@pytest.mark.asyncio
@respx.mock
async def test_latest_in_major_skips_prereleases():
    respx.get("https://registry.npmjs.org/foo").mock(
        return_value=httpx.Response(
            200, json={"versions": {"2.1.0": {}, "2.2.0": {}, "2.3.0-beta.1": {}}}
        )
    )
    assert await latest_in_major("npm", "foo", "2.1.0") == "2.2.0"


@pytest.mark.asyncio
@respx.mock
async def test_latest_in_major_none_when_no_same_major():
    respx.get("https://registry.npmjs.org/foo").mock(
        return_value=httpx.Response(200, json={"versions": {"3.0.0": {}, "4.0.0": {}}})
    )
    assert await latest_in_major("npm", "foo", "2.1.0") is None


@pytest.mark.asyncio
async def test_latest_in_major_pip_unsupported():
    assert await latest_in_major("pip", "requests", "2.0.0") is None


@pytest.mark.asyncio
@respx.mock
async def test_repo_url_normalizes_git_prefix():
    respx.get("https://registry.npmjs.org/sanitize-html").mock(
        return_value=httpx.Response(
            200,
            json={
                "repository": {
                    "type": "git",
                    "url": "git+https://github.com/apostrophecms/sanitize-html.git",
                }
            },
        )
    )
    assert (
        await repo_url("npm", "sanitize-html")
        == "https://github.com/apostrophecms/sanitize-html"
    )
