# Upsun Task Playground — Specification

**Purpose.** Hands-on test bed for the new Upsun **task container** and for running AI agents inside it. Used to validate the outcomes defined in the [Run Background Agents on Upsun Cloud](https://linear.app/platformsh/project/run-background-agents-on-upsun-cloud-33a30afc5ff8) project brief.

**Status.** Iteration 1 completed end-to-end on 2026-05-08 (PR auto-built into an active preview environment serving the agent's change). Iteration 2 (the admin UI) shipped its core lifecycle on 2026-06-05, with one item deferred: see [ITERATION-2.md](./ITERATION-2.md) for what landed and §7.4 there for what is parked. Living document — later iterations extend the spec.

**Project on Upsun.** `upsun-task-playground` (`vdaznsr6gfmd2`), region `eu-3`, default branch `main`, connected to GitHub repo `nicogommen/upsun-task-playground` via the GitHub integration.

---

## 1. Iteration plan

We grow the playground in small, demonstrable increments. This document specifies **Iteration 1** in detail. Later iterations are listed for context but are not yet committed.

| # | Status | Title | Outcome |
| - | ------ | ----- | ------- |
| **1** | **Done (2026-05-08)** | **Visual Flask app + minimal coding-agent task** | Agent receives a prompt, makes the change, opens a PR, Upsun builds an active preview environment from the PR. |
| **2** | **Done (2026-06-05) — see [ITERATION-2.md](./ITERATION-2.md)** | **Admin UI for triggering the agent** | An authenticated admin web app lets a user submit prompts that trigger `coding-agent` (using `authorizations` + the per-container auth proxy, no user PAT) and surfaces the resulting PR. The preview environment URL is **deferred**: the admin's env-scoped token cannot read the sibling preview env, so it returns once a project-scoped authorization ships (ITERATION-2 §7.4). |
| 2.x | **In progress (2026-08-16) — see [ITERATION-2.x.md](./ITERATION-2.x.md)** | Persistent chat history (Postgres) | Move admin storage from in-memory to Postgres so sessions and runs survive a redeploy, plus the chat-history left-nav and the session lifecycle it needs (D15, which supersedes D13). The single-worker constraint is released but not exercised (D10, see ITERATION-2.x §4). |
| 3 | Pending | Verification step | Agent waits for the preview deploy and curls a URL to confirm the change is live. |
| 4 | Pending | Sandbox restrictions | Outbound firewall + bubblewrap layered onto the task container. |
| 5 | Pending | Sub-agents and parallelism | One agent task launches and orchestrates others. |

Detailed forward-looking notes for each pending iteration: [FUTURE-ITERATIONS.md](./FUTURE-ITERATIONS.md).

**Off the arc:** [EXPORT-TASK.md](./EXPORT-TASK.md) covers the `export-job` task, a second task container added purely to demonstrate that a task is general-purpose on-demand compute rather than an agent runtime. It is a demo side-track and is deliberately not an iteration row, so this table keeps meaning what it says.

---

## 2. Iteration 1 — Visual Flask app + minimal coding-agent task

### 2.1 Goals

- A small Flask app with an actual UI (homepage with demo content) so visual changes are obvious.
- An Upsun `task` container named **`coding-agent`** that, given a natural-language prompt, edits the codebase to satisfy that prompt, pushes the result to a new branch, and opens a pull request on GitHub.
- The PR triggers an **active** preview environment via the GitHub integration's `build_pull_requests` setting. **The agent's job ends after the PR is opened.**

The folder name (`coding-agent/`) and task name in the config are deliberately specific so future iterations can add other agent types (e.g. `review-agent/`, `test-agent/`) by dropping a new folder + a new `tasks.<name>` block alongside the existing one.

### 2.2 Out of scope for Iteration 1

- Verifying the deploy succeeded.
- Checking the change is visually correct.
- Sandboxing (firewall, bubblewrap).
- App-to-task triggering (`PLATFORM_TASK_TOKEN`, `triggers:` on the app).
- Sub-agents, parallel runs, cancellation.
- A UI for invoking the agent. Triggering is done from a terminal.

### 2.3 Success criteria

- Running `upsun e:curl tasks/coding-agent/run -X POST -d '{"variables":{"env":{"AGENT_PROMPT":"..."}}}'` creates an activity that completes without error.
- A new branch named `coding-<6 hex>-<slug>` (≤39 chars, no slashes) appears on GitHub.
- A pull request against `main` is opened automatically by the agent.
- The GitHub integration builds the PR as an **active** preview environment (`build_pull_requests`).
- The preview environment URL serves the modified version of the homepage.
- The task activity log contains a readable trace of the agent's reasoning, tool calls, and the PR URL.

---

## 3. Application specification (`frontend`)

The app was named `flask` in iteration 1 and renamed to `frontend` at the start of iteration 2 (see [ITERATION-2.md §5](./ITERATION-2.md) for the move). Behavior is unchanged.

### 3.1 Pages & routes

| Path | Description |
| ---- | ----------- |
| `GET /` | Homepage. Single page with a hero section, a short feature list, and a footer. |
| `GET /health` | JSON `{"status": "ok"}`. Existing, do not remove. |

The homepage exists so that prompts like "change the headline" or "add a features card" produce **visible** changes in the preview environment.

### 3.2 Tech stack

- Python 3.14, Flask 3.1.x, Jinja2 templates.
- Tailwind via CDN (`<script src="https://cdn.tailwindcss.com"></script>`) — no JS build step.
- Gunicorn 26.x as the production WSGI server (already in place).

Tailwind via CDN is deliberate: it avoids a Node toolchain in the repo and keeps the diff for an agent-driven change tiny and human-readable (one HTML file).

### 3.3 File layout (current, after Iteration 2)

```
upsun-task-playground/
├── frontend/
│   ├── app.py              # Flask app, renders templates/index.html
│   ├── pyproject.toml      # Flask + gunicorn (runtime deps for the frontend app)
│   ├── uv.lock             # Resolved versions for the frontend app
│   ├── static/
│   │   ├── favicon.svg     # Upsun mark, prefers-color-scheme aware
│   │   └── favicon-32.png  # Fallback for browsers without SVG favicon support
│   └── templates/
│       └── index.html      # Homepage (hero, features, footer)
├── admin/
│   ├── app.py              # FastAPI app: routes, auth middleware, run lifecycle
│   ├── auth.py             # argon2 password verification
│   ├── storage.py          # Run store: Postgres, or in-memory when unconfigured (ITERATION-2.x)
│   ├── schema.sql          # DDL applied at startup by storage.connect()
│   ├── upsun_client.py     # Async httpx wrapper: proxy token, trigger, activity log
│   ├── passwordhash.py     # CLI helper printing an argon2 hash
│   ├── pyproject.toml      # FastAPI + uvicorn[standard] + httpx + argon2-cffi
│   ├── uv.lock             # Resolved versions for the admin app
│   ├── static/             # favicon.svg + favicon-32.png (violet variant of the mark)
│   └── templates/
│       ├── base.html       # Shell: Tailwind, HTMX, top bar, favicons
│       ├── login.html
│       ├── chat.html
│       └── _run_card.html  # Run-card fragment swapped in by HTMX
├── coding-agent/
│   ├── run.py              # Task entry point — the agent loop
│   ├── tools.py            # Tool definitions exposed to the LLM
│   ├── pyproject.toml      # anthropic SDK
│   └── uv.lock             # Resolved versions for the task
├── pyproject.toml          # Shared dev tooling only (ruff, yamllint)
├── uv.lock                 # Resolved versions for the dev tooling
├── .upsun/
│   └── config.yaml         # frontend + admin apps + coding-agent task
├── .github/workflows/
│   └── ci.yml              # ruff + yamllint pipeline
├── .yamllint.yaml          # YAML lint config
├── README.md
├── SPEC.md                 # this file
├── ITERATION-2.md          # in-flight iteration detail
├── FUTURE-ITERATIONS.md    # forward-looking notes for iter 2.x, 3, 4, 5
└── .gitignore
```

`admin/` is fully built as of iteration 2: login, session middleware, the chat UI, the run lifecycle, and the Upsun client are all in place and deployed (see [ITERATION-2.md §4](./ITERATION-2.md)).

### 3.4 Homepage content (Iteration 1 baseline)

The agent will modify this content; the baseline is intentionally generic so changes are easy to compare.

- Hero: headline "Upsun Task Playground", subtitle "A playground for running AI agents on Upsun's task containers.", primary CTA button "Read the docs" linking to `https://upsun.com`.
- Features section: three cards titled "Tasks", "Agents", "Sandboxes" with a one-sentence description each.
- Footer: project name + link to the GitHub repo.

### 3.5 Upsun config — `frontend`

In `.upsun/config.yaml`. Python 3.14, uv-managed venv (`dependencies.python3.uv: "*"` bootstraps uv at build, `uv sync --frozen --no-dev` installs the locked deps), `.venv/bin/gunicorn -w 2 -b localhost:$PORT` as the start command. `source.root: /frontend` since iter 2 step 1.

Three routes are declared: `https://{default}/` to `frontend`, `https://admin.{default}/` to `admin` (iter 2, see [ITERATION-2.md §3.2](./ITERATION-2.md)), and `https://www.{default}/` as a redirect to the apex.

### 3.6 Tooling and CI

The playground uses [uv](https://docs.astral.sh/uv/) end-to-end (build, runtime, local dev). Lint and format are enforced via [ruff](https://docs.astral.sh/ruff/) (Python) and [yamllint](https://yamllint.readthedocs.io/) (YAML) — both installed as dev deps in the **root** `pyproject.toml`. App-specific runtime deps live in each app's own `pyproject.toml` (`frontend/`, `admin/`, `coding-agent/`).

CI runs on every push and PR via `.github/workflows/ci.yml`:

1. `uv sync --frozen` for each project in turn: root tooling, `frontend/`, `admin/`, `coding-agent/`
2. `uv run ruff check .`
3. `uv run ruff format --check .`
4. `uv run yamllint .`

Local equivalents:

```bash
uv run ruff check .
uv run ruff format .
uv run yamllint .
```

Lock files (`uv.lock`, `frontend/uv.lock`, `admin/uv.lock`, `coding-agent/uv.lock`) are committed for reproducible builds. The Upsun build hooks use `--frozen` so a missing or outdated lock fails the build instead of silently resolving fresh.

---

## 4. Task specification (`coding-agent`)

### 4.1 Concept

An Upsun `task` container named `coding-agent` that runs the **agent runtime**. The agent runtime is a Python script using the Anthropic SDK with tool use. The task is triggered via the Upsun task-trigger API ([GIT-857](https://linear.app/platformsh/issue/GIT-857/add-task-trigger-api-endpoint)); the prompt is passed in the trigger payload under `variables.env.AGENT_PROMPT`. After making the change the agent opens a pull request, which the GitHub integration builds as an active preview environment.

The agent is one of potentially many — its folder is `coding-agent/` and the task is `tasks.coding-agent`. Future agents (e.g. `review-agent`, `test-agent`) sit alongside it under their own `<name>/` folders and `tasks.<name>` blocks.

### 4.2 Inputs

| Input | Source | Notes |
| ----- | ------ | ----- |
| **`AGENT_PROMPT`** | **Trigger payload** (`variables.env.AGENT_PROMPT`) | The natural-language instruction. Per [GIT-857](https://linear.app/platformsh/issue/GIT-857/add-task-trigger-api-endpoint), variables under `variables.env.<NAME>` land as plain env vars in the task process. Confirmed empirically. |
| `AGENT_MODEL` | Trigger payload (`variables.env.AGENT_MODEL`) **or** project env var | Optional. Anthropic model ID. If unset, defaults to `DEFAULT_MODEL` in `coding-agent/run.py` (currently `claude-haiku-4-5-20251001`). Trigger-payload value wins per run; project env var is a global override. |
| `GITHUB_REPO` | Project env var (runtime-visible) | Required. Form: `<owner>/<name>` (e.g. `nicogommen/upsun-task-playground`). Used for both the clone URL and the PR API call. Replaces the previously hardcoded repo. |
| `ANTHROPIC_API_KEY` | Project sensitive env var (runtime-visible) | Bring-your-own LLM key. Requires a redeploy of `main` after creation for the value to reach a running task (see §7 Q6). |
| `GITHUB_TOKEN` | Project sensitive env var (runtime-visible) | Fine-grained PAT on the configured `GITHUB_REPO` with **`Contents: read+write`** *and* **`Pull requests: read+write`**. Used to push the branch and open the PR. |
| `GIT_USER_NAME`, `GIT_USER_EMAIL` | Project env vars | Commit author. Defaults: `upsun-task-playground-coding-agent` / `coding-agent@playground.local`. |

`resolve_prompt()` reads `os.environ["AGENT_PROMPT"]` directly. Earlier drafts of the runtime had a probe ladder for hypothetical alternative payload mechanisms (`*TASK*INPUT*` env vars, well-known JSON files); we removed it once §7 Q1 was resolved, since none of those paths ever fired in practice.

### 4.3 Lifecycle

1. Upsun creates a fresh container with the task's slug.
2. Container starts, working directory is the task source root (`/app`); the repository contents are available (the slug, no `.git`).
3. The agent runtime parses `GITHUB_REPO` into owner/name and clones the repo fresh (`git clone` over HTTPS using `GITHUB_TOKEN`) into `/tmp/work` so it has full git history and a writable tree.
4. Agent creates a branch `coding-<6 hex>-<slug>` (≤39 chars, no `/`).
5. Agent runs the LLM loop until either: the LLM emits a `stop_reason: end_turn`, the loop hits the iteration limit (default 25), or the timeout expires.
6. Agent commits and pushes the branch.
7. Agent opens a pull request against `main` via the GitHub REST API (stdlib `urllib`, no extra deps). The PR title encodes the prompt; the body links the prompt and the source.
8. The GitHub integration builds the PR as an active preview environment (`build_pull_requests`).
9. Container exits.

### 4.4 Container config

Added to `.upsun/config.yaml`:

```yaml
tasks:
  coding-agent:
    source:
      root: /coding-agent
    type: "python:3.14"
    hooks:
      build: |
        set -eux
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        uv sync --frozen
    run:
      command: ".venv/bin/python run.py"
      timeout: 900
    # Added after iteration 1 (PCO-695): lets the auth proxy at localhost:8200
    # mint an env/view token from inside the task. Not used by the iteration 1
    # flow; it is the groundwork for the iteration 3 verification step.
    authorizations:
      - type: env
        action: view
    mounts:
      "/tmp":
        source: "tmp"
```

- `source.root: /coding-agent` keeps the task's code separate from the Flask app and from any future agent folders.
- **uv install path differs from the flask app — and not by choice.** The task validator rejects both `dependencies` (the app's path) and `stack` (the composable image's path) with `Unknown key`, even with the task capability enabled. Astral's official installer is the only pip-free option that works today. The flask app stays on `dependencies.python3.uv: "*"`. See §7 Q5 — both gaps are real findings from this experiment, to be raised with the schema owners.
- `uv sync --frozen` installs the locked deps from `coding-agent/uv.lock`. No `--no-dev` here because the task has no dev-only deps.
- Ruff's `target-version` is intentionally pinned to `py313` even though both apps deploy on Python 3.14. The newer "unparenthesized except clauses" syntax is a 3.14-only feature; pinning ruff lower keeps the source portable and prevents `ruff format` from stripping parens that would then break a 3.13 fallback. Cheap insurance.
- `timeout: 900` (15 min) is enough for an iteration-1 prompt; raise later if needed.
- `tmp` mount gives the agent a workspace for cloning and editing.
- No `relationships:` declared. Iteration 1 doesn't talk to any service.
- No `triggers:` on the `frontend` app (named `flask` at the time of iteration 1). In iteration 1 the trigger comes from a user-level token. Iteration 2 replaced that with the `admin` app calling the trigger API under its own `authorizations`, still without a user PAT (see [ITERATION-2.md §3.4](./ITERATION-2.md)).

### 4.5 Agent runtime

Located at `coding-agent/run.py`. Pseudocode (the contract; see the file for the full implementation):

```python
import os, secrets, subprocess, anthropic, urllib.request
from tools import TOOLS, dispatch_tool

DEFAULT_MODEL = "claude-haiku-4-5-20251001"  # latest haiku — cheapest default
BRANCH_PREFIX = "coding"

owner, name = get_repo()                          # parse GITHUB_REPO env var
model = os.environ.get("AGENT_MODEL") or DEFAULT_MODEL
prompt = resolve_prompt()                          # variables.env.AGENT_PROMPT

# 1. Clone fresh repo with credentials
workdir = "/tmp/work"
clone_url = f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/{owner}/{name}.git"
subprocess.run(["git", "clone", clone_url, workdir], check=True)

# 2. Branch name: coding-<6hex>-<slug<=25>, total <=39 chars, no slashes
branch = f"{BRANCH_PREFIX}-{secrets.token_hex(3)}-{slugify(prompt)}"
subprocess.run(["git", "-C", workdir, "checkout", "-b", branch], check=True)

# 3. Configure git author, run the LLM loop with `model`, commit, push.
# (25 turn limit, stop on end_turn) ...

# 4. Open the PR via GitHub REST API (stdlib urllib)
urllib.request.Request(
    f"https://api.github.com/repos/{owner}/{name}/pulls",
    data=json.dumps({"title": ..., "head": branch, "base": "main", "body": ...}).encode(),
    headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", ...},
)
```

### 4.6 Tool surface

The LLM gets four tools, defined in `coding-agent/tools.py`. Minimal by design — small surface area, easy to audit.

| Tool | Purpose | Inputs |
| ---- | ------- | ------ |
| `read_file` | Read a file from the working tree | `path` (relative to repo root) |
| `list_dir` | List the contents of a directory | `path` |
| `write_file` | Overwrite a file | `path`, `content` |
| `run_bash` | Run a shell command in the work tree | `command` (string) |

Edits-only via `write_file` (no diff/patch tool) keeps the loop simple. `run_bash` is the escape hatch — the agent can do anything else through it (e.g. `python -m py_compile`, `grep`).

### 4.7 Outputs

- **Stdout / stderr** of the task is captured by Upsun and surfaced via `upsun activity:log <id>`. This is the **audit trail** for Iteration 1.
- **Branch on GitHub** named `coding-<6 hex>-<prompt-slug>` (≤39 chars, no slashes).
- **Pull request** opened against `main`, with the prompt as title and body.
- **Active preview environment** built automatically from the PR by Upsun's GitHub integration (`build_pull_requests`).
- **PR URL** printed to stdout at the end of the task log.

### 4.8 Trigger

CLI form (the user, with their CLI session):

```bash
# Trigger with the prompt in the variables.env envelope
upsun e:curl -p vdaznsr6gfmd2 -e main tasks/coding-agent/run \
  -X POST \
  -d '{"variables":{"env":{"AGENT_PROMPT":"Change the homepage headline to Hello from an agent"}}}'

# Override the default model on a single run
upsun e:curl -p vdaznsr6gfmd2 -e main tasks/coding-agent/run \
  -X POST \
  -d '{"variables":{"env":{"AGENT_PROMPT":"...","AGENT_MODEL":"claude-sonnet-4-6"}}}'

# Watch
upsun activity:list -p vdaznsr6gfmd2 -e main --limit 5
upsun activity:log <ID>
```

Direct API form (equivalent):

```bash
curl -X POST \
  -H "Authorization: Bearer $UPSUN_TOKEN" \
  https://api.upsun.com/projects/vdaznsr6gfmd2/environments/main/tasks/coding-agent/run \
  -d '{"variables":{"env":{"AGENT_PROMPT":"..."}}}'
```

The trigger response is a 202 with the activity embedded — see [GIT-857](https://linear.app/platformsh/issue/GIT-857/add-task-trigger-api-endpoint).

### 4.9 Failure modes (acceptable for Iteration 1)

- Agent makes no edits → exits with `NO_CHANGES`. No branch is pushed. Acceptable.
- Agent edits compile-broken code → it still pushes; the preview env build will fail. **Acceptable** in Iteration 1; verifying the build is Iteration 2.
- Loop exceeds 25 iterations → agent exits without pushing. Acceptable.
- Timeout (15 min) hit → SIGTERM, no push. Acceptable.

---

## 5. Observability (Iteration 1)

The minimum we keep:

- Upsun activity log for the task run (decision path, tool calls, timings — all stdout).
- Git history on the new branch (one commit).
- Upsun activity log for the preview environment build (separate activity).

Iteration 2+ may add structured tracing (e.g. write a JSON log of each LLM turn), token-cost tracking, etc.

---

## 6. Recommendations and trade-offs

These shaped the spec above; documented so we can revisit them.

1. **Tailwind via CDN, not a build step.** Avoids Node, keeps prompts → diffs → previews fast. We can swap to a real build later if we test Node-based agent prompts.
2. **Trigger payload via `variables.env.<NAME>`.** Per [GIT-857](https://linear.app/platformsh/issue/GIT-857/add-task-trigger-api-endpoint), entries under `variables.env` land as plain env vars in the task process — confirmed empirically (§7 Q1). `resolve_prompt()` reads `os.environ["AGENT_PROMPT"]` directly. An earlier draft carried a fallback ladder for hypothetical alternative payload mechanisms; it was removed once Q1 resolved, because none of those paths ever fired (see §4.2).
3. **Clone fresh over HTTPS, not push from the slug.** The task slug is not a git checkout; cloning fresh is the simplest way to get a full git tree to edit and push from.
4. **GitHub PAT now, scoped credentials later.** A PAT in `GITHUB_TOKEN` is the simplest. `PLATFORM_TASK_TOKEN` (per the App task trigger authentication RFC, which lives outside this repo in the PM workspace) is the right answer for Iteration 3, when the trigger comes from the app itself. Document but don't build it now.
5. **Four tools, not many.** `read_file`, `list_dir`, `write_file`, `run_bash`. Easy to reason about, easy to lock down later. `run_bash` is the escape hatch; we can remove it once we have richer tools.
6. **Don't wait for the deploy.** Push and exit is the contract for Iteration 1. Adding "wait + curl + report" is a clean Iteration 2 because it's a strict superset.
7. **Branch name encodes the agent type and the prompt.** `coding-<6 hex>-<slug>` (≤39 chars, no slashes, alphanumeric+dash). The prefix identifies which agent created the branch — useful when multiple agent types coexist. Random hex keeps each run unique; the slug keeps PR titles human-readable.
8. **Anthropic SDK, not Claude Code CLI.** Per your call — better for learning. The trade-off is we write the loop ourselves; the upside is we own every step and can instrument it.
9. **Default to Haiku, override per-run.** The default model is `claude-haiku-4-5-20251001` — the cheapest current Anthropic model, and good enough for the small visual prompts Iteration 1 targets. Override per run via `variables.env.AGENT_MODEL` (e.g. switch to `claude-sonnet-4-6` for trickier prompts) or globally via a project env var. Avoids paying Opus / Sonnet rates by default.
10. **Agent identity in folder + task name.** `coding-agent/` and `tasks.coding-agent` are deliberately specific so adding more agents (`review-agent/`, `test-agent/`, …) is a copy-and-rename, not a refactor.
11. **GitHub repo via env var, not hardcoded.** `GITHUB_REPO` (`<owner>/<name>`) is read at runtime in both the clone URL and the PR API call. Decouples the agent runtime from this specific playground repo so the same code can target a different repo with no code change.

---

## 7. Open questions

Real things we don't yet know — to be answered by Iteration 1 itself or by checking with engineering.

- **Q1.** *Resolved.* Trigger payload uses `{"variables": {"env": {"<NAME>": "<VALUE>"}}}` (per [GIT-857](https://linear.app/platformsh/issue/GIT-857/add-task-trigger-api-endpoint)) and entries land as plain env vars in the task process. Confirmed empirically — the first successful task run picked up `AGENT_PROMPT` via the `variables.env.AGENT_PROMPT` payload.
- **Q2.** What is the user-visible behavior when a second trigger fires while one is in flight? The docs mention a default cap of 3 parallel runs, but it's unclear whether requests above the cap queue, reject with an error, or block.
- **Q3.** *Resolved.* The GitHub integration mirrors agent-pushed branches back to the project (confirmed via activity `e2fhnt5psbgzc`), but the resulting environment is **inactive** by default. Activating it requires either a manual `environment:activate`, or a PR flow with `build_pull_requests: true`. Iteration 1 takes the PR path: the agent opens a PR after pushing, and Upsun builds it as an active preview environment automatically.
- **Q4.** What permissions does the user-token-authenticated trigger require? Is project-admin enough, or is there a finer-grained role we should use for production?
- **Q5.** Why are `dependencies` *and* `stack` both rejected on tasks (`Unknown key`) even after the task capability is enabled? The flask app accepts both keys (single-runtime takes `dependencies`, composable image takes `stack`). On tasks, neither works, which means the only pip-free way to get `uv` (or any non-default Python tool) onto a task today is Astral's curl installer. Confirmed empirically with two failed deploys (composable 24.1 and composable 25.11). Worth raising with the team that owns the task config schema — this is likely a parity oversight in the new task type rather than an intentional restriction.
- **Q6.** *Resolved.* Project-level env variables (`upsun variable:create env:X`) do **not** reach a running task slug until the environment is redeployed. Variables added before deploy are baked in; variables added after require an explicit redeploy. Worth flagging to engineering — the documented `--no-build`/`--no-deploy` defaults may be confusing for task-only consumers who expect immediate visibility on the next trigger.

---

## 8. Glossary

- **Task** — Upsun's new ephemeral, API-triggered, run-to-completion container type. The agent/sandbox/task terminology doc lives outside this repo in the PM workspace.
- **Agent** — software that uses an LLM in a loop with tools to achieve a goal. Lives inside the task container in this playground.
- **Sandbox** — security-restriction pattern (firewall, bubblewrap, env filtering). Iteration 4+; not present in Iteration 1.
- **Preview environment** — Upsun environment automatically created from a non-default branch via the GitHub integration.
