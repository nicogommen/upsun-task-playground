# Iteration 2.x — Persistent chat history (Postgres)

**Purpose.** Move admin storage from the in-memory dict to Postgres so runs and sessions survive a redeploy, and release the single-worker constraint that the in-memory store forced.

**Status.** In progress (2026-08-16). Platform facts below are verified; the three open choices resolved as D12, D13, D14.

**Companion docs.** [SPEC.md](./SPEC.md) §1 is the iteration index. [ITERATION-2.md](./ITERATION-2.md) is the shipped admin app this extends: §3.5 for the storage interface, §7.1 for the schema sketch this doc supersedes. [FUTURE-ITERATIONS.md](./FUTURE-ITERATIONS.md) held the outline before this doc existed.

---

## 1. Verified platform facts

Checked before planning so the build does not discover these late.

| Fact | Value | How it was checked |
|---|---|---|
| Postgres versions on Upsun | 14, 15, 16, 17, **18** supported; 13 and below retired | `meta.upsun.com/images` |
| Service declaration | `services: { <name>: { type: postgresql:<version> } }` | [PostgreSQL service doc](https://developer.upsun.com/docs/add-services/postgresql) |
| App wiring | `relationships: { postgresql: }` (short form: relationship name resolves to the service of the same name, default endpoint) | same |
| Credentials at runtime | `POSTGRESQL_HOST`, `_PORT`, `_USERNAME`, `_PASSWORD`, `_PATH` (the database name), `_SCHEME`. Preferred over decoding `PLATFORM_RELATIONSHIPS` | same |
| Service needs disk | Yes (`need_disk: true`). Disk is set per environment with `upsun resources:set --service postgresql --disk <MB>` | `meta.upsun.com/images`, `upsun resources:set --help` |
| Current allocation | `admin` and `frontend` at 0.5 CPU / 224 MB, no disk. `coding-agent` 0.5, disk N/A | `upsun resources:get -p vdaznsr6gfmd2 -e main` |
| `asyncpg` on Python 3.14 | Works. 0.31.0 publishes cp314 wheels and resolves clean against 3.14 | `uv pip compile --python-version 3.14` |

`asyncpg` was the risk worth retiring first: it ships C extensions and has historically lagged new Python releases, and every container here is Python 3.14. It is fine.

**Version choice: `postgresql:18`.** [FUTURE-ITERATIONS.md](./FUTURE-ITERATIONS.md) guessed 16 with the version to be pinned at iteration start. 18 is the newest supported and this is a greenfield database with no data to migrate, so there is no reason to start behind.

---

## 2. What the outline got wrong

Two claims carried in [ITERATION-2.md §7.1](./ITERATION-2.md) and [FUTURE-ITERATIONS.md](./FUTURE-ITERATIONS.md) do not survive contact with the code. Correcting them here rather than discovering them mid-build.

**2.1 "The route layer doesn't change" is false.** `admin/storage.py` is synchronous today (a dict behind a `threading.Lock`). Every Postgres driver worth using here is async, so `save_run`, `get_run`, `list_runs`, and `update_run` all become coroutines and each call site needs `await`. There are 8 of them in `admin/app.py` (lines 162, 181, 188, 200, 207, 229, 242, 253). The change is mechanical and small, but it is not zero, and "one-file swap" oversells it.

The alternative, keeping storage synchronous and running a sync driver in a threadpool, is worse: it burns a thread per query inside an app that is async precisely so it can stream logs later (§7.2). Going async is the right call, the docs just need to stop promising otherwise.

**2.2 Sessions are not persisted at all today, so the left-nav is not a template change.** `Session` exists as a dataclass in `storage.py` but nothing ever constructs or stores one. `_ensure_session_id` (`app.py:96`) mints a UUID into the signed cookie and that is the whole session model. Wiring the left-nav therefore needs a `sessions` table, an upsert on first use, a title derivation, and new interface functions, not just the sidebar markup that is already stubbed in `chat.html`. This is the reason C4 below exists.

---

## 3. Decisions

Same convention as [ITERATION-2.md](./ITERATION-2.md): open candidates are `C<n>`, and become `D<n>` once decided.

| ID | Was | Decision | Detail |
|---|---|---|---|
| **D12** | C6 | Land straight on `main`, not via a branch. | §3.1 |
| **D13** | C4 | **Persistence only.** Runs and sessions survive a redeploy; no visible UI change. The session left-nav stays out. | §2.2 |
| **D14** | C5 | Keep an **in-memory fallback**. `storage` selects its backend at startup: Postgres when `POSTGRESQL_HOST` is set, in-memory otherwise. | §6.1 |
| **D15** | — | **Supersedes D13.** The chat-history left-nav ships after all, as a follow-up inside this iteration, together with the session lifecycle it turned out to require. | §10 |

### 3.1 Note on D12

Landing directly on `main` was chosen over the branch-first path recommended here. The trade-off accepted: a service that fails to provision fails the whole deploy, and `frontend` and `admin` go down with it, the evening before a demo. Two things make that recoverable, and they are the reason this is workable rather than reckless:

- The schema is greenfield. Nothing to migrate, nothing to lose by reverting.
- Reverting is one `git revert` plus a push, because every change is additive. No existing behavior is modified, only extended.

If the deploy fails, revert first and diagnose after. Do not debug forward on `main` with a demo pending.

### 3.2 Note on D14

The fallback keys off `POSTGRESQL_HOST` being present, **not** off a failed connection. A connection-failure fallback would let a broken database in production quietly degrade to an in-memory store that looks healthy while silently losing every run at redeploy. Absent configuration is a legitimate state (local dev); an unreachable configured database is a fault and should behave like one, by failing loudly at startup.

---

## 4. Decisions already inherited

| ID | Decision | Source |
|---|---|---|
| **D7** | Postgres, not SQLite | [ITERATION-2.md](./ITERATION-2.md) |
| **D10** | Single worker, because in-memory state demanded it | [ITERATION-2.md §4.6](./ITERATION-2.md) |

**D10 is released but not exercised.** Persistent storage removes the correctness reason for one worker, and the outline treats scaling up as part of this iteration. It should not be: `admin` runs on 0.5 CPU and **224 MB**. Several uvicorn workers plus an asyncpg pool each is a poor fit for that envelope, and this is a single-user playground with no load to justify it. Recommendation: record D10 as released, keep the start command at one worker, and let a future iteration raise the count with a resource bump if there is ever a reason. Anything else spends memory to solve a problem nobody has.

---

## 5. Schema

Supersedes the sketch in [ITERATION-2.md §7.1](./ITERATION-2.md), which was written before Q-iter2-8 removed three fields.

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL,
    title       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runs (
    id                 TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    prompt             TEXT NOT NULL,
    status             TEXT NOT NULL,
    target_environment TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL,
    activity_id        TEXT,
    branch_name        TEXT,
    pr_url             TEXT,
    error              TEXT,
    completed_at       TIMESTAMPTZ,

    -- Unused today. Q-iter2-8 removed the preview-env read, and §7.4 documents
    -- the restore path for when a project-scoped authorization ships. Nullable
    -- columns cost nothing now and save a migration then.
    preview_env_id     TEXT,
    preview_url        TEXT
);

CREATE INDEX IF NOT EXISTS runs_session_created_idx
    ON runs (session_id, created_at DESC);
```

Notes:

- Ids are `TEXT`, not `UUID`. The app treats them as opaque strings (`str(uuid.uuid4())`), so a `UUID` column would mean casting on every read and write, and would turn any non-UUID id into a runtime error at the database boundary. `TEXT` removes that class of bug for no practical loss at this size.
- `status` is plain `TEXT`, deliberately without a `CHECK` constraint. The status set has already changed once (`task_complete` came and went) and will change again if §7.4 lands or iteration 3 adds `verified`. A constraint here buys little and turns each of those into a migration.
- The `session_id` foreign key is the reason `ensure_session` has to exist. Today a run is inserted against a session id that has never been recorded anywhere, which the FK would reject.
- `ON DELETE CASCADE` so clearing a session takes its runs with it.

---

## 6. Changes by file

| File | Change |
|---|---|
| `.upsun/config.yaml` | Add `services: { postgresql: { type: postgresql:18 } }`; add `relationships: { postgresql: }` to `admin` only |
| `admin/pyproject.toml` + `uv.lock` | Add `asyncpg>=0.31` |
| `admin/schema.sql` | New. The DDL above, applied at startup |
| `admin/storage.py` | Rewrite against asyncpg; same call shapes, now coroutines. Add `ensure_session`, plus `list_sessions` if C4 says left-nav |
| `admin/app.py` | `await` at 8 call sites; create and close the pool in `lifespan` alongside the existing httpx client; `_ensure_session_id` also upserts the session row |
| `admin/templates/chat.html` | Left-nav, only if C4 says so |
| Resources | `upsun resources:set --service postgresql --size 0.5 --disk 512 -p vdaznsr6gfmd2 -e main` |
| Docs | SPEC §1 row 2.x to Done; SPEC §3.3 layout; ITERATION-2 §7.1 pointer to this doc; FUTURE-ITERATIONS 2.x section collapsed to a back-pointer |

Connection setup reads the `POSTGRESQL_*` service variables rather than decoding `PLATFORM_RELATIONSHIPS`, per the Upsun guidance that connection details change across restarts.

Schema application: run `schema.sql` on startup inside the lifespan hook. Every statement is `IF NOT EXISTS`, so it is idempotent. With one worker there is no startup race. If the worker count ever rises, this needs an advisory lock, which is worth a comment in the code so the next person sees the coupling.

---

## 7. Cost and blast radius

**Every environment gets its own Postgres.** Services on Upsun are per-environment, so each active preview environment runs its own database container with its own disk. This playground opens a preview environment for every agent PR, which is the entire point of the demo, so the steady-state resource bill rises with the number of open agent PRs rather than being a one-time addition.

There is no clean way to declare a service for `main` only: the config is shared across environments by design. The practical mitigations are to keep the service small (0.5, 512 MB), and to keep pruning merged and abandoned PR environments, which is already the habit.

This is the main reason to be deliberate about C6. It is worth knowing the cost shape before it lands on `main`, not after.

---

## 8. Success criteria

- `admin` deploys with the service attached and `upsun relationships` shows `postgresql`.
- Submitting a prompt creates a run, and the run is still listed after `upsun redeploy`. This is the whole point of the iteration and the one criterion that fails today.
- A fresh environment (a new preview env) starts with an empty schema applied automatically, no manual step.
- Run cards behave exactly as they do now through `triggering → running → succeeded | failed`. No user-visible change beyond persistence, unless C4 adds the left-nav.
- `ruff` and `yamllint` stay clean, and CI still syncs all four projects.

---

## 9. Open questions

- **Q-iter2x-1.** Does branching an environment copy the parent's Postgres data, or start empty? Upsun clones service data on branch for most services, which would mean a preview environment inherits `main`'s run history. Harmless here, possibly confusing in a demo. Confirm on the first preview environment.
- **Q-iter2x-2.** Whether the `admin` container's 224 MB is comfortable with an asyncpg pool open. Expected to be fine at pool size 1 to 5, but worth watching the first deploy rather than assuming.

---

## 10. Chat-history left-nav (D15)

Added after the persistence work landed. D13 had scoped it out; D15 supersedes that.

### 10.1 Three problems the outline did not anticipate

FUTURE-ITERATIONS described this as wiring a stubbed sidebar. Reading the code first turned up three reasons it is not:

1. **There was no way to have more than one chat.** `_ensure_session_id` minted a single UUID into the signed cookie and reused it forever, with no action that could ever start a second one. A history nav over that model lists exactly one session, permanently. The actual work is a session lifecycle, not markup.
2. **Empty sessions would have filled the nav.** `GET /chat` called `ensure_session`, so every idle page load wrote a titleless row. This was already visible in production before the fix: the deployed database held 1 session and 0 runs. Fixed on both sides. `GET /chat` now resolves the id from the cookie without writing (`_active_session_id`), and `list_sessions` inner-joins `runs` so a session with no runs cannot appear.
3. **Run cards showed the time with no date** (`%H:%M:%S`). Correct while all history was same-day, misleading the moment it spans days, which is exactly what this feature introduces. Cards now render `%b %d · %H:%M:%S`.

### 10.2 Shape

- `GET /chat` renders the active session from the cookie, plus the session list.
- `POST /chat/sessions` starts a new chat: a fresh id in the cookie, no row until the first prompt.
- `GET /chat/sessions/{id}` switches the active chat. There is no ownership check because there is one admin user (ITERATION-2 §3.3), so every session belongs to the same operator.
- `storage.list_sessions()` orders by **most recent run**, not by session creation, so returning to an old chat and running something moves it back to the top.
- A brand-new chat is not in the list yet (no runs), so the nav renders it as a placeholder entry to keep the active highlight from being orphaned.

The nav is desktop-only (`lg:`). A slide-over drawer for narrow screens was not worth the JS for a laptop demo.

### 10.3 Seed data

`admin/seed.sql` is demo fixture data: 5 sessions and 14 runs across three weeks, so the nav looks used instead of empty in a walkthrough. Points worth keeping straight:

- **Nothing applies it automatically.** `storage.connect()` runs `schema.sql` only. Seeding is a deliberate manual step.
- Every seeded id is prefixed `seed-`, which makes the undo surgical: real ids are UUIDs and cannot collide. `DELETE FROM sessions WHERE id LIKE 'seed-%'` removes the whole fixture, and runs cascade.
- Timestamps are relative to `now()`, so the history stays plausible whenever it is applied rather than decaying into stale dates.
- PR links point at real closed PRs in the repo, so clicking one in a demo opens a genuine agent PR instead of a 404.
