# upsun-task-playground

Test project for Upsun task container experiments. First app: a minimal Flask service deployed via the GitHub integration on project `vdaznsr6gfmd2`.

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
