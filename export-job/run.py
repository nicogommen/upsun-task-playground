"""export-job: dump every session and run to structured JSON.

Deliberately not an agent. No LLM, no Anthropic SDK, no tools. It exists to
show that a task container is general-purpose on-demand compute, and that the
same trigger, activity and log machinery serves a plain data job (EXPORT-TASK.md).

Shape of a run:
  1. Admin creates an `exports` row and triggers this task with EXPORT_ID.
  2. This task connects to the same Postgres service over its own relationship,
     reads sessions and runs, and builds the payload.
  3. It writes the result back into its own row and exits. The admin never
     reads this container: task relationships are one-way, and the container is
     gone moments later.

Agent output is synthesized (D17). The runs table has never stored transcripts,
and capturing them is a product feature rather than a demo side-track. Every
synthetic block is flagged so the export cannot be mistaken for measurement.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime

import asyncpg

SYNTHETIC_MODEL = "claude-haiku-4-5-20251001"


def dsn() -> str:
    """Built from the service variables Upsun injects for the relationship."""
    user = os.environ.get("POSTGRESQL_USERNAME", "main")
    password = os.environ.get("POSTGRESQL_PASSWORD", "")
    host = os.environ["POSTGRESQL_HOST"]
    port = os.environ.get("POSTGRESQL_PORT", "5432")
    database = os.environ.get("POSTGRESQL_PATH", "main")
    credentials = f"{user}:{password}@" if password else f"{user}@"
    return f"postgresql://{credentials}{host}:{port}/{database}"


def synthetic_output(run: dict) -> dict:
    """A plausible transcript for a run, derived deterministically from its
    prompt so the same run always exports the same thing.

    This is fabricated. The `synthetic` flag is not decoration: an export that
    does not announce its fixture data is how a demo artifact ends up quoted as
    a real measurement months later.
    """
    prompt = run["prompt"]
    seed = int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)
    target = "frontend/templates/index.html"

    turns = [
        {
            "role": "assistant",
            "type": "text",
            "content": f"I'll locate the file to change for: {prompt}",
        },
        {"role": "assistant", "type": "tool_use", "tool": "list_dir", "input": {"path": "."}},
        {"role": "user", "type": "tool_result", "content": "admin/\ncoding-agent/\nfrontend/"},
        {
            "role": "assistant",
            "type": "tool_use",
            "tool": "read_file",
            "input": {"path": target},
        },
        {"role": "user", "type": "tool_result", "content": "<!DOCTYPE html> ... (truncated)"},
        {
            "role": "assistant",
            "type": "tool_use",
            "tool": "write_file",
            "input": {"path": target, "content": "<!DOCTYPE html> ... (truncated)"},
        },
        {
            "role": "user",
            "type": "tool_result",
            "content": f"write_file: wrote {target}",
        },
        {"role": "assistant", "type": "text", "content": f"Done. Applied: {prompt}"},
    ]

    if run["status"] == "failed":
        turns = [
            *turns[:3],
            {"role": "assistant", "type": "text", "content": "The task did not complete."},
        ]

    return {
        "model": SYNTHETIC_MODEL,
        "turns": turns,
        "token_usage": {"input": 1200 + seed % 900, "output": 300 + seed % 400},
        "duration_seconds": 40 + seed % 40,
        "synthetic": True,
    }


def iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


async def build_payload(conn: asyncpg.Connection) -> tuple[dict, int, int]:
    sessions = await conn.fetch(
        "SELECT id, title, created_at FROM sessions ORDER BY created_at DESC"
    )
    runs = await conn.fetch(
        """
        SELECT id, session_id, prompt, status, target_environment, created_at,
               completed_at, branch_name, pr_url, error
        FROM runs ORDER BY created_at ASC
        """
    )

    by_session: dict[str, list] = {}
    for run in runs:
        by_session.setdefault(run["session_id"], []).append(run)

    exported_sessions = []
    for session in sessions:
        session_runs = by_session.get(session["id"], [])
        exported_sessions.append(
            {
                "id": session["id"],
                "title": session["title"],
                "created_at": iso(session["created_at"]),
                "runs": [
                    {
                        "id": r["id"],
                        "prompt": r["prompt"],
                        "status": r["status"],
                        "target_environment": r["target_environment"],
                        "created_at": iso(r["created_at"]),
                        "completed_at": iso(r["completed_at"]),
                        "branch_name": r["branch_name"],
                        "pr_url": r["pr_url"],
                        "error": r["error"],
                        "agent_output": synthetic_output(dict(r)),
                    }
                    for r in session_runs
                ],
            }
        )

    payload = {
        "export_id": os.environ.get("EXPORT_ID", ""),
        "generated_at": datetime.now(UTC).isoformat(),
        "project": os.environ.get("PLATFORM_PROJECT", ""),
        "environment": os.environ.get("PLATFORM_BRANCH", ""),
        "session_count": len(exported_sessions),
        "run_count": len(runs),
        "agent_output_is_synthetic": True,
        "sessions": exported_sessions,
    }
    return payload, len(exported_sessions), len(runs)


async def main() -> int:
    export_id = os.environ.get("EXPORT_ID", "").strip()
    if not export_id:
        print("EXPORT_ID missing from the trigger payload", flush=True)
        return 1

    print(f"export-job starting for EXPORT_ID={export_id}", flush=True)
    conn = await asyncpg.connect(dsn())
    try:
        payload, session_count, run_count = await build_payload(conn)
        await conn.execute(
            """
            UPDATE exports
            SET status='succeeded', completed_at=$2, session_count=$3,
                run_count=$4, payload=$5::jsonb, error=NULL
            WHERE id=$1
            """,
            export_id,
            datetime.now(UTC),
            session_count,
            run_count,
            json.dumps(payload),
        )
    except Exception as exc:  # the row must record any failure, whatever it is
        print(f"export failed: {exc}", flush=True)
        await conn.execute(
            "UPDATE exports SET status='failed', completed_at=$2, error=$3 WHERE id=$1",
            export_id,
            datetime.now(UTC),
            str(exc),
        )
        return 1
    finally:
        await conn.close()

    # Same self-describing-log contract as the agent's BRANCH= / PR_URL=
    # markers (ITERATION-2 §6.5).
    print(f"EXPORT_ID={export_id}", flush=True)
    print(f"SESSION_COUNT={session_count}", flush=True)
    print(f"RUN_COUNT={run_count}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
