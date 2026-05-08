# Upsun Task Playground — Specification

**Purpose.** Hands-on test bed for the new Upsun **task container** and for running AI agents inside it. Used to validate the outcomes defined in the [Run Background Agents on Upsun Cloud](https://linear.app/platformsh/project/run-background-agents-on-upsun-cloud-33a30afc5ff8) project brief.

**Status.** Living document. The current scope is intentionally minimal (Iteration 1 below). New iterations extend the spec.

**Project on Upsun.** `upsun-task-playground` (`vdaznsr6gfmd2`), region `eu-3`, default branch `main`, connected to GitHub repo `nicogommen/upsun-task-playground` via the GitHub integration.

---

## 1. Iteration plan

We grow the playground in small, demonstrable increments. This document only specifies **Iteration 1** in detail. Later iterations are listed for context but are not yet committed.

| # | Title | Outcome |
| - | ----- | ------- |
| **1** | **Visual Flask app + minimal agent task** | Agent receives a prompt asking for a code change, makes the change, and creates a preview environment with the change applied. |
| 2 | Verification step | Agent waits for the preview deploy and curls a URL to confirm the change is live. |
| 3 | Agent triggers itself from the app | An app endpoint triggers the task using `PLATFORM_TASK_TOKEN` instead of a user-level token. |
| 4 | Sandbox restrictions | Outbound firewall + bubblewrap layered onto the task container. |
| 5 | Sub-agents and parallelism | One agent task launches and orchestrates others. |

---

## 2. Iteration 1 — Visual Flask app + minimal agent task

### 2.1 Goals

- A small Flask app with an actual UI (homepage with demo content) so visual changes are obvious.
- An Upsun `task` container that, given a natural-language prompt, edits the codebase to satisfy that prompt and pushes the result to a new branch.
- The push triggers a preview environment via the existing GitHub integration. **The agent's job ends at `git push`.**

### 2.2 Out of scope for Iteration 1

- Verifying the deploy succeeded.
- Checking the change is visually correct.
- Sandboxing (firewall, bubblewrap).
- App-to-task triggering (`PLATFORM_TASK_TOKEN`, `triggers:` on the app).
- Sub-agents, parallel runs, cancellation.
- A UI for invoking the agent. Triggering is done from a terminal.

### 2.3 Success criteria

- Running `upsunstg e:curl tasks/agent/run -X POST -d '{"prompt":"..."}'` creates an activity that completes without error. (See §4.2 for prompt delivery — payload first, env var as fallback.)
- A new branch named `agent/<timestamp>-<slug>` appears on GitHub.
- Upsun creates a preview environment for that branch automatically (via the existing GitHub integration).
- The preview environment URL serves the modified version of the homepage.
- The task activity log contains a readable trace of the agent's reasoning and tool calls.

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

A single Upsun `task` container that runs the **agent runtime**. The agent runtime is a Python script using the Anthropic SDK with tool use. The task is triggered manually (per-run) via the API; the prompt is passed in via an Upsun environment variable.

### 4.2 Inputs

| Input | Source | Notes |
| ----- | ------ | ----- |
| **Prompt** | **Trigger payload (preferred), env var (fallback)** | The natural-language instruction. Primary path: passed in the JSON body of the trigger call (`-d '{"prompt":"..."}'`). Fallback: Upsun runtime env var `AGENT_PROMPT`, set via `upsun variable:create --level environment --name env:AGENT_PROMPT --value "..."`. |
| `ANTHROPIC_API_KEY` | Upsun sensitive env var | Bring-your-own LLM key. Set once. |
| `GITHUB_TOKEN` | Upsun sensitive env var | GitHub PAT with `repo` scope on `nicogommen/upsun-task-playground`. Used to push the agent's branch. |
| `GIT_USER_NAME`, `GIT_USER_EMAIL` | Upsun env vars | Used to set commit author. Default: `upsun-task-playground-agent` / `agent@playground.local`. |

#### Prompt delivery: probe + fallback

Trigger input parameters are listed as a "Later" item in [GIT-857](https://linear.app/platformsh/issue/GIT-857/add-task-trigger-api-endpoint), but the endpoint may already pass the body through. We will find out empirically rather than assuming.

On startup, the agent runtime:

1. Prints all env vars matching `PLATFORM_*` and `UPSUN_*` to stdout (logs are private; values are not redacted at this stage so we can see exactly what's exposed).
2. Looks for the prompt in this priority order:
   1. JSON-decoded `prompt` field from any env var matching `*TASK*INPUT*`, `*TASK*PAYLOAD*`, or `PLATFORM_TASK_*` (in case Upsun injects the trigger body under one of those names).
   2. Body content of any file at `/run/task-input.json`, `/var/run/task-input.json`, or `$PLATFORM_TASK_INPUT_FILE` (alternate convention some platforms use).
   3. The `AGENT_PROMPT` env var.
3. If none is present, exits with a clear `NO_PROMPT_FOUND` message and the env dump.

The first triggered run is effectively a probe: we trigger with `-d '{"prompt":"<small visible change>"}'` **and** also set `AGENT_PROMPT` to a different value. Whichever the agent picks up tells us where the payload landed (or that we need the env-var fallback).

Once the mechanism is known, we drop the unused fallback path from the runtime and update this spec.

### 4.3 Lifecycle

1. Upsun creates a fresh container with the task's slug.
2. Container starts, working directory is the task source root (`/app/agent`); the repository contents are available (the slug, no `.git`).
3. The agent runtime clones the repo fresh (`git clone` over HTTPS using `GITHUB_TOKEN`) into `/tmp/work` so it has full git history and a writable tree.
4. Agent runs the LLM loop until either: the LLM emits a `done` tool call, the loop hits the iteration limit (default 25), or the timeout expires.
5. Agent commits and pushes the branch.
6. Container exits.

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
- `uv sync --frozen` installs the locked deps from `agent/uv.lock`. No `--no-dev` here because the task has no dev-only deps.
- `timeout: 900` (15 min) is enough for an iteration-1 prompt; raise later if needed.
- `tmp` mount gives the agent a workspace for cloning and editing.
- No `relationships:` declared. Iteration 1 doesn't talk to any service.
- No `triggers:` on the `flask` app. The trigger comes from a user-level token, not from the app.

### 4.5 Agent runtime

Located at `agent/run.py`. Pseudocode:

```python
import os, subprocess, time, slugify, anthropic
from tools import TOOLS, dispatch_tool

prompt = resolve_prompt()  # see §4.2 — payload first, env var fallback
client = anthropic.Anthropic()

# 1. Clone fresh repo with credentials
workdir = "/tmp/work"
clone_url = f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/nicogommen/upsun-task-playground.git"
subprocess.run(["git", "clone", clone_url, workdir], check=True)

branch = f"agent/{int(time.time())}-{slugify(prompt)[:40]}"
subprocess.run(["git", "-C", workdir, "checkout", "-b", branch], check=True)

# 2. Configure git author
subprocess.run(["git", "-C", workdir, "config", "user.name",  os.environ["GIT_USER_NAME"]], check=True)
subprocess.run(["git", "-C", workdir, "config", "user.email", os.environ["GIT_USER_EMAIL"]], check=True)

# 3. Run the LLM loop
messages = [{"role": "user", "content": prompt}]
for _ in range(25):
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        tools=TOOLS,
        messages=messages,
    )
    messages.append({"role": "assistant", "content": resp.content})
    if resp.stop_reason == "end_turn":
        break
    tool_results = [dispatch_tool(b, workdir) for b in resp.content if b.type == "tool_use"]
    messages.append({"role": "user", "content": tool_results})

# 4. Commit and push if there are changes
status = subprocess.run(["git", "-C", workdir, "status", "--porcelain"], capture_output=True, text=True).stdout
if status.strip():
    subprocess.run(["git", "-C", workdir, "add", "-A"], check=True)
    subprocess.run(["git", "-C", workdir, "commit", "-m", f"Agent: {prompt[:72]}"], check=True)
    subprocess.run(["git", "-C", workdir, "push", "origin", branch], check=True)
    print(f"PUSHED {branch}")
else:
    print("NO_CHANGES")
```

The full implementation will be in `agent/run.py`; the snippet above defines the contract.

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
- **Branch on GitHub** named `agent/<timestamp>-<prompt-slug>`.
- **Preview environment** automatically created by the existing GitHub integration when Upsun mirrors the new branch.

### 4.8 Trigger

CLI form (the user, with their CLI session):

```bash
# Trigger with the prompt in the payload (preferred path)
upsunstg e:curl -p vdaznsr6gfmd2 -e main tasks/agent/run \
  -X POST \
  -d '{"prompt":"Change the homepage headline to '"'"'Hello from an agent'"'"'"}'

# Watch
upsun activity:list -p vdaznsr6gfmd2 -e main --limit 5
upsun activity:log <ID>
```

If the payload doesn't reach the task container, fall back to setting the prompt as an env var first:

```bash
upsun variable:update -p vdaznsr6gfmd2 -e main \
  env:AGENT_PROMPT --value "Change the homepage headline to 'Hello from an agent'"
upsunstg e:curl -p vdaznsr6gfmd2 -e main tasks/agent/run -X POST -d '{}'
```

Direct API form (equivalent to the preferred path):

```bash
curl -X POST \
  -H "Authorization: Bearer $UPSUN_TOKEN" \
  https://api.upsun.com/projects/vdaznsr6gfmd2/environments/main/tasks/agent/run \
  -d '{"prompt":"..."}'
```

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
2. **Try the trigger payload first; runtime env var is the fallback.** Per GIT-857, trigger input parameters are nominally "Later", but the endpoint may already pass the body through. Iteration 1 probes for it on the first run and only commits to the env-var path if the payload doesn't surface in the container.
3. **Clone fresh over HTTPS, not push from the slug.** The task slug is not a git checkout; cloning fresh is the simplest way to get a full git tree to edit and push from.
4. **GitHub PAT now, scoped credentials later.** A PAT in `GITHUB_TOKEN` is the simplest. `PLATFORM_TASK_TOKEN` (per the [App task trigger auth RFC](../../rfc-app-task-trigger-authentication.md)) is the right answer for Iteration 3, when the trigger comes from the app itself. Document but don't build it now.
5. **Four tools, not many.** `read_file`, `list_dir`, `write_file`, `run_bash`. Easy to reason about, easy to lock down later. `run_bash` is the escape hatch; we can remove it once we have richer tools.
6. **Don't wait for the deploy.** Push and exit is the contract for Iteration 1. Adding "wait + curl + report" is a clean Iteration 2 because it's a strict superset.
7. **Branch name encodes the prompt.** `agent/<timestamp>-<slug>` makes preview environments easy to find in the Upsun console.
8. **Anthropic SDK, not Claude Code CLI.** Per your call — better for learning. The trade-off is we write the loop ourselves; the upside is we own every step and can instrument it.
9. **Sonnet 4.6, not Opus.** Cheaper per token and fast enough for small visual edits. We'll move to Opus only if we see Sonnet fail on tasks we expect to succeed.

---

## 7. Open questions

Real things we don't yet know — to be answered by Iteration 1 itself or by checking with engineering.

- **Q1.** Does the trigger payload reach the task container today? If yes, under what mechanism (env var name, file path, API callback)? The first triggered run answers this — see §4.2.
- **Q2.** What is the user-visible behavior when a second trigger fires while one is in flight? The docs mention a default cap of 3 parallel runs, but it's unclear whether requests above the cap queue, reject with an error, or block.
- **Q3.** Does the GitHub integration treat a push from inside a task container identically to a push from a developer machine? Specifically: does it auto-create a preview environment, and does Upsun mirror the agent-pushed branch back into the project?
- **Q4.** What permissions does the user-token-authenticated trigger require? Is project-admin enough, or is there a finer-grained role we should use for production?
- **Q5.** Why are `dependencies` *and* `stack` both rejected on tasks (`Unknown key`) even after the task capability is enabled? The flask app accepts both keys (single-runtime takes `dependencies`, composable image takes `stack`). On tasks, neither works, which means the only pip-free way to get `uv` (or any non-default Python tool) onto a task today is Astral's curl installer. Confirmed empirically with two failed deploys (composable 24.1 and composable 25.11). Worth raising with the team that owns the task config schema — this is likely a parity oversight in the new task type rather than an intentional restriction.

---

## 8. Glossary

- **Task** — Upsun's new ephemeral, API-triggered, run-to-completion container type. See [terminology doc](../../terminology-agent-sandbox-task.md).
- **Agent** — software that uses an LLM in a loop with tools to achieve a goal. Lives inside the task container in this playground.
- **Sandbox** — security-restriction pattern (firewall, bubblewrap, env filtering). Iteration 4+; not present in Iteration 1.
- **Preview environment** — Upsun environment automatically created from a non-default branch via the GitHub integration.
