# Iteration 2 — Admin UI for triggering the coding-agent

**Purpose.** Replace the terminal-only trigger from Iteration 1 with a small, authenticated web UI: a user logs in, types a prompt, watches the resulting `coding-agent` run, and gets the PR + preview environment URL back when it finishes.

**Status.** In progress (2026-06-05). Steps 1-7 plus the step 6b preview-env transition are implemented and deployed to `main`. The full lifecycle (`triggering → running → task_complete → succeeded`) is wired. `running → task_complete` is confirmed live, and the `task_complete → succeeded` preview-env path is pending an end-to-end run with the step-7 markers. Findings logged in §9.

**Companion docs.** [SPEC.md](./SPEC.md) is the iteration index and Iteration 1 contract. Nothing here replaces it; this doc extends the system, doesn't redefine it.

---

## Decisions

A running record of resolved decisions. Listed once, never relitigated. Detail in the linked sections.

| ID | Was | Decision | Detail |
|---|---|---|---|
| **D1** | C1 | Auth via `authorizations:` + the per-container OAuth2 proxy at `localhost:8200`. No user PAT in production. | §3.4 |
| **D2** | C2 | Restructure into `frontend/`, `admin/`, `coding-agent/` — each Upsun app/task in its own folder. | §5 |
| **D3** | C3 | Admin triggers tasks in its **own** environment (`$PLATFORM_BRANCH`). Auth-proxy tokens are env-scoped by design, so cross-env triggering is deferred (§7.3) but the `Run.target_environment` field is per-run so we don't paint ourselves into a corner. | §3.2, §3.4, §7.3, §9 Q-iter2-5 |
| **D4** | N4 | `admin` is FastAPI + uvicorn (ASGI). `frontend` stays Flask + gunicorn (WSGI). Deliberate multi-stack showcase. | §3.1, §4.1 |
| **D5** | I1 | Roll-our-own auth middleware (~40 lines, no third-party auth library). | §4.5 |
| **D6** | I2 | Poll task activity to completion, **then** poll preview env. Sequential, not parallel. | §6.2, §6.3 |
| **D7** | I3 | In-memory dict + `storage.py` interface in v1. Future swap target is **Postgres** (not SQLite). | §3.5, §7.1 |
| **D8** | I4 | Admin on subdomain `https://admin.{default}/`. | §3.2 |
| **D9** | N1 | argon2 (`argon2-cffi`) for password hashing. | §4.1, §4.5 |
| **D10** | N2 | Single-worker via uvicorn's default; no explicit `--workers` flag. | §4.6 |
| **D11** | N3 | Tailwind via CDN, no JS toolchain. | §4.1, §4.4 |

All open decisions resolved as of 2026-05-08. Empirical questions to answer during build live separately in §9.

---

## 1. Goals

- A second Upsun **application** named `admin` runs alongside `frontend` (renamed from the previous `flask` app) in the playground project.
- The whole `admin` app is gated by username/password. One hardcoded admin user; password stored as an argon2 hash, never plaintext.
- Once logged in, the admin sees a chat-style interface with a prompt input.
- Submitting a prompt triggers `tasks/coding-agent/run` with the prompt in `variables.env.AGENT_PROMPT` and shows a status panel.
- The admin polls the task activity, then the preview environment, and displays:
  - the PR URL when the task completes,
  - the preview environment URL when the build deploys.

## 2. Out of scope for Iteration 2

- Real user management (signup, password reset, multiple users, roles).
- Persistent chat history (no DB in v1; runs live in process memory and are lost on redeploy).
- Streaming the agent's logs to the UI (we poll instead — see §7.2 for the upgrade path).
- Verifying the change is **visually correct** (proposed new iter 3 — moved from old iter 2).
- Sandboxing the task container (still iter 4).

## 3. Architecture overview

### 3.1 Apps & topology

After Iteration 2, the project deliberately mixes stacks to exercise Upsun's multi-runtime story:

| App | Framework | Server | Why this stack |
|---|---|---|---|
| `frontend` | Flask 3.1 (WSGI) | gunicorn | The demo target. Kept simple from iter 1; small Jinja templates, no JS toolchain. |
| `admin` | **FastAPI (ASGI)** — new in iter 2 | uvicorn | Async-native, native SSE, fits the chatbot UI we're building toward. |
| `coding-agent` (task) | stdlib + `anthropic` SDK | n/a (run-to-completion) | Minimal; no web layer. Unchanged from iter 1. |

All three share Python 3.14 + uv for dependency management, so the build pipeline (`uv sync --frozen`) and CI lints (`ruff`, `yamllint`) stay uniform across the project even though the application frameworks differ. **Documenting this multi-stack arrangement is part of the deliverable** — the README must call out the framework split and why we chose it (per N4).

The admin app calls the Upsun task-trigger API (the same endpoint hit by `upsun e:curl tasks/coding-agent/run` in iter 1) to start a `coding-agent` run.

### 3.2 Routing & domains

```yaml
routes:
  "https://{default}/":
    type: upstream
    upstream: "frontend:http"
  "https://admin.{default}/":
    type: upstream
    upstream: "admin:http"
```

- Production admin URL: `https://admin.<env>.<region>.platformsh.site`.
- Preview environments get their own pair (`https://<envid>.…` for frontend, `https://admin.<envid>.…` for admin). Per D3, each admin instance triggers tasks in its own env (preview-env admin → preview-env coding-agent). Cross-env triggering is deferred to §7.3.

### 3.3 Authentication model

- One admin user. Username defaults to `admin` and is overridable via `ADMIN_USERNAME`.
- Password is provided as an **argon2 hash** in the `ADMIN_PASSWORD_HASH` sensitive env var.
- A small CLI helper (`uv run --directory admin python -m passwordhash <password>`) prints a hash for the operator to paste into `upsun variable:create env:ADMIN_PASSWORD_HASH ...`.
- Sessions ride on Starlette's `SessionMiddleware` (FastAPI's underlying framework), signed with `SECRET_KEY` (sensitive env var, ≥32 random bytes). Cookie attributes: `https_only=True`, `same_site="lax"`, `max_age=SESSION_LIFETIME_DAYS * 86400`.
- A custom **HTTP middleware** redirects unauthenticated requests to `/login` for every path except `/login`, `/health`, and `/static/*`. Same outcome as Flask's `before_request`, expressed as `@app.middleware("http")` in FastAPI.

### 3.4 App→task trigger model

The admin app uses Upsun's per-container auth proxy and the platform's `authorizations` mechanism — **no user PAT** (per resolved C1).

1. Admin declares two authorizations in `.upsun/config.yaml` (see §4.6):
   - `{type: task, resource: coding-agent, action: operate}` — lets it trigger the task.
   - `{type: env, action: view}` — lets it list/read environments (needed for the preview-env polling in §6.3).
2. At runtime, `admin/upsun_client.py` (an `httpx.AsyncClient` wrapper) requests a short-lived bearer token from the local auth proxy:
   ```python
   import httpx, time

   async def fetch_token(http: httpx.AsyncClient) -> tuple[str, float]:
       resp = await http.post(
           "http://localhost:8200/oauth2/token",
           data={"grant_type": "client_credentials"},
           headers={"x-token-ttl": "900"},  # 15 min, the documented max
       )
       resp.raise_for_status()
       body = resp.json()
       return body["access_token"], time.monotonic() + int(body.get("expires_in", 60))
   ```
   Default TTL is 60 s; we ask for the max (900 s) and cache the token in process memory, refreshing when the cached expiry is within ~5 s of `time.monotonic()`.
3. Admin POSTs to `https://api.upsun.com/projects/$PLATFORM_PROJECT/environments/<target_env>/tasks/coding-agent/run` with `Authorization: Bearer <token>` and body `{"variables":{"env":{"AGENT_PROMPT":"..."}}}`.
4. The 202 response carries the activity. Admin extracts `activity_id` and starts polling.

Project id is read from `$PLATFORM_PROJECT` (a standard Upsun runtime var). The `<target_env>` is the env admin itself runs in (`$PLATFORM_BRANCH`) — auth-proxy tokens are scoped to their issuing env per the [authorizations doc](https://upsun-c9761871-492-new-task-container.mintlify.app/docs/configure-apps/image-properties/authorizations) ("a token issued in one environment cannot act on another"). Cross-env triggering (admin-on-preview → main, admin-on-main → preview, etc.) needs a different mechanism; we keep `target_environment` as a per-run field so the data model is open. See §7.3.

Polling lifecycle: see §6.

### 3.5 Storage model

`admin/storage.py` exposes a thin interface:

```python
def save_run(run: Run) -> None: ...
def get_run(run_id: str) -> Run | None: ...
def list_runs(session_id: str | None = None, limit: int = 50) -> list[Run]: ...
def update_run(run_id: str, **fields) -> Run: ...
```

The v1 implementation is an in-memory dict guarded by a lock. The schema (a `Run` dataclass and an implicit `Session` keyed by an opaque id) is designed for the future move to **Postgres** (per D7) without changing call sites. See §7.1.

---

## 4. Application specification (`admin`)

### 4.1 Tech stack

- Python 3.14.
- **FastAPI** (current stable, ≥0.115) on **uvicorn** (ASGI). Per N4 — async-native, fits the chatbot/streaming UI we're building toward, and intentionally diverges from `frontend`'s Flask + WSGI stack to exercise Upsun's multi-runtime support.
- **Jinja2Templates** (`fastapi.templating.Jinja2Templates`) for HTML rendering. Same template language as `frontend`, just a different glue layer.
- **HTMX** via CDN for form swapping and polling. Picked over a JS framework because v1 interactivity is "form submit + periodic status fetch" — HTMX handles both with `hx-post` and `hx-trigger="every Ns"` and upgrades cleanly to SSE later.
- **Tailwind** via CDN (consistent with `frontend`).
- **`httpx`** (async) for both the auth-proxy token request and the Upsun API calls. Cleaner than running `urllib` in a thread pool, and async fits FastAPI naturally. (The `coding-agent` task keeps its stdlib-only stance; that's a separate process.)
- **`starlette.middleware.sessions.SessionMiddleware`** for signed session cookies. Ships with FastAPI (Starlette is its base).
- **`python-multipart`** for `Form(...)` parsing (login form).
- **`argon2-cffi`** for password hashing.
- Single-worker server: `uvicorn app:app --host 0.0.0.0 --port $PORT`. Per N2, one worker keeps the in-memory store coherent. When we move to multi-worker (post-DB), the canonical path is `gunicorn -k uvicorn.workers.UvicornWorker -w N app:app`.

### 4.2 Folder layout

```
upsun-task-playground/
├── frontend/                     # was app.py + templates/ at root
│   ├── app.py
│   ├── templates/index.html
│   ├── pyproject.toml
│   └── uv.lock
├── admin/
│   ├── app.py                    # FastAPI app + routes + middleware wiring
│   ├── auth.py                   # password verification + login/logout helpers
│   ├── upsun_client.py           # async httpx wrapper: token fetch, trigger, poll, find env
│   ├── storage.py                # Run/Session in-memory store (interface)
│   ├── passwordhash.py           # CLI helper to print an argon2 hash
│   ├── templates/
│   │   ├── base.html
│   │   ├── login.html
│   │   └── chat.html
│   ├── static/                   # empty in v1; reserved
│   ├── pyproject.toml
│   └── uv.lock
├── coding-agent/                 # unchanged
├── .upsun/config.yaml            # frontend, admin, coding-agent
├── .github/workflows/ci.yml      # extended to lint admin/
├── README.md
├── SPEC.md
├── ITERATION-2.md                # this file
└── ...
```

### 4.3 Routes

Auth gating is enforced by the HTTP middleware in §4.5, not per-route — public paths are the explicit allowlist `{"/login", "/health"}` plus `/static/*`. The "Auth" column below documents the resulting behavior.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/` | yes | Redirects to `/chat` |
| GET | `/login` | no | Login form |
| POST | `/login` | no | Verify credentials, set session |
| POST | `/logout` | yes | Clear session, redirect to `/login` |
| GET | `/chat` | yes | Main UI (prompt textarea + recent runs panel) |
| POST | `/chat/runs` | yes | Submit a prompt; creates a `Run`, triggers task, returns the run-card HTML fragment for HTMX swap |
| GET | `/chat/runs/{run_id}` | yes | Returns the current run-card HTML fragment (HTMX polls this) |
| GET | `/health` | no | `{"status": "ok"}` |

### 4.4 Templates / UX

- `base.html` carries Tailwind, HTMX (`<script src="https://unpkg.com/htmx.org@2"></script>`), and a top bar (project name, logout button if authenticated).
- `login.html` is a centered card with username + password inputs and a single submit button.
- `chat.html`:
  - **Left column** (collapsed in v1, reserved for the session list): placeholder div with a comment marker — the future home of the "previous sessions" nav.
  - **Main column**: a prompt `<textarea>` + "Run" button. Below it, a list of recent runs (from `list_runs(session_id=current)`), each rendered as a card showing prompt, status, PR URL when known, preview URL when known, and an error message if the run failed.
  - Each in-flight run card has `hx-get="/chat/runs/{id}" hx-trigger="every 5s" hx-swap="outerHTML"`. Once terminal (`succeeded` or `failed`), the server omits the `hx-trigger` attribute and polling stops.

### 4.5 Auth implementation details

```python
# admin/app.py (excerpt)
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import os
from auth import verify_password

app = FastAPI()
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SECRET_KEY"],
    same_site="lax",
    https_only=True,
    max_age=int(os.environ.get("SESSION_LIFETIME_DAYS", "7")) * 86400,
)

PUBLIC_PATHS = {"/login", "/health"}

@app.middleware("http")
async def require_login(request: Request, call_next):
    p = request.url.path
    if p in PUBLIC_PATHS or p.startswith("/static/"):
        return await call_next(request)
    if not request.session.get("user"):
        return RedirectResponse(url="/login", status_code=302)
    return await call_next(request)

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    expected_user = os.environ.get("ADMIN_USERNAME", "admin")
    if username != expected_user or not verify_password(password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"},
            status_code=401,
        )
    request.session["user"] = username
    return RedirectResponse(url="/chat", status_code=303)
```

- `auth.py` exposes `verify_password(plain) -> bool` (reads `ADMIN_PASSWORD_HASH`, calls `argon2.PasswordHasher().verify`; returns False on `VerifyMismatchError`).
- Cookie attributes set on `SessionMiddleware` map directly to the Flask names from earlier: `same_site="lax"` ↔ `SAMESITE`, `https_only=True` ↔ `SECURE`. `HttpOnly` is the Starlette default.
- Login is rate-limited with a simple in-memory counter (e.g. 10 failed attempts per IP per 10 minutes → 429). Trivial; documented as not durable across restarts. Good enough for a single-tenant admin in v1.
- Lifetime defaults to 7 days (`SESSION_LIFETIME_DAYS`).

### 4.6 Upsun config

To add to `.upsun/config.yaml`:

```yaml
applications:
  frontend:
    source:
      root: /frontend
    type: "python:3.14"
    dependencies:
      python3:
        uv: "*"
    hooks:
      build: |
        set -eux
        uv sync --frozen --no-dev
    web:
      commands:
        start: ".venv/bin/gunicorn -b :$PORT app:app"
      locations:
        "/":
          passthru: true

  admin:
    source:
      root: /admin
    type: "python:3.14"
    dependencies:
      python3:
        uv: "*"
    hooks:
      build: |
        set -eux
        uv sync --frozen --no-dev
    authorizations:
      - type: task
        resource: coding-agent
        action: operate
      - type: env
        action: view
    variables:
      env:
        SESSION_LIFETIME_DAYS: "7"
    web:
      commands:
        start: ".venv/bin/uvicorn app:app --host 0.0.0.0 --port $PORT"
      locations:
        "/":
          passthru: true

routes:
  "https://{default}/":
    type: upstream
    upstream: "frontend:http"
  "https://admin.{default}/":
    type: upstream
    upstream: "admin:http"
```

The `coding-agent` task block stays as in iteration 1 (`source.root: /coding-agent`).

### 4.7 Environment variables

| Var | Type | Required | Notes |
|---|---|---|---|
| `ADMIN_USERNAME` | env | no | Defaults to `admin`. |
| `ADMIN_PASSWORD_HASH` | sensitive | yes | Argon2 hash. Generate with `uv run --directory admin python -m passwordhash <password>`. |
| `SECRET_KEY` | sensitive | yes | ≥32 random bytes for Starlette `SessionMiddleware` cookie signing. `python -c 'import secrets; print(secrets.token_hex(32))'`. |
| `SESSION_LIFETIME_DAYS` | env | no | Defaults to 7. |
| `UPSUN_API_TOKEN` | sensitive | local dev only | Optional override: if set, `upsun_client.py` skips the auth proxy and uses this PAT directly. Lets `uv run python app.py` work outside Upsun. **Not set in production.** |

**No project-id or target-env var is needed.** The runtime injects `PLATFORM_PROJECT` and `PLATFORM_BRANCH`; admin reads both directly. Target env defaults to `$PLATFORM_BRANCH` (the admin's own env, per D3). Bearer tokens are fetched from `http://localhost:8200/oauth2/token` (no env var either).

Per SPEC §7 Q6: project-level sensitive env vars don't reach a running container until the environment is redeployed. After creating `ADMIN_PASSWORD_HASH` or `SECRET_KEY`, redeploy `main` once before they're visible to the admin app.

### 4.8 Local run

The auth proxy at `localhost:8200` only exists inside Upsun containers. For local dev, set a user PAT in `UPSUN_API_TOKEN`; `upsun_client.py` detects it and skips the proxy.

```bash
cd admin
uv sync
ADMIN_PASSWORD_HASH="$(uv run --directory admin python -m passwordhash 'devpass')" \
SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
UPSUN_API_TOKEN="$UPSUN_API_TOKEN"            \
PLATFORM_PROJECT="vdaznsr6gfmd2"              \
PLATFORM_BRANCH="main"                        \
uv run uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

Local mode binds to `:8001` so it doesn't collide with `frontend` on `:8000`. `--reload` picks up template + code changes; drop it for a closer-to-prod local run.

---

## 5. Repo restructure (`frontend/` folder)

This is the iteration's one breaking change to existing files. Pending C2.

### 5.1 Moves

| From | To |
|---|---|
| `app.py` | `frontend/app.py` |
| `templates/index.html` | `frontend/templates/index.html` |
| `pyproject.toml` (root, runtime deps) | `frontend/pyproject.toml` |
| `uv.lock` (root) | `frontend/uv.lock` |

The repo root keeps its own `pyproject.toml` for shared dev tooling (`ruff`, `yamllint`) so `uv run ruff check .` from the root still lints everything.

### 5.2 Config & CI updates

- `.upsun/config.yaml`: rename application key `flask` → `frontend`; set `source.root: /frontend`.
- `.github/workflows/ci.yml`: `uv sync --frozen` runs in `frontend/`, `admin/`, and `coding-agent/`. Lint runs from the repo root and covers all three.
- `README.md`: update path references **and** add a top-level "Stack" section that names the framework + server for each app (per §3.1 table) and explains the deliberate Flask/FastAPI split. Add an "Admin app" section mirroring §1 of this doc with local-run instructions.
- `SPEC.md` §3.3: file-layout diagram updated to reflect the move (small follow-up edit; not done yet to keep the iter-1 record clean during planning).

### 5.3 Risk

The agent in iter 1 was given prompts that referenced "the homepage." After the rename, the homepage lives at `frontend/templates/index.html`. The agent's `list_dir` and `read_file` tools work on relative paths, so the agent will discover the new structure on its own. **No code change to `coding-agent/`.**

---

## 6. Trigger + monitoring lifecycle

### 6.1 Submit flow

1. User logs in, lands on `/chat`.
2. User types a prompt and submits.
3. Admin creates a `Run(id=uuid4, prompt, status='triggering', target_environment=$PLATFORM_BRANCH, created_at=now)` and stores it. `target_environment` is set per-run rather than baked-in so a future selector (§7.3) can override it.
4. Admin fetches (or reuses a cached) bearer token from the auth proxy at `localhost:8200/oauth2/token`.
5. Admin POSTs to `https://api.upsun.com/projects/$PLATFORM_PROJECT/environments/<target_env>/tasks/coding-agent/run` with `Authorization: Bearer <token>` and body `{"variables":{"env":{"AGENT_PROMPT":"..."}}}`.
6. On 202: parse `activity_id` from the response, update the `Run` to `status='running'`, persist `activity_id`.
7. On non-2xx: update the `Run` to `status='failed'`, store the response body as `error`. Render the failure card.
8. Render the run-card fragment; HTMX swaps it into the runs list.

### 6.2 Activity polling

- HTMX polls `/chat/runs/<id>` every 5s.
- The route fetches the activity (`GET /projects/.../activities/<activity_id>`) and updates the `Run`:
  - `state == "complete"` and `result == "success"` → `status='task_complete'`. Parse the activity log for `PR_URL=https://github.com/...` and `BRANCH=...` markers (we add those to the agent's stdout in §6.5).
  - `state == "complete"` and `result != "success"` → `status='failed'`, set `error`.
  - `state in {"pending", "in_progress"}` → unchanged.
- Once `status` is `task_complete`, the same poll endpoint transitions to environment polling.

### 6.3 Preview environment display (deferred to §7.3)

Originally this section polled the `pr-<number>` env for its public URL. That is blocked in v1 by env-scoped tokens (Q-iter2-8): the admin runs in `main` and cannot read a sibling env, and the URL hash is not reproducible from the name. So v1 does not surface the preview URL in the admin.

- When the task activity completes with `result == "success"`, admin fetches the log, extracts the `PR_URL` and `BRANCH` markers, and sets the run straight to `succeeded`. No preview-env read.
- The UI shows the PR link. The preview URL is reachable from the GitHub integration's PR comment.
- Surfacing the preview URL inside the admin returns once cross-env access exists (§7.3).

### 6.4 Status states & UI

| `status` | UI | HTMX polling |
|---|---|---|
| `triggering` | "Starting…" spinner | every 5s |
| `running` | "Agent is working…" + activity link | every 5s |
| `succeeded` | "Done." + PR link | stopped |
| `failed` | Red banner, error message, retry button | stopped |

(`task_complete` was removed with Q-iter2-8: there is no preview-build step the admin can observe, so the run transitions `running → succeeded` directly. The **Open preview** button returns with §7.3.)

### 6.5 Required change to the agent

To make the activity log machine-parseable, the agent prints two marker lines at the end of its run:

```
BRANCH=coding-1a2b3c-change-the-headline
PR_URL=https://github.com/nicogommen/upsun-task-playground/pull/42
```

`PR_URL` is already printed today; `BRANCH` is new. The format becomes a contract — both lines must appear, exactly one per line, on stdout, no trailing whitespace. Tiny change to `coding-agent/run.py`.

---

## 7. Forward-looking architecture

These aren't built in v1 but the v1 design must not block them.

### 7.1 Sessions & history (DB schema sketch)

Even though v1 is in-memory, we design the data model now so the future move is a one-file swap:

```python
@dataclass
class Session:
    id: str             # uuid
    created_at: datetime
    title: str          # first prompt's first 60 chars; powers the left-nav

@dataclass
class Run:
    id: str             # uuid
    session_id: str
    prompt: str
    status: Literal["triggering","running","task_complete","succeeded","failed"]
    activity_id: str | None
    target_environment: str
    branch_name: str | None
    pr_url: str | None
    preview_env_id: str | None
    preview_url: str | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None
```

When we add **Postgres** (planned iter 2.x or iter 3 — per D7, Postgres not SQLite), `storage.py` swaps from a dict to two tables matching this dataclass, and admin gains a Postgres `relationships:` block in `.upsun/config.yaml` (likely paired with `psycopg`/`asyncpg`). The route layer doesn't change.

### 7.2 Streaming logs (SSE upgrade path)

HTMX polling is a v1 stand-in. FastAPI streams natively via `StreamingResponse` over an async generator, so the upgrade is mechanical:

1. Add a route `GET /chat/runs/{id}/events` that returns `StreamingResponse(generator(), media_type="text/event-stream")`.
2. Server-side, the generator tails the activity log via the Upsun API (incremental fetch by offset) and yields `data: ...\n\n` frames.
3. Client-side, swap the run card to use `hx-ext="sse"` + `sse-connect` instead of `hx-trigger="every"`.

This is one of the main reasons we picked FastAPI in N4. No framework swap required.

### 7.3 Cross-env triggering (deferred)

The auth-proxy issues env-scoped tokens by design ("a token issued in one environment cannot act on another", per the [authorizations doc](https://upsun-c9761871-492-new-task-container.mintlify.app/docs/configure-apps/image-properties/authorizations)). v1 lives with that: each admin triggers in its own env.

Use cases we'd like to keep open for later:

- **Admin-on-preview → arbitrary preview env**: pick which preview to test a prompt against from a single admin instance.
- **Admin-on-main → preview env**: production admin nudges a specific preview env to verify a fix.
- **Admin-on-X → admin-on-Y delegation**: the user picks env Y in the UI; the X admin asks the Y admin to run the task locally in Y. Y is the one that actually issues the task call from its own auth proxy.

Candidate paths to unlock this without compromising the "no long-lived PAT in production" rule (D1):

1. **Admin-to-admin delegation over the public URL** (probably the cleanest): admin-X POSTs `{prompt, idempotency_key}` to `https://admin.<envY>.…/internal/runs`, signed with a shared `INTERNAL_HMAC_KEY`. Admin-Y verifies the signature, then triggers the task from its *own* env-scoped token. Each admin keeps the proxy-only contract; the cross-env hop is just an HTTPS call between two admins.
2. **Upsun adds cross-env scopes to `authorizations`**: schema change on the platform side. Asked the engineering team (2026-06-05) whether a `type: project` authorization can be added (today only `type: task` and `type: env` exist, both env-scoped). If it ships, this becomes the cleanest unlock for both cross-env triggering and the preview-URL read (see §7.4). Tracking, out of our control until then.
3. **Per-environment service account PATs** stored as project-scoped sensitive vars: weaker security posture, sidesteps the proxy entirely. Avoid unless 1 and 2 prove infeasible.

The v1 data model already accommodates all three: `Run.target_environment` is a per-run string, and `upsun_client.trigger_task(env_id, ...)` takes the env id as a parameter rather than a class-level constant. No code in the route layer or storage layer assumes "self only".

### 7.4 Preview URL in the admin (pick up when `type: project` lands)

v1 does not surface the preview URL because reading the sibling `pr-<number>` env is a cross-env read the env-scoped token can't do (Q-iter2-8). The blocker is the same `authorizations` scope limit as §7.3, so the unlock is the same: a project-scoped authorization. We've asked engineering for `type: project` (see §7.3, candidate 2). When it ships, restore the feature:

1. Add the new authorization to `admin` in `.upsun/config.yaml`, e.g.

   ```yaml
   authorizations:
     - type: task
       resource: coding-agent
       action: operate
     - type: project
       action: view   # confirm the exact action name against the shipped schema
   ```

2. Confirm the project-scoped token can `GET /projects/{id}/environments/pr-<number>` (and ideally list `/environments`) from the admin's own env, the call that returns 403 today.
3. Reinstate the code removed in commit `8648058` (revert is the fastest start): the `task_complete` state, `_poll_preview_env`, `_preview_env_id` + `_PR_NUMBER_RE` in `admin/app.py`, `UpsunClient.get_environment` in `admin/upsun_client.py`, the `preview_env_id`/`preview_url` fields in `admin/storage.py`, and the `task_complete` + "Open preview" blocks in `admin/templates/_run_card.html`.
4. Re-point §6.3 and the §6.4 status table back to the preview-polling flow, and mark Q-iter2-8 superseded.

Reading the preview env gives the public URL at `env["_links"]["public-url"]["href"]` once `env["status"] == "active"` (verified via the CLI: `pr-4` resolved to `https://pr-4-afnwgxy-vdaznsr6gfmd2.eu-3.platformsh.site/`). The env id is `pr-<number>`, parsed from the agent's `PR_URL` marker (Q-iter2-1).

---

## 8. Success criteria

- `https://admin.<env>.<region>.platformsh.site/` redirects an anonymous request to `/login`.
- Logging in with the right credentials lands on `/chat`. Wrong credentials show an error and don't set a session. Logout clears the session.
- Submitting a prompt creates a run card that progresses through `triggering → running → task_complete → succeeded` (or `failed`) in real time (≤30 s perceived latency to the first state change after submit).
- On `succeeded`, the card shows a clickable PR link **and** a preview URL; the preview URL serves the modified version of the homepage.
- CI lints both `admin/` and `frontend/`. Both apps deploy via `uv sync --frozen --no-dev` and pass the build hook.
- The Upsun activity log for the task still contains the agent's full reasoning trace, plus the new `BRANCH=` line and the existing `PR_URL=` line.

---

## 9. Open questions / findings

To be filled in as we build, in the same shape as SPEC §7. Likely candidates:

- **Q-iter2-1.** *Resolved (2026-06-05), and it changes the §6.3 plan.* Branch matching is the wrong approach. When the GitHub integration (with `build_pull_requests`) processes a PR, it creates an **active** environment keyed `pr-<number>` (parent `main`, title "PR #N: ..."). The branch push separately creates an environment named after the branch (`coding-...`) that stays **inactive**. So matching the env by branch name finds the dead env, not the live preview. The reliable key is the PR number: parse it from the agent's `PR_URL` marker (`/pull/(\d+)`), the preview env id is `pr-<number>`, and the admin reads that env directly. The public URL is `env._links["public-url"]["href"]`, and readiness is `env["status"] == "active"`. This replaces `find_env_by_branch` (removed) with `get_environment(env_id)`.
- **Q-iter2-2.** Whether single-worker is enforceable via Upsun config or only by how we invoke `uvicorn` in the start command. (Plain `uvicorn app:app` defaults to one worker, so this is mostly a doc question.)
- **Q-iter2-3.** Whether project-level sensitive env vars set on `main` are visible to a preview env's `admin` app, or whether each preview env needs its own (likely the former — but worth confirming).
- **Q-iter2-4.** *Resolved.* The auth proxy follows the OAuth2 standard response shape: `{"access_token", "expires_in", "token_type"}` with a 900s expiry. Documented in the [authentication doc](https://developer.upsun.com/api/rest/authentication.md) and confirmed for the proxy by the [authorizations doc](https://upsun-c9761871-492-new-task-container.mintlify.app/docs/configure-apps/image-properties/authorizations). `expires_in` is authoritative — trust it over the requested `x-token-ttl`.
- **Q-iter2-5.** *Resolved against C3.* Auth-proxy tokens are env-scoped by design: "a token issued in one environment cannot act on another" ([authorizations doc](https://upsun-c9761871-492-new-task-container.mintlify.app/docs/configure-apps/image-properties/authorizations)). v1 admin triggers in its own env (D3); cross-env triggering moves to §7.3.
- **Q-iter2-6.** *Resolved (2026-06-05).* The trigger 202 body is `{"status": "created", "_embedded": {"activities": [{...}]}}` (GIT-857). The activity id is at `_embedded.activities[0].id`, not a top-level `id` or `activity.id`. The original parse guessed the latter, so `activity_id` was always `None` and polling never started (runs stuck on "running" forever). Fixed in step 6a, confirmed live.
- **Q-iter2-7.** *Resolved (2026-06-05).* The task stdout is **no longer inline on the activity object**. `GET .../activities/{id}` returns metadata only: `state`, `result`, `text` (the human description, not stdout), and a deprecated `log` placeholder ("available in the streaming logs endpoint. Update your API client."). The real log is a separate call: `GET /projects/{project}/environments/{env}/activities/{id}/log?start_at=0&max_items=0&max_delay=-1` (the env-scoped and project-level paths both return 200). Response is `application/x-json-stream` (NDJSON): one `{"_id": N, "data": {"timestamp", "message"}}` per line, terminated by `{"_id": N, "seal": true}`. `max_delay=-1` returns immediately rather than long-polling. Consequence: PR/branch marker extraction needs this second call and must reassemble the `data.message` fields, then run the regexes. The old `_activity_log()` that read fields off the activity object could never have worked.
- **Q-iter2-8.** *Resolved (2026-06-05) — changes §6.3.* The preview-env read (`GET .../environments/pr-<number>`) is a **cross-environment** call, and the auth-proxy token is env-scoped (Q-iter2-5). From the admin running in `main` it returns **403 Forbidden**, not the `404` §6.3 assumed. So §6.3 contradicted Q-iter2-5: it needed to read a sibling env (`pr-N`) the env-scoped token cannot reach. Confirmed live: every poll of `pr-4` logged `403 Forbidden` and the card stayed stuck on "Agent finished, building preview…" forever (the poll loop swallows `HTTPError` and keeps polling). Mapped the token's reach from inside the admin container with a proxy-minted token: list `/environments` → 403, GET own env `main` → 200, GET sibling `pr-4` → 403. The token can read its own env only. Resolution paths considered: (a) a project-scoped PAT in `UPSUN_API_TOKEN` (the existing `pat` path bypasses the proxy) — no PAT available, and it would put a long-lived secret in prod. (b) *Ruled out.* Deduce the URL from the env name — the middle hostname token is a per-environment hash, not the name (`main-bvxea6i` vs `pr-4-afnwgxy`), deterministic but undocumented, so not reproducible client-side. (c) **Chosen for v1.** Drop the preview URL: when the task completes successfully the run goes straight to `succeeded` with the PR link only. The GitHub integration's PR comment carries the preview URL until cross-env access lands in §7.3. This removes the `task_complete` state, `_poll_preview_env`, `_preview_env_id`, and `UpsunClient.get_environment`. Verified live (2026-06-05): a fresh prompt ran `running → succeeded` and the card landed on "Done" with the PR link, no hang. To restore the preview URL once a project-scoped authorization exists, see §7.4 (a `type: project` authorization has been requested from engineering).
- **Finding (informational).** The new task container is in **private beta** and requires a per-project support ticket to enable. Worth flagging in the playground README so anyone trying to reproduce this setup knows to request enablement first.
