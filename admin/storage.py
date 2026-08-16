"""Store for admin Run/Session records.

Two backends behind one async interface (ITERATION-2.x):

- Postgres (asyncpg) when POSTGRESQL_HOST is set, which is the case on Upsun
  wherever the `postgresql` relationship is declared. Runs survive a redeploy.
- An in-memory dict otherwise, so a local `uv run` still starts with no
  database to install (D14).

The choice keys off configuration being *present*, never off a connection
failing. A configured-but-unreachable database is a fault: it raises at
startup rather than degrading to a store that looks healthy while silently
dropping every run at the next redeploy.

Every function is a coroutine, including on the in-memory path, so call sites
do not change shape when the backend does. ITERATION-2 §7.1 claimed the route
layer would not change when Postgres landed; that was wrong, and ITERATION-2.x
§2.1 records why.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.parse import quote

RunStatus = Literal[
    "triggering",
    "running",
    "succeeded",
    "failed",
]

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@dataclass
class Session:
    id: str
    created_at: datetime
    title: str
    # Populated by list_sessions for the history nav. A session is ordered by
    # its most recent run, not by when it was created, so returning to an old
    # chat and running something moves it back to the top.
    last_run_at: datetime | None = None
    run_count: int = 0


@dataclass
class Run:
    id: str
    session_id: str
    prompt: str
    status: RunStatus
    target_environment: str
    created_at: datetime
    activity_id: str | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    error: str | None = None
    completed_at: datetime | None = None


@dataclass
class Export:
    """An export-job run. The admin creates the row and the task fills it in
    from its own connection to the same database (EXPORT-TASK.md D16)."""

    id: str
    status: str
    created_at: datetime
    activity_id: str | None = None
    completed_at: datetime | None = None
    session_count: int | None = None
    run_count: int | None = None
    payload: str | None = None
    error: str | None = None


_RUN_FIELDS = frozenset(f.name for f in fields(Run))
_EXPORT_FIELDS = frozenset(f.name for f in fields(Export))
_EXPORT_COLUMNS = ", ".join(f.name for f in fields(Export))

# Columns selected for a Run, in dataclass order. Kept explicit rather than
# SELECT * because the table carries two columns (preview_env_id, preview_url)
# that the dataclass deliberately does not, pending ITERATION-2 §7.4.
_RUN_COLUMNS = ", ".join(f.name for f in fields(Run))


class _MemoryBackend:
    """Process-local dict. Loses everything on restart, which is the point of
    replacing it in deployed environments."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._runs: dict[str, Run] = {}
        self._sessions: dict[str, Session] = {}
        self._exports: dict[str, Export] = {}

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def ensure_session(self, session_id: str, created_at: datetime, title: str) -> None:
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is None:
                self._sessions[session_id] = Session(
                    id=session_id, created_at=created_at, title=title
                )
            elif not existing.title and title:
                self._sessions[session_id] = replace(existing, title=title)

    async def list_sessions(self, limit: int = 50) -> list[Session]:
        with self._lock:
            sessions = list(self._sessions.values())
            runs = list(self._runs.values())
        by_session: dict[str, list[Run]] = {}
        for run in runs:
            by_session.setdefault(run.session_id, []).append(run)
        summaries = [
            replace(
                s,
                last_run_at=max(r.created_at for r in by_session[s.id]),
                run_count=len(by_session[s.id]),
            )
            for s in sessions
            if by_session.get(s.id)  # mirrors the SQL inner join: no runs, no nav entry
        ]
        summaries.sort(key=lambda s: s.last_run_at, reverse=True)
        return summaries[:limit]

    async def save_run(self, run: Run) -> None:
        with self._lock:
            self._runs[run.id] = run

    async def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(run_id)

    async def list_runs(self, session_id: str | None = None, limit: int = 50) -> list[Run]:
        with self._lock:
            runs = list(self._runs.values())
        if session_id is not None:
            runs = [r for r in runs if r.session_id == session_id]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[:limit]

    async def update_run(self, run_id: str, **changes: object) -> Run:
        with self._lock:
            existing = self._runs.get(run_id)
            if existing is None:
                raise KeyError(run_id)
            updated = replace(existing, **changes)
            self._runs[run_id] = updated
            return updated

    async def save_export(self, export: Export) -> None:
        with self._lock:
            self._exports[export.id] = export

    async def get_export(self, export_id: str) -> Export | None:
        with self._lock:
            return self._exports.get(export_id)

    async def latest_export(self) -> Export | None:
        with self._lock:
            exports = list(self._exports.values())
        return max(exports, key=lambda e: e.created_at) if exports else None

    async def update_export(self, export_id: str, **changes: object) -> Export:
        with self._lock:
            existing = self._exports.get(export_id)
            if existing is None:
                raise KeyError(export_id)
            updated = replace(existing, **changes)
            self._exports[export_id] = updated
            return updated


class _PostgresBackend:
    """asyncpg-backed store. Credentials come from the service env vars Upsun
    injects for the relationship, which is the documented stable path (the
    connection details themselves change across restarts)."""

    def __init__(self) -> None:
        self._pool = None

    @staticmethod
    def _dsn() -> str:
        user = quote(os.environ.get("POSTGRESQL_USERNAME", "main"), safe="")
        password = quote(os.environ.get("POSTGRESQL_PASSWORD", ""), safe="")
        host = os.environ["POSTGRESQL_HOST"]
        port = os.environ.get("POSTGRESQL_PORT", "5432")
        database = os.environ.get("POSTGRESQL_PATH", "main")
        credentials = f"{user}:{password}@" if password else f"{user}@"
        return f"postgresql://{credentials}{host}:{port}/{database}"

    async def connect(self) -> None:
        import asyncpg

        # Small pool on purpose: the admin container is 0.5 CPU / 224 MB and
        # serves one user (ITERATION-2.x §4).
        self._pool = await asyncpg.create_pool(self._dsn(), min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA_PATH.read_text())

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def ensure_session(self, session_id: str, created_at: datetime, title: str) -> None:
        # Backfill the title the first time a non-empty one shows up: /chat is
        # loaded (no prompt, empty title) before the first run is submitted.
        await self._pool.execute(
            """
            INSERT INTO sessions (id, created_at, title)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title
            WHERE sessions.title = '' AND EXCLUDED.title <> ''
            """,
            session_id,
            created_at,
            title,
        )

    async def list_sessions(self, limit: int = 50) -> list[Session]:
        # INNER JOIN, not LEFT: a session with no runs is a page load that never
        # became a chat, and must not show up in the history nav.
        rows = await self._pool.fetch(
            """
            SELECT s.id, s.created_at, s.title,
                   MAX(r.created_at) AS last_run_at,
                   COUNT(r.id)       AS run_count
            FROM sessions s
            JOIN runs r ON r.session_id = s.id
            GROUP BY s.id, s.created_at, s.title
            ORDER BY last_run_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [Session(**dict(r)) for r in rows]

    async def save_run(self, run: Run) -> None:
        await self._pool.execute(
            """
            INSERT INTO runs (
                id, session_id, prompt, status, target_environment, created_at,
                activity_id, branch_name, pr_url, error, completed_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            run.id,
            run.session_id,
            run.prompt,
            run.status,
            run.target_environment,
            run.created_at,
            run.activity_id,
            run.branch_name,
            run.pr_url,
            run.error,
            run.completed_at,
        )

    async def get_run(self, run_id: str) -> Run | None:
        row = await self._pool.fetchrow(f"SELECT {_RUN_COLUMNS} FROM runs WHERE id = $1", run_id)
        return Run(**dict(row)) if row else None

    async def list_runs(self, session_id: str | None = None, limit: int = 50) -> list[Run]:
        if session_id is None:
            rows = await self._pool.fetch(
                f"SELECT {_RUN_COLUMNS} FROM runs ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        else:
            rows = await self._pool.fetch(
                f"SELECT {_RUN_COLUMNS} FROM runs WHERE session_id = $1 "
                f"ORDER BY created_at DESC LIMIT $2",
                session_id,
                limit,
            )
        return [Run(**dict(r)) for r in rows]

    async def save_export(self, export: Export) -> None:
        await self._pool.execute(
            """
            INSERT INTO exports (id, status, created_at, activity_id)
            VALUES ($1, $2, $3, $4)
            """,
            export.id,
            export.status,
            export.created_at,
            export.activity_id,
        )

    async def _fetch_export(self, sql: str, *args: object) -> Export | None:
        row = await self._pool.fetchrow(sql, *args)
        if row is None:
            return None
        # payload is JSONB; asyncpg hands it back as str with no codec set, which
        # is what we want since the admin only ever passes it straight through.
        return Export(**dict(row))

    async def get_export(self, export_id: str) -> Export | None:
        return await self._fetch_export(
            f"SELECT {_EXPORT_COLUMNS} FROM exports WHERE id = $1", export_id
        )

    async def latest_export(self) -> Export | None:
        return await self._fetch_export(
            f"SELECT {_EXPORT_COLUMNS} FROM exports ORDER BY created_at DESC LIMIT 1"
        )

    async def update_export(self, export_id: str, **changes: object) -> Export:
        columns = list(changes)
        # payload is JSONB, so its placeholder needs an explicit cast.
        assignments = ", ".join(
            f"{c} = ${i + 2}" + ("::jsonb" if c == "payload" else "") for i, c in enumerate(columns)
        )
        row = await self._pool.fetchrow(
            f"UPDATE exports SET {assignments} WHERE id = $1 RETURNING {_EXPORT_COLUMNS}",
            export_id,
            *(changes[c] for c in columns),
        )
        if row is None:
            raise KeyError(export_id)
        return Export(**dict(row))

    async def update_run(self, run_id: str, **changes: object) -> Run:
        # Column names are validated against the dataclass fields by the caller
        # below, so interpolating them here cannot carry caller-controlled SQL.
        # Values stay parameterized.
        columns = list(changes)
        assignments = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(columns))
        row = await self._pool.fetchrow(
            f"UPDATE runs SET {assignments} WHERE id = $1 RETURNING {_RUN_COLUMNS}",
            run_id,
            *(changes[c] for c in columns),
        )
        if row is None:
            raise KeyError(run_id)
        return Run(**dict(row))


_backend: _MemoryBackend | _PostgresBackend = _MemoryBackend()


def backend_name() -> str:
    return "postgres" if isinstance(_backend, _PostgresBackend) else "memory"


async def connect() -> None:
    """Pick the backend and open it. Called once from the app lifespan."""
    global _backend
    _backend = _PostgresBackend() if os.environ.get("POSTGRESQL_HOST") else _MemoryBackend()
    await _backend.connect()


async def disconnect() -> None:
    await _backend.disconnect()


async def ensure_session(session_id: str, created_at: datetime, title: str = "") -> None:
    await _backend.ensure_session(session_id, created_at, title)


async def list_sessions(limit: int = 50) -> list[Session]:
    """Sessions that have at least one run, most recently active first."""
    return await _backend.list_sessions(limit)


async def save_run(run: Run) -> None:
    await _backend.save_run(run)


async def get_run(run_id: str) -> Run | None:
    return await _backend.get_run(run_id)


async def list_runs(session_id: str | None = None, limit: int = 50) -> list[Run]:
    return await _backend.list_runs(session_id, limit)


async def update_run(run_id: str, **changes: object) -> Run:
    unknown = set(changes) - _RUN_FIELDS
    if unknown:
        raise TypeError(f"unknown Run fields: {sorted(unknown)}")
    if not changes:
        existing = await _backend.get_run(run_id)
        if existing is None:
            raise KeyError(run_id)
        return existing
    return await _backend.update_run(run_id, **changes)


async def save_export(export: Export) -> None:
    await _backend.save_export(export)


async def get_export(export_id: str) -> Export | None:
    return await _backend.get_export(export_id)


async def latest_export() -> Export | None:
    """Most recent export, used to render the panel on page load."""
    return await _backend.latest_export()


async def update_export(export_id: str, **changes: object) -> Export:
    unknown = set(changes) - _EXPORT_FIELDS
    if unknown:
        raise TypeError(f"unknown Export fields: {sorted(unknown)}")
    if not changes:
        existing = await _backend.get_export(export_id)
        if existing is None:
            raise KeyError(export_id)
        return existing
    return await _backend.update_export(export_id, **changes)
