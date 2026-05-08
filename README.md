# upsun-task-playground

Test bed for the new Upsun **task container** and for running AI agents inside it. The repo contains a small Flask web app (the demo target) and an agent task that takes a natural-language prompt, edits the codebase, and opens a pull request — which Upsun builds as an active preview environment via the GitHub integration. Deployed on Upsun project `vdaznsr6gfmd2`.

Full design and findings: [SPEC.md](./SPEC.md).

## Local run

Uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync                   # install runtime + dev deps
uv run python app.py      # http://localhost:8000
```

## Endpoints

- `GET /` — homepage (Jinja template, Tailwind via CDN)
- `GET /health` — JSON health check

## Lint and format

```bash
uv run ruff check .
uv run ruff format .
uv run yamllint .
```

CI runs the same set on every push and PR. See `.github/workflows/ci.yml`.

## Deploy

Commits to `main` push to GitHub; Upsun mirrors via the connected integration and runs the build defined in `.upsun/config.yaml` (Python 3.14, uv-managed venv, gunicorn).

## Agent task

`.upsun/config.yaml` also declares a `task` container (`agent/`) that runs an LLM loop using the Anthropic SDK. Trigger it with:

```bash
upsun e:curl -p vdaznsr6gfmd2 -e main tasks/agent/run \
  -X POST \
  -d '{"variables":{"env":{"AGENT_PROMPT":"<your instruction>"}}}'
```

The agent edits the repo, pushes a branch named `agent-<6 hex>-<slug>`, and opens a PR on GitHub. The PR auto-builds an active preview environment. See [SPEC.md §4](./SPEC.md) for the full contract, inputs, and findings.
