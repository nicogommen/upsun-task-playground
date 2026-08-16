# Export task — a second task container, deliberately not an agent

**Purpose.** Add an `export-job` task that dumps every session and run to structured JSON for offline use, such as building an eval set. An **Export** button in the admin triggers it; when it finishes, the admin offers the file for download.

**Why it exists.** The point is not the export. The point is to show that a task container is a general-purpose on-demand compute primitive, not an AI feature. Everything shipped so far (`coding-agent`) runs an LLM, which makes it easy to read "task" as "agent runtime". A plain data job on the same machinery corrects that in about ninety seconds of demo time.

**Status.** In progress (2026-08-16). Platform facts verified. Both choices resolved:

| ID | Was | Decision |
|---|---|---|
| **D16** | C7 | **Postgres.** The task relates to the `postgresql` service and writes its result into an `exports` row. No new service, no new public endpoint, no new secret. |
| **D17** | C8 | **Fully synthetic agent output**, flagged `"synthetic": true` in the payload. Capturing real transcripts is a product feature, not a demo side-track. |

**Scope note.** This is a **demo side-track**, not part of the product arc in [SPEC.md](./SPEC.md) §1. It is recorded here rather than in [FUTURE-ITERATIONS.md](./FUTURE-ITERATIONS.md) so the roadmap keeps meaning what it says.

---

## 1. Verified platform facts

| Fact | Value | Source |
|---|---|---|
| Tasks support `relationships` | Yes, to **both services and apps** (`database: {}`, `app: "myapp:http"`) | [tasks doc](https://developer.upsun.com/docs/configure-apps/tasks) |
| Relationship direction | **One-way.** An app cannot declare a relationship *to* a task, because a task exists only while it runs. "If you need the app to receive results from a task, the task should push them to the app." | same |
| Other supported task keys | `source.root`, `run.command`, `run.timeout`, `authorizations`, `hooks.build`, `mounts`, `variables` | same |
| Keys with no meaning on tasks | `web`, `workers`, `crons` | same |
| `run.timeout` | Defaults to **3600**, max 86400. Our `coding-agent` sets 900 explicitly. | same |
| Mount persistence | `instance` and `tmp` mounts reset between runs. Only `storage` or `service` mounts persist. | same |
| `dependencies` / `stack` on tasks | Still rejected (SPEC §7 Q5). Any task needing a library must use the Astral uv installer in `hooks.build`, as `coding-agent` does. | SPEC §7 Q5 |

The one-way relationship rule is the fact that shapes the whole design. A task cannot be polled for its output and its filesystem disappears on exit, so **the result has to be pushed somewhere durable before the container dies.** That is C7.

---

## 2. Open choices

### C7 — Where the export lands

The task has to hand its output to something that outlives it. Three viable targets:

| Option | Shape | Trade-off |
|---|---|---|
| **A. Postgres** (recommended) | Task declares `relationships: postgresql: {}`, reads sessions and runs, writes the JSON into a new `exports` row. Admin reads it back and serves the download. | Reuses the service added in iteration 2.x. No new service, no new public endpoint, no new secret. Demos app→task trigger *and* task→service relationship. |
| **B. Push to the admin over HTTP** | Task declares `relationships: admin: "admin:http"` and POSTs the JSON to an internal admin route. | This is literally the pattern the docs recommend, so it is the most faithful demo of the documented flow. But it needs an unauthenticated endpoint on a publicly reachable admin, whitelisted past `require_login` and protected by a shared secret. That is a new auth surface on the internet for a demo convenience. |
| **C. Shared network-storage mount** | A `network-storage` service mounted by both admin and task. Task writes a file, admin serves it. | Demos a third platform feature, but adds a service, its resources, and its cost to every environment including previews. Heaviest option for the least return. |

**Recommendation: A.** It is the smallest amount of new surface for the same demo beat, and it leans on the Postgres service that already exists. B's only real advantage is fidelity to the documented pattern, and it buys that by putting an unauthenticated write endpoint on a public host, which is a poor trade for a playground.

### C8 — Where the agent output comes from

The `runs` table has **no column for agent output**. It stores the prompt, status, branch, PR URL, and error, and nothing else. The transcript was never captured, so an eval-shaped export needs it from somewhere.

| Option | Shape | Trade-off |
|---|---|---|
| **A. Fully synthetic** (recommended for the demo) | The task generates a plausible transcript per run, derived deterministically from the prompt. | Simple, fast, cannot fail, works identically for seeded and real runs. It is fake, and anyone reading closely will see that. |
| **B. Real where available, synthetic otherwise** | The task fetches the genuine activity log via the Upsun API for runs with a real `activity_id`, and synthesizes for the seeded ones. | The export then contains real agent transcripts, which is a much stronger story for the eval framing. Needs `authorizations: [{type: env, action: view}]` on the task, which `coding-agent` already has, so the pattern is proven. Costs one API call per run and adds a failure path. |

You said fake data is fine and not to build the real thing. Option B is on the table only because it is a smaller step than it sounds: the task already has a proven pattern for minting a proxy token and reading activity logs, and the seeded runs would fall back to synthetic anyway.

---

## 3. Design (assuming C7=A)

### 3.1 Naming

Folder `export-job/`, task `tasks.export-job`. **Not** `export-agent`. SPEC §6.10 established `<name>-agent` for agent types; this deliberately breaks that suffix because it is not an agent, and the naming is part of the point being demonstrated.

### 3.2 Schema addition

```sql
CREATE TABLE IF NOT EXISTS exports (
    id            TEXT PRIMARY KEY,
    status        TEXT NOT NULL,          -- triggering | running | succeeded | failed
    activity_id   TEXT,
    created_at    TIMESTAMPTZ NOT NULL,
    completed_at  TIMESTAMPTZ,
    session_count INTEGER,
    run_count     INTEGER,
    payload       JSONB,                  -- the export itself
    error         TEXT
);
```

Appended to `admin/schema.sql`, so it is created by the same idempotent startup path. `JSONB` rather than `TEXT` so the export is queryable in `upsun sql` during the demo, which is itself a nice thing to show.

### 3.3 Export format

Shaped for an eval harness rather than for a human reader:

```json
{
  "export_id": "…",
  "generated_at": "2026-08-16T23:40:00Z",
  "project": "vdaznsr6gfmd2",
  "environment": "main",
  "session_count": 6,
  "run_count": 15,
  "sessions": [
    {
      "id": "…", "title": "…", "created_at": "…",
      "runs": [
        {
          "id": "…", "prompt": "…", "status": "succeeded",
          "created_at": "…", "completed_at": "…",
          "branch_name": "…", "pr_url": "…", "error": null,
          "agent_output": {
            "model": "claude-haiku-4-5-20251001",
            "turns": [
              {"role": "assistant", "type": "text", "content": "…"},
              {"role": "assistant", "type": "tool_use", "tool": "read_file", "input": {"path": "…"}},
              {"role": "user", "type": "tool_result", "content": "…"}
            ],
            "token_usage": {"input": 0, "output": 0},
            "duration_seconds": 53,
            "synthetic": true
          }
        }
      ]
    }
  ]
}
```

The `"synthetic": true` flag is deliberate. Fixture data that does not announce itself is how a demo artifact ends up quoted as a real measurement three months later.

### 3.4 Config

```yaml
tasks:
  export-job:
    source:
      root: /export-job
    type: "python:3.14"
    hooks:
      build: |
        set -eux
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        uv sync --frozen
    run:
      command: ".venv/bin/python run.py"
      timeout: 300
    relationships:
      postgresql:
```

Same uv-installer workaround as `coding-agent`, because `dependencies` and `stack` are still rejected on tasks (Q5). The task needs `asyncpg`, which this repo has already proven on Python 3.14.

Admin gains a second task authorization:

```yaml
    authorizations:
      - type: task
        resource: coding-agent
        action: operate
      - type: task
        resource: export-job      # new
        action: operate
      - type: env
        action: view
```

### 3.5 Admin changes

| Piece | Change |
|---|---|
| `storage.py` | `create_export`, `get_export`, `latest_export`, `update_export` |
| `app.py` | `POST /exports` triggers the task and creates the row; `GET /exports/{id}` is the HTMX poll returning the status card; `GET /exports/{id}/download` streams the payload with `Content-Disposition: attachment` |
| `chat.html` | An **Export sessions** button in the page header, plus a small status card that polls on the same 5s HTMX cadence as run cards and turns into a download link on success |

The trigger and poll logic is the same shape as runs, so `UpsunClient.trigger_task` and `get_activity` are reused unchanged. No new client code.

### 3.6 What the task does

1. Read `POSTGRESQL_*` from the environment, connect.
2. `SELECT` all sessions and their runs.
3. Build the JSON, attaching `agent_output` per C8.
4. `UPDATE exports SET status='succeeded', payload=…, session_count=…, run_count=… WHERE id=$1`, using the export id passed in via `variables.env.EXPORT_ID`.
5. Print a `EXPORT_ID=` marker to stdout so the activity log stays self-describing, consistent with the `BRANCH=` / `PR_URL=` contract in ITERATION-2 §6.5.

The task writes its own terminal status. The admin's poll then only needs the activity to decide `failed`, and reads the row for everything else.

---

## 4. Demo beat

1. Show the chat history, five weeks of prompts. "Every one of these ran in a task container."
2. Click **Export sessions**. A second task starts, visible in `upsun activity:list` right next to the agent runs.
3. It finishes in seconds. Download the JSON, show the `agent_output` blocks.
4. The line to land: *same primitive, no LLM anywhere in it.* One is an agent, one is a data job, the platform does not care.

Optionally, `upsun sql … "SELECT session_count, run_count FROM exports"` to show the task wrote straight into the service.

---

## 5. Risks

- **Second task authorization on the admin is unproven here.** The `authorizations` field is a list and the schema takes multiple entries, but this project has only ever declared one task resource. If the auth proxy rejects it, the fallback is to widen to a task-wide grant if one exists, or to trigger the export with the same `coding-agent` resource name, which is ugly but demonstrable.
- **Resources.** A new task may need `upsun resources:set --task export-job`. The Postgres service auto-allocated in iteration 2.x, so this probably does too, but it is the kind of thing that only shows up on first deploy.
- **Cost.** Unlike the Postgres service, a task consumes nothing while idle, so adding this to every environment including previews is close to free. Worth saying out loud during the demo, since it contrasts with the database.

---

## 6. Not doing

- Capturing real agent output at run time. That is a genuine feature (`runs` would need a transcript column, and the agent would need to emit one), and it belongs in the product arc if it is ever wanted, not in a demo side-track.
- Scheduling the export, filtering by date, or paginating. One button, everything, one file.
- Any download authentication beyond the existing session gate.

---

## 11. The export result is transient

`GET /chat` renders the panel from `storage.active_export()`, which returns
only exports that have **not** finished. A completed export is the result of an
action the user just took, not durable state, so a reload clears it. Otherwise
"Export ready" and a download link to an increasingly old file sit in the
sidebar forever, and a stale artifact presented as current is exactly the kind
of thing someone downloads by mistake.

An export still in flight *does* survive a reload, deliberately. Clearing those
too would orphan the HTMX poller and the user would never see the run finish.

Nothing is deleted. The row keeps its payload, `GET /exports/{id}` still
renders it, and `GET /exports/{id}/download` still serves it. Only the
page-load rendering drops it, so a link already in hand keeps working.

The filter is applied in SQL rather than after the fetch, so a finished
export's payload is never pulled from the database just to be thrown away.
