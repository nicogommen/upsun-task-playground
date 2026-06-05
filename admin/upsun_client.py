"""Async Upsun API client for the admin app.

A single class wrapping httpx.AsyncClient with two concerns:
- Bearer-token acquisition (auth proxy in production, PAT for local dev).
- Task trigger + activity lookup (ITERATION-2 §3.4, §6).

Preview-env discovery is not here: the env-scoped token can't read a sibling
env (Q-iter2-8), so v1 doesn't surface the preview URL. It returns with §7.3.

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
import json
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


def _join_log_stream(body: str) -> str:
    """Reassemble task stdout from the activity log's x-json-stream body.

    Each line is {"_id": N, "data": {"timestamp", "message"}} except the final
    {"_id": N, "seal": true} sentinel (Q-iter2-7). Messages already carry their
    trailing newline, so they're concatenated as-is. Non-JSON lines are skipped.
    """
    parts: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        data = obj.get("data")
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            parts.append(data["message"])
    return "".join(parts)


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

    async def get_activity_log(self, env_id: str, activity_id: str) -> str:
        """Fetch the task's stdout (Q-iter2-7: no longer inline on the activity)."""
        path = f"/projects/{self._project_id}/environments/{env_id}/activities/{activity_id}/log"
        resp = await self._request(
            "GET",
            path,
            params={"start_at": 0, "max_items": 0, "max_delay": -1},
        )
        resp.raise_for_status()
        return _join_log_stream(resp.text)
