# upsun-task-playground

Test bed for the new Upsun **task container** and for running AI agents inside it. A user opens the `admin` web UI, types a natural-language prompt, and a `coding-agent` task edits the codebase and opens a pull request, which Upsun builds as an active preview environment via the GitHub integration. The `frontend` app is the demo target the agent changes. Deployed on Upsun project `vdaznsr6gfmd2`.

Full design and findings: [SPEC.md](./SPEC.md) (iteration 1) and [ITERATION-2.md](./ITERATION-2.md) (the admin UI).

> **Private beta.** The Upsun task container is in private beta and must be enabled per project via a support ticket. Request enablement before trying to reproduce this setup, otherwise the `tasks.coding-agent` block and the trigger API will not be available.

## Architecture (multi-stack)

The project deliberately mixes runtimes to exercise Upsun's multi-stack story. Documenting that split is part of the deliverable (per decision D4 / N4).

| App | Framework | Server | Why this stack |
|---|---|---|---|
| `frontend` | Flask 3.1 (WSGI) | gunicorn | The demo target. Small Jinja templates, Tailwind via CDN, no JS toolchain. |
| `admin` | FastAPI (ASGI) | uvicorn | Async-native and a natural fit for the chat-style trigger UI and a future SSE log stream. |
| `coding-agent` (task) | stdlib + `anthropic` SDK | n/a (run-to-completion) | Minimal, no web layer. Triggered by the admin or the API. |

All three share Python 3.14 and uv, so the build (`uv sync --frozen`) and CI lints (`ruff`, `yamllint`) stay uniform even though the frameworks differ. Each app lives in its own folder and declares its own `applications` or `tasks` block in `.upsun/config.yaml`.

## Local run

Uses [uv](https://docs.astral.sh/uv/) for dependency management. Each app is its own uv project.

```bash
# Frontend (the demo Flask app)
uv sync --directory frontend
uv run --directory frontend python app.py    # http://localhost:8000

# Dev tooling (ruff, yamllint) lives at the repo root
uv sync                                       # installs ruff + yamllint
```

The `admin` app needs a few environment variables. Locally it skips the in-container OAuth2 proxy and talks to the Upsun API with a user PAT in `UPSUN_API_TOKEN`. Set `SESSION_COOKIE_SECURE=false` so the session cookie is accepted over plain http.

```bash
# Admin (FastAPI trigger UI)
uv sync --directory admin
ADMIN_PASSWORD_HASH="$(uv run --directory admin python -m passwordhash 'devpass')" \
SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
SESSION_COOKIE_SECURE=false \
UPSUN_API_TOKEN="$UPSUN_API_TOKEN" \
PLATFORM_PROJECT="vdaznsr6gfmd2" \
PLATFORM_BRANCH="main" \
uv run --directory admin uvicorn app:app --port 8001 --reload   # http://localhost:8001
```

Then log in at http://localhost:8001 with username `admin` (override via `ADMIN_USERNAME`) and the password you hashed above. The full env-var reference is in [ITERATION-2.md §4.7](./ITERATION-2.md).

## Endpoints

Frontend:

- `GET /` — homepage (Jinja template, Tailwind via CDN)
- `GET /health` — JSON health check

Admin (login required, except `/login` and `/health`):

- `GET /chat` — the prompt input and run history
- `POST /chat/runs` — submit a prompt, triggers `coding-agent`
- `GET /chat/runs/{id}` — HTMX poll endpoint for a single run's status
- `GET|POST /login`, `POST /logout`, `GET /health`

## Lint and format

```bash
uv run ruff check .
uv run ruff format .
uv run yamllint .
```

CI runs the same set on every push and PR. See `.github/workflows/ci.yml`.

## Deploy

Commits to `main` push to GitHub. Upsun mirrors via the connected integration and runs the build defined in `.upsun/config.yaml` (Python 3.14, uv-managed venv). Both `frontend` (gunicorn) and `admin` (uvicorn) redeploy. Admin run history is in-memory in v1, so a redeploy clears it (the Postgres swap is deferred, see ITERATION-2 §7.1).

## Coding-agent task

The admin UI is the primary way to trigger a run. The same task is also reachable directly through the API:

```bash
upsun e:curl -p vdaznsr6gfmd2 -e main tasks/coding-agent/run \
  -X POST \
  -d '{"variables":{"env":{"AGENT_PROMPT":"<your instruction>"}}}'
```

To override the model on a single run, add `AGENT_MODEL` to the same envelope:

```bash
upsun e:curl -p vdaznsr6gfmd2 -e main tasks/coding-agent/run \
  -X POST \
  -d '{"variables":{"env":{"AGENT_PROMPT":"...","AGENT_MODEL":"claude-sonnet-4-6"}}}'
```

The agent edits the repo, pushes a branch named `coding-<6 hex>-<slug>`, and opens a PR on GitHub. The PR auto-builds an active preview environment. The agent prints two machine-readable markers on stdout (`BRANCH=...` and `PR_URL=...`) that the admin parses to surface the PR link. See [SPEC.md §4](./SPEC.md) for the full contract and [ITERATION-2.md §6.5](./ITERATION-2.md) for the marker format.

The folder name is the agent name. Adding more agents later (e.g. `review-agent/`, `test-agent/`) means dropping a new folder and a new `tasks.<name>` block alongside this one.
