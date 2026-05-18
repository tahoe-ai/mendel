from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"


class GitHubError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GitHubClient:
    """Thin async httpx wrapper with retries on 5xx/429."""

    def __init__(self, token: str, *, timeout: float = 30.0):
        token = token.strip()
        if not token:
            raise ValueError("GitHub token is empty after stripping whitespace")
        self._client = httpx.AsyncClient(
            base_url=API_ROOT,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "patch-bot",
            },
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> httpx.Response:
        for attempt in range(4):
            r = await self._client.request(method, path, params=params, json=json)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                wait = min(2**attempt, 8)
                log.warning("github %s %s -> %d, retry in %ds", method, path, r.status_code, wait)
                await asyncio.sleep(wait)
                continue
            if r.status_code >= 400:
                raise GitHubError(
                    f"{method} {path} -> {r.status_code}: {r.text[:500]}",
                    status_code=r.status_code,
                )
            return r
        raise GitHubError(f"{method} {path} exhausted retries")

    async def get_json(self, path: str, *, params: dict | None = None) -> Any:
        r = await self.request("GET", path, params=params)
        return r.json()

    async def paginate(self, path: str, *, params: dict | None = None) -> list[Any]:
        out: list[Any] = []
        params = dict(params or {})
        params.setdefault("per_page", 100)
        next_url: str | None = path
        while next_url:
            r = await self.request("GET", next_url, params=params)
            out.extend(r.json())
            link = r.headers.get("Link", "")
            next_url = _parse_next(link)
            params = None  # the Link URL already includes query
        return out


def _parse_next(link_header: str) -> str | None:
    for part in link_header.split(","):
        section = part.strip().split(";")
        if len(section) < 2:
            continue
        if 'rel="next"' in section[1]:
            url = section[0].strip().lstrip("<").rstrip(">")
            return url
    return None
