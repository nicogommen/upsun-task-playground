"""In-memory store for admin Run/Session records.

The interface is the contract; the implementation is intentionally a dict
guarded by a lock because v1 admin runs single-worker (D10). The dataclass
schema mirrors the future Postgres tables (D7) so the swap is one file.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime
from threading import Lock
from typing import Literal

RunStatus = Literal[
    "triggering",
    "running",
    "task_complete",
    "succeeded",
    "failed",
]


@dataclass
class Session:
    id: str
    created_at: datetime
    title: str


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
    preview_env_id: str | None = None
    preview_url: str | None = None
    error: str | None = None
    completed_at: datetime | None = None


_RUN_FIELDS = frozenset(f.name for f in fields(Run))

_lock = Lock()
_runs: dict[str, Run] = {}


def save_run(run: Run) -> None:
    with _lock:
        _runs[run.id] = run


def get_run(run_id: str) -> Run | None:
    with _lock:
        return _runs.get(run_id)


def list_runs(session_id: str | None = None, limit: int = 50) -> list[Run]:
    with _lock:
        runs = list(_runs.values())
    if session_id is not None:
        runs = [r for r in runs if r.session_id == session_id]
    runs.sort(key=lambda r: r.created_at, reverse=True)
    return runs[:limit]


def update_run(run_id: str, **changes: object) -> Run:
    unknown = set(changes) - _RUN_FIELDS
    if unknown:
        raise TypeError(f"unknown Run fields: {sorted(unknown)}")
    with _lock:
        existing = _runs.get(run_id)
        if existing is None:
            raise KeyError(run_id)
        updated = replace(existing, **changes)
        _runs[run_id] = updated
        return updated
