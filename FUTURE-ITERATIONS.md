# Future iterations — outlook

**Purpose.** Capture what's already decided or pre-arranged about iterations beyond the one currently in flight, given the architecture choices locked in by iteration 2 (decisions D1–D11 in [ITERATION-2.md](./ITERATION-2.md)).

**Status.** Forward-looking. Nothing here is committed as a build plan — it's the running understanding so we don't lose context between now and when we pick each one up. When an iteration starts, it gets its own `ITERATION-N.md` and the matching section here is collapsed to a back-pointer.

**Companion docs.** [SPEC.md](./SPEC.md) §1 is the iteration index; [ITERATION-2.md](./ITERATION-2.md) is the in-flight detail.

---

## Iteration 2.x — Persistent chat history (Postgres)

**Driver.** Per **D7**, v1 admin stores runs in an in-memory dict, lost on every redeploy. The committed future storage is **Postgres** (not SQLite). This is substantial work — DB service, relationships block, schema, async driver, worker reconfig — so it gets its own iteration row instead of hiding inside iter 2.

**Pre-arranged by iter 2.**
- Schema sketch ready in [ITERATION-2.md §7.1](./ITERATION-2.md) (`Session`, `Run` dataclasses).
- `admin/storage.py` interface keeps call sites stable across the swap.
- Left-nav placeholder already in `chat.html` so wiring up "previous sessions" is a template change, not a layout one.

**Scope.**
- Add a Postgres service to `.upsun/config.yaml` (`services: db: { type: postgresql:16 }` — exact version pinned at iter start).
- Add `relationships: { db: "db:postgresql" }` on the `admin` app.
- Add `asyncpg` (admin is async — D4) to admin's deps.
- Reimplement `admin/storage.py` against Postgres; keep the public interface unchanged.
- Migrations: single `schema.sql` applied at startup is enough until the schema gets richer; switch to alembic if/when needed.
- Drop the single-worker constraint (**D10** released): persistent storage frees us to scale uvicorn workers (`gunicorn -k uvicorn.workers.UvicornWorker -w N`, per ITERATION-2.md §4.1 footnote).
- Wire the left-nav: `list_runs(session_id=None)` grouped by session, rendered as the previously-stubbed sidebar.

**Out of scope.** User accounts (still single hardcoded admin), log retention beyond the Upsun activity log.

---

## Iteration 3 — Verification step

**Driver.** SPEC §1 row 3 — agent waits for preview deploy and curls a URL to confirm the change is live. (This was the original iter 2 scope; demoted to iter 3 when iter 2 was redefined as the admin UI.)

**Pre-arranged by iter 2.**
- Polling logic for activity + preview env ([ITERATION-2.md §6.2/§6.3](./ITERATION-2.md)) is already written for admin. Iter 3 needs the same logic *inside* the `coding-agent` task. Two options at iter-3 start: (a) extract a shared `upsun_client.py` lib used by both admin and the agent; (b) keep them separate and accept ~50 lines of duplication. Decision deferred, but admin's code should be clean enough that extraction is mechanical.
- Auth-proxy flow (**D1**) works the same inside a task container. **Already in place:** `authorizations: [{type: env, action: view}]` is declared on the `coding-agent` task block in `.upsun/config.yaml` (PCO-695), and the proxy was confirmed reachable from inside a running task. Iter 3 consumes that token rather than adding it.
- Marker contract ([§6.5](./ITERATION-2.md): `BRANCH=`, `PR_URL=`) extends naturally — iter 3 adds `VERIFIED=true|false`.

**Scope.**
- After pushing the PR, the agent fetches a proxy token, polls for the matching preview environment, and curls its public URL.
- Verification heuristic: at minimum HTTP 200 on the homepage; ideally an LLM-derived expected substring from the original prompt grep'd against the response body. **Open question** to settle at iter 3 start.
- Admin's status table ([§6.4](./ITERATION-2.md)) gains a `verified` state between `task_complete` and `succeeded` — or `succeeded` simply means "verified" (decision at iter 3 start).

---

## Iteration 4 — Sandbox restrictions

**Driver.** SPEC §1 row 4 — outbound firewall + bubblewrap layered onto the task container.

**Pre-arranged by iter 2.** Iter 2's `authorizations:` work scopes Upsun-side privileges; iter 4's sandbox layers *inside* the container (network egress + filesystem isolation). They're orthogonal — iter 4 doesn't need to revisit auth, and iter 2's auth model survives unchanged.

**Scope.**
- Outbound allowlist: GitHub API + auth proxy (`localhost:8200`) + Anthropic API. Deny everything else by default.
- Bubblewrap or equivalent for filesystem isolation; agent edits constrained to `/tmp/work`.
- **Open question** (empirical): how Upsun's task container interacts with bubblewrap capabilities (seccomp, user-namespace flags). To answer at iter 4 start.

---

## Iteration 5 — Sub-agents and parallelism

**Driver.** SPEC §1 row 5 — one agent task launches and orchestrates others.

**Pre-arranged by iter 2.**
- Trigger pattern is `authorizations: [{type: task, resource: <name>, action: operate}]` (**D1** — same as admin→coding-agent). A sub-agent orchestrator just declares the same authorization on a *task* instead of an *app*. The Upsun docs already cover task-side authorizations.
- `BRANCH=` / `PR_URL=` marker contract ([§6.5](./ITERATION-2.md)) lets a parent agent parse a child's outcome from the activity log without bespoke IPC.
- Postgres-backed admin (iter 2.x) means dozens of runs from a parallel sweep don't get lost; the left-nav becomes load-bearing here.

**Blocked on an unanswered question.** [SPEC.md §7 Q2](./SPEC.md) is still open: what happens when a second trigger fires while one is in flight? The docs mention a default cap of 3 parallel runs, but whether requests above the cap queue, reject, or block is unknown. This iteration is *about* parallelism, so Q2 is a prerequisite, not a footnote. It is cheap to answer before the iteration starts: the `AGENT_SLEEP` probe mode built for [TASK-MIDRUN-ACCESS.md](./TASK-MIDRUN-ACCESS.md) holds runs open with no side effects, so firing four concurrent sleeps and watching the activity list settles it.

**Scope.**
- New agent type — likely `lead-agent/` or `orchestrator-agent/` per the folder convention from SPEC §6.10.
- Orchestrator declares `authorizations: [{type: task, resource: coding-agent, action: operate}]` and uses the same auth-proxy flow as admin.
- Fan-out width has to respect whatever Q2 turns out to be. If the cap rejects rather than queues, the orchestrator needs its own queue.
- **Open question**: how to merge multiple child PRs back into a single parent PR. Unclear at this distance — possibly a final "merge" step in the orchestrator.

---

## Recurring open questions

Not pinned to one iteration; resurface across the plan:

- **User accounts.** Single hardcoded admin works through iter 5 if the playground stays single-user. First real multi-user moment is when a teammate wants to run prompts. Not yet committed to any iteration.
- **Cost tracking.** No structured token-spend instrumentation yet — agent loop emits stdout but no token-usage record. As the system grows (sub-agents in iter 5 multiplying calls), this becomes load-bearing.
- **Failure recovery.** Today, a failed task leaves only the activity log. Postgres in iter 2.x opens the door to a "Retry" button on failed runs and to resuming a session from its last successful state.
