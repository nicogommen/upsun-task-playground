"""Async Upsun API client for the admin app.

A single class wrapping httpx.AsyncClient with three concerns:
- Bearer-token acquisition (auth proxy in production, PAT for local dev).
- Task trigger + activity lookup (ITERATION-2 §3.4, §6).
- Environment listing for preview-env discovery (§6.3).

Design notes:
- The HTTP client is injected, not owned. FastAPI manages its lifespan via
  the app's lifespan handler.
- env_id is a parameter on every call, never a class-level constant. This
  keeps cross-env triggering reachable (D3 / §7.3).
- Token caching uses double-checked locking under an asyncio.Lock so concurrent
  pollers don't all refresh on expiry.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

UPSUN_API_BASE = "https://api.upsun.com"
AUTH_PROXY_URL = "http://localhost:8200/oauth2/token"
TOKEN_TTL_SECONDS = 900
TOKEN_REFRESH_LEEWAY = 5


@dataclass
class _CachedToken:
    value: str
    expires_at: float  # time.monotonic() seconds


class UpsunClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        project_id: str,
        *,
        pat: str | None = None,
    ) -> None:
        self._http = http
        self._project_id = project_id
        self._pat = pat
        self._token: _CachedToken | None = None
        self._token_lock = asyncio.Lock()

    async def _bearer(self) -> str:
        if self._pat:
            return self._pat
        cached = self._token
        if cached and time.monotonic() < cached.expires_at - TOKEN_REFRESH_LEEWAY:
            return cached.value
        async with self._token_lock:
            cached = self._token
            if cached and time.monotonic() < cached.expires_at - TOKEN_REFRESH_LEEWAY:
                return cached.value
            self._token = await self._fetch_proxy_token()
            return self._token.value

    async def _fetch_proxy_token(self) -> _CachedToken:
        resp = await self._http.post(
            AUTH_PROXY_URL,
            data={"grant_type": "client_credentials"},
            headers={"x-token-ttl": str(TOKEN_TTL_SECONDS)},
        )
        resp.raise_for_status()
        body = resp.json()
        # Q-iter2-4: trust expires_in (OAuth2 standard); fall back to the
        # requested TTL if the proxy ever omits it.
        ttl = int(body.get("expires_in", TOKEN_TTL_SECONDS))
        return _CachedToken(value=body["access_token"], expires_at=time.monotonic() + ttl)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = await self._bearer()
        headers = kwargs.pop("headers", None) or {}
        headers["Authorization"] = f"Bearer {token}"
        return await self._http.request(
            method, f"{UPSUN_API_BASE}{path}", headers=headers, **kwargs
        )

    async def trigger_task(
        self,
        env_id: str,
        task_name: str,
        env_vars: dict[str, str],
    ) -> dict:
        path = f"/projects/{self._project_id}/environments/{env_id}/tasks/{task_name}/run"
        resp = await self._request("POST", path, json={"variables": {"env": env_vars}})
        resp.raise_for_status()
        return resp.json()

    async def get_activity(self, env_id: str, activity_id: str) -> dict:
        path = f"/projects/{self._project_id}/environments/{env_id}/activities/{activity_id}"
        resp = await self._request("GET", path)
        resp.raise_for_status()
        return resp.json()

    async def list_environments(self) -> list[dict]:
        path = f"/projects/{self._project_id}/environments"
        resp = await self._request("GET", path)
        resp.raise_for_status()
        body = resp.json()
        # Upsun's HAL responses sometimes wrap the list under _embedded; both
        # shapes are tolerated until we pin one down empirically (see §9).
        if isinstance(body, list):
            return body
        return body.get("_embedded", {}).get("environments") or body.get("items", [])

    async def find_env_by_branch(self, branch: str, parent: str = "main") -> dict | None:
        """Find a child env of `parent` whose source branch is `branch`.

        Q-iter2-1: tries `name`, `title`, `head_ref` and logs which field
        matched so we learn empirically on the first preview-env hit.
        """
        envs = await self.list_environments()
        for env in envs:
            if env.get("parent") != parent:
                continue
            for field in ("name", "title", "head_ref"):
                if env.get(field) == branch:
                    logger.info(
                        "preview env match: field=%s value=%r env_id=%r",
                        field,
                        branch,
                        env.get("id") or env.get("name"),
                    )
                    return env
        return None
