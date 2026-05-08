# upsun-task-playground

Test project for Upsun task container experiments. First app: a minimal Flask service deployed via the GitHub integration on project `vdaznsr6gfmd2`.

## Local run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py  # http://localhost:8000
```

## Endpoints

- `GET /` — JSON greeting
- `GET /health` — health check

## Deploy

Commits to `main` are pushed to GitHub and deployed by Upsun via the connected integration. App config lives in `.upsun/config.yaml` (Python 3.12, gunicorn).
