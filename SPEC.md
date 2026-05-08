# Upsun Task Playground — Specification

**Purpose.** Hands-on test bed for the new Upsun **task container** and for running AI agents inside it. Used to validate the outcomes defined in the [Run Background Agents on Upsun Cloud](https://linear.app/platformsh/project/run-background-agents-on-upsun-cloud-33a30afc5ff8) project brief.

**Status.** Iteration 1 completed end-to-end on 2026-05-08 (PR auto-built into an active preview environment serving the agent's change). Living document — later iterations extend the spec.

**Project on Upsun.** `upsun-task-playground` (`vdaznsr6gfmd2`), region `eu-3`, default branch `main`, connected to GitHub repo `nicogommen/upsun-task-playground` via the GitHub integration.

---

## 1. Iteration plan

We grow the playground in small, demonstrable increments. This document specifies **Iteration 1** in detail. Later iterations are listed for context but are not yet committed.

| # | Status | Title | Outcome |
| - | ------ | ----- | ------- |
| **1** | **Done (2026-05-08)** | **Visual Flask app + minimal agent task** | Agent receives a prompt, makes the change, opens a PR, Upsun builds an active preview environment from the PR. |
| 2 | Pending | Verification step | Agent waits for the preview deploy and curls a URL to confirm the change is live. |
| 3 | Pending | Agent triggers itself from the app | An app endpoint triggers the task using `PLATFORM_TASK_TOKEN` instead of a user-level token. |
| 4 | Pending | Sandbox restrictions | Outbound firewall + bubblewrap layered onto the task container. |
| 5 | Pending | Sub-agents and parallelism | One agent task launches and orchestrates others. |

---

## 2. Iteration 1 — Visual Flask app + minimal agent task

### 2.1 Goals

- A small Flask app with an actual UI (homepage with demo content) so visual changes are obvious.
- An Upsun `task` container that, given a natural-language prompt, edits the codebase to satisfy that prompt, pushes the result to a new branch, and opens a pull request on GitHub.
- The PR triggers an **active** preview environment via the GitHub integration's `build_pull_requests` setting. **The agent's job ends after the PR is opened.**

### 2.2 Out of scope for Iteration 1

- Verifying the deploy succeeded.
- Checking the change is visually correct.
- Sandboxing (firewall, bubblewrap).
- App-to-task triggering (`PLATFORM_TASK_TOKEN`, `triggers:` on the app).
- Sub-agents, parallel runs, cancellation.
- A UI for invoking the agent. Triggering is done from a terminal.

### 2.3 Success criteria

- Running `upsun e:curl tasks/agent/run -X POST -d '{"variables":{"env":{"AGENT_PROMPT":"..."}}}'` creates an activity that completes without error.
- A new branch named `agent-<6 hex>-<slug>` (≤39 chars, no slashes) appears on GitHub.
- A pull request against `main` is opened automatically by the agent.
- The GitHub integration builds the PR as an **active** preview environment (`build_pull_requests`).
- The preview environment URL serves the modified version of the homepage.
- The task activity log contains a readable trace of the agent's reasoning, tool calls, and the PR URL.

---

## 3. Application specification (`flask`)

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

### 3.3 File layout (after Iteration 1)

```
upsun-task-playground/
├── app.py                  # Flask app, renders templates/index.html
├── pyproject.toml          # Flask + gunicorn + dev deps (ruff, yamllint)
├── uv.lock                 # Resolved versions for the Flask app
├── templates/
│   └── index.html          # Homepage (hero, features, footer)
├── agent/
│   ├── run.py              # Task entry point — the agent loop
│   ├── tools.py            # Tool definitions exposed to the LLM
│   ├── pyproject.toml      # anthropic SDK
│   └── uv.lock             # Resolved versions for the task
├── .upsun/
│   └── config.yaml         # flask app + agent task
├── .github/workflows/
│   └── ci.yml              # ruff + yamllint pipeline
├── .yamllint.yaml          # YAML lint config
├── README.md
├── SPEC.md                 # this file
└── .gitignore
```

### 3.4 Homepage content (Iteration 1 baseline)

The agent will modify this content; the baseline is intentionally generic so changes are easy to compare.

- Hero: headline "Upsun Task Playground", subtitle "A playground for running AI agents on Upsun's task containers.", primary CTA button "Read the docs" linking to `https://upsun.com`.
- Features section: three cards titled "Tasks", "Agents", "Sandboxes" with a one-sentence description each.
- Footer: project name + link to the GitHub repo.

### 3.5 Upsun config — `flask`

In `.upsun/config.yaml`. Python 3.14, uv-managed venv (`dependencies.python3.uv: "*"` bootstraps uv at build, `uv sync --frozen --no-dev` installs the locked deps), `.venv/bin/gunicorn` as the start command, single `/` route to upstream.

### 3.6 Tooling and CI

The playground uses [uv](https://docs.astral.sh/uv/) end-to-end (build, runtime, local dev). Lint and format are enforced via [ruff](https://docs.astral.sh/ruff/) (Python) and [yamllint](https://yamllint.readthedocs.io/) (YAML) — both installed as dev deps in the root `pyproject.toml`.

CI runs on every push and PR via `.github/workflows/ci.yml`:

1. `uv sync --frozen` (root + agent)
2. `uv run ruff check .`
3. `uv run ruff format --check .`
4. `uv run yamllint .`

Local equivalents:

```bash
uv run ruff check .
uv run ruff format .
uv run yamllint .
```

Lock files (`uv.lock`, `agent/uv.lock`) are committed for reproducible builds. The Upsun build hooks use `--frozen` so a missing or outdated lock fails the build instead of silently resolving fresh.

---

## 4. Task specification (`agent`)

### 4.1 Concept

A single Upsun `task` container that runs the **agent runtime**. The agent runtime is a Python script using the Anthropic SDK with tool use. The task is triggered via the Upsun task-trigger API ([GIT-857](https://linear.app/platformsh/issue/GIT-857/add-task-trigger-api-endpoint)); the prompt is passed in the trigger payload under `variables.env.AGENT_PROMPT`. After making the change the agent opens a pull request, which the GitHub integration builds as an active preview environment.

### 4.2 Inputs

| Input | Source | Notes |
| ----- | ------ | ----- |
| **`AGENT_PROMPT`** | **Trigger payload** (`variables.env.AGENT_PROMPT`) | The natural-language instruction. Per [GIT-857](https://linear.app/platformsh/issue/GIT-857/add-task-trigger-api-endpoint), variables under `variables.env.<NAME>` land as plain env vars in the task process. Confirmed empirically. |
| `ANTHROPIC_API_KEY` | Upsun sensitive env var (project, runtime-visible) | Bring-your-own LLM key. Requires a redeploy of `main` after creation for the value to reach a running task (see §7 Q6). |
| `GITHUB_TOKEN` | Upsun sensitive env var (project, runtime-visible) | Fine-grained PAT on `nicogommen/upsun-task-playground` with **`Contents: read+write`** *and* **`Pull requests: read+write`**. Used to push the branch and open the PR. |
| `GIT_USER_NAME`, `GIT_USER_EMAIL` | Upsun env vars | Commit author. Defaults: `upsun-task-playground-agent` / `agent@playground.local`. |

The agent's `resolve_prompt()` still keeps a small fallback ladder (look for likely-named env vars, then a `task-input.json` file, then `AGENT_PROMPT`) but the primary path on Upsun today is `variables.env.AGENT_PROMPT` → `os.environ["AGENT_PROMPT"]`.

### 4.3 Lifecycle

1. Upsun creates a fresh container with the task's slug.
2. Container starts, working directory is the task source root (`/app`); the repository contents are available (the slug, no `.git`).
3. The agent runtime clones the repo fresh (`git clone` over HTTPS using `GITHUB_TOKEN`) into `/tmp/work` so it has full git history and a writable tree.
4. Agent creates a branch `agent-<6 hex>-<slug>` (≤39 chars, no `/`).
5. Agent runs the LLM loop until either: the LLM emits a `stop_reason: end_turn`, the loop hits the iteration limit (default 25), or the timeout expires.
6. Agent commits and pushes the branch.
7. Agent opens a pull request against `main` via the GitHub REST API (stdlib `urllib`, no extra deps). The PR title encodes the prompt; the body links the prompt and the source.
8. The GitHub integration builds the PR as an active preview environment (`build_pull_requests`).
9. Container exits.

### 4.4 Container config

Added to `.upsun/config.yaml`:

```yaml
tasks:
  agent:
    source:
      root: /agent
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
    mounts:
      "/tmp":
        source: "tmp"
```

- `source.root: /agent` keeps the task's code separate from the Flask app.
- **uv install path differs from the flask app — and not by choice.** The task validator rejects both `dependencies` (the app's path) and `stack` (the composable image's path) with `Unknown key`, even with the task capability enabled. Astral's official installer is the only pip-free option that works today. The flask app stays on `dependencies.python3.uv: "*"`. See §7 Q5 — both gaps are real findings from this experiment, to be raised with the schema owners.
- `uv sync --frozen` installs the locked deps from `agent/uv.lock`. No `--no-dev` here because the task has no dev-only deps.
- Ruff's `target-version` is intentionally pinned to `py313` even though both apps deploy on Python 3.14. The newer "unparenthesized except clauses" syntax is a 3.14-only feature; pinning ruff lower keeps the source portable and prevents `ruff format` from stripping parens that would then break a 3.13 fallback. Cheap insurance.
- `timeout: 900` (15 min) is enough for an iteration-1 prompt; raise later if needed.
- `tmp` mount gives the agent a workspace for cloning and editing.
- No `relationships:` declared. Iteration 1 doesn't talk to any service.
- No `triggers:` on the `flask` app. The trigger comes from a user-level token, not from the app.

### 4.5 Agent runtime

Located at `agent/run.py`. Pseudocode (the contract; see the file for the full implementation):

```python
import os, secrets, subprocess, anthropic, urllib.request
from tools import TOOLS, dispatch_tool

prompt = resolve_prompt()  # variables.env.AGENT_PROMPT (see §4.2)
client = anthropic.Anthropic()

# 1. Clone fresh repo with credentials
workdir = "/tmp/work"
clone_url = f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/nicogommen/upsun-task-playground.git"
subprocess.run(["git", "clone", clone_url, workdir], check=True)

# 2. Branch name: agent-<6hex>-<slug<=26>, total <=39 chars, no slashes
branch = f"agent-{secrets.token_hex(3)}-{slugify(prompt, max_len=26)}"
subprocess.run(["git", "-C", workdir, "checkout", "-b", branch], check=True)

# 3. Configure git author, run the LLM loop, commit, push
# (claude-sonnet-4-6, 25 turn limit, stop on end_turn) ...

# 4. Open the PR via GitHub REST API (stdlib urllib)
urllib.request.Request(
    "https://api.github.com/repos/nicogommen/upsun-task-playground/pulls",
    data=json.dumps({"title": ..., "head": branch, "base": "main", "body": ...}).encode(),
    headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", ...},
)
```

### 4.6 Tool surface

The LLM gets four tools, defined in `agent/tools.py`. Minimal by design — small surface area, easy to audit.

| Tool | Purpose | Inputs |
| ---- | ------- | ------ |
| `read_file` | Read a file from the working tree | `path` (relative to repo root) |
| `list_dir` | List the contents of a directory | `path` |
| `write_file` | Overwrite a file | `path`, `content` |
| `run_bash` | Run a shell command in the work tree | `command` (string) |

Edits-only via `write_file` (no diff/patch tool) keeps the loop simple. `run_bash` is the escape hatch — the agent can do anything else through it (e.g. `python -m py_compile`, `grep`).

### 4.7 Outputs

- **Stdout / stderr** of the task is captured by Upsun and surfaced via `upsun activity:log <id>`. This is the **audit trail** for Iteration 1.
- **Branch on GitHub** named `agent-<6 hex>-<prompt-slug>` (≤39 chars, no slashes).
- **Pull request** opened against `main`, with the prompt as title and body.
- **Active preview environment** built automatically from the PR by Upsun's GitHub integration (`build_pull_requests`).
- **PR URL** printed to stdout at the end of the task log.

### 4.8 Trigger

CLI form (the user, with their CLI session):

```bash
# Trigger with the prompt in the variables.env envelope
upsun e:curl -p vdaznsr6gfmd2 -e main tasks/agent/run \
  -X POST \
  -d '{"variables":{"env":{"AGENT_PROMPT":"Change the homepage headline to Hello from an agent"}}}'

# Watch
upsun activity:list -p vdaznsr6gfmd2 -e main --limit 5
upsun activity:log <ID>
```

Direct API form (equivalent):

```bash
curl -X POST \
  -H "Authorization: Bearer $UPSUN_TOKEN" \
  https://api.upsun.com/projects/vdaznsr6gfmd2/environments/main/tasks/agent/run \
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
2. **Trigger payload via `variables.env.<NAME>`.** Per [GIT-857](https://linear.app/platformsh/issue/GIT-857/add-task-trigger-api-endpoint), entries under `variables.env` land as plain env vars in the task process — confirmed empirically (§7 Q1). The agent's `resolve_prompt()` keeps a small fallback ladder for portability, but in practice on Upsun the prompt arrives as `os.environ["AGENT_PROMPT"]`.
3. **Clone fresh over HTTPS, not push from the slug.** The task slug is not a git checkout; cloning fresh is the simplest way to get a full git tree to edit and push from.
4. **GitHub PAT now, scoped credentials later.** A PAT in `GITHUB_TOKEN` is the simplest. `PLATFORM_TASK_TOKEN` (per the [App task trigger auth RFC](../../rfc-app-task-trigger-authentication.md)) is the right answer for Iteration 3, when the trigger comes from the app itself. Document but don't build it now.
5. **Four tools, not many.** `read_file`, `list_dir`, `write_file`, `run_bash`. Easy to reason about, easy to lock down later. `run_bash` is the escape hatch; we can remove it once we have richer tools.
6. **Don't wait for the deploy.** Push and exit is the contract for Iteration 1. Adding "wait + curl + report" is a clean Iteration 2 because it's a strict superset.
7. **Branch name encodes the prompt.** `agent-<6 hex>-<slug>` (≤39 chars, no slashes, alphanumeric+dash). Random hex keeps each run unique even if the same prompt fires twice; the slug keeps PR titles and Upsun env names human-readable.
8. **Anthropic SDK, not Claude Code CLI.** Per your call — better for learning. The trade-off is we write the loop ourselves; the upside is we own every step and can instrument it.
9. **Sonnet 4.6, not Opus.** Cheaper per token and fast enough for small visual edits. We'll move to Opus only if we see Sonnet fail on tasks we expect to succeed.

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

- **Task** — Upsun's new ephemeral, API-triggered, run-to-completion container type. See [terminology doc](../../terminology-agent-sandbox-task.md).
- **Agent** — software that uses an LLM in a loop with tools to achieve a goal. Lives inside the task container in this playground.
- **Sandbox** — security-restriction pattern (firewall, bubblewrap, env filtering). Iteration 4+; not present in Iteration 1.
- **Preview environment** — Upsun environment automatically created from a non-default branch via the GitHub integration.
