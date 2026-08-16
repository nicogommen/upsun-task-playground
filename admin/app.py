"""Admin app: FastAPI front end for triggering coding-agent and watching runs.

Lifespan owns the shared httpx.AsyncClient and the UpsunClient so they're
created once and closed cleanly on shutdown. Each request reaches the client
via request.app.state.upsun.

Status state machine: triggering → running → succeeded | failed. The preview
URL is not shown in v1 (Q-iter2-8): the admin's env-scoped token cannot read
the sibling pr-<number> env, so a successful task goes straight to succeeded
with the PR link. Surfacing the preview URL returns with cross-env access (§7.3).
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import storage
from auth import verify_password
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from upsun_client import UpsunClient

# uvicorn doesn't raise the app logger to INFO, so module-level logger.info is
# dropped by default. Configure the root logger once so our logs actually surface.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CODING_AGENT_TASK = "coding-agent"
PUBLIC_PATHS = frozenset({"/login", "/health"})

# Machine-readable markers the coding-agent prints on stdout (ITERATION-2 §6.5).
_PR_URL_RE = re.compile(r"PR_URL=(https?://\S+)")
_BRANCH_RE = re.compile(r"^BRANCH=(\S+)$", re.MULTILINE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    http = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    project_id = os.environ.get("PLATFORM_PROJECT", "")
    pat = os.environ.get("UPSUN_API_TOKEN") or None
    if not project_id:
        logger.warning("PLATFORM_PROJECT not set — task trigger will fail until provided")
    app.state.upsun = UpsunClient(http, project_id=project_id, pat=pat)
    app.state.http = http
    try:
        yield
    finally:
        await http.aclose()


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# Serves the favicon. require_login below already lets /static/ through
# unauthenticated, so the icon also shows on the login page.
app.mount("/static", StaticFiles(directory="static"), name="static")


# Register the auth gate BEFORE SessionMiddleware: Starlette inserts each
# add_middleware at index 0, so the most recently added wraps the others.
# We need SessionMiddleware to be outermost so request.session is populated
# before require_login reads it.
@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)
    if not request.session.get("user"):
        return RedirectResponse(url="/login", status_code=302)
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SECRET_KEY"],
    same_site="lax",
    https_only=os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true",
    max_age=int(os.environ.get("SESSION_LIFETIME_DAYS", "7")) * 86400,
)


def _current_env() -> str:
    """Default target env for trigger and polling — admin's own env (D3)."""
    return os.environ.get("PLATFORM_BRANCH", "main")


def _ensure_session_id(request: Request) -> str:
    sid = request.session.get("chat_session_id")
    if not sid:
        sid = str(uuid.uuid4())
        request.session["chat_session_id"] = sid
    return sid


def _extract_pr_url(activity_log: str) -> str | None:
    m = _PR_URL_RE.search(activity_log)
    return m.group(1) if m else None


def _extract_branch(activity_log: str) -> str | None:
    m = _BRANCH_RE.search(activity_log)
    return m.group(1) if m else None


def _render_card(request: Request, run: storage.Run) -> Response:
    return templates.TemplateResponse(request, "_run_card.html", {"run": run})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/chat", status_code=302)


@app.get("/login")
async def login_form(request: Request) -> Response:
    if request.session.get("user"):
        return RedirectResponse(url="/chat", status_code=302)
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> Response:
    expected_user = os.environ.get("ADMIN_USERNAME", "admin")
    if username != expected_user or not verify_password(password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid credentials"},
            status_code=401,
        )
    request.session["user"] = username
    return RedirectResponse(url="/chat", status_code=303)


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/chat")
async def chat(request: Request) -> Response:
    sid = _ensure_session_id(request)
    runs = storage.list_runs(session_id=sid)
    return templates.TemplateResponse(request, "chat.html", {"runs": runs})


@app.post("/chat/runs")
async def submit_run(
    request: Request,
    prompt: str = Form(...),
) -> Response:
    sid = _ensure_session_id(request)
    target = _current_env()
    run = storage.Run(
        id=str(uuid.uuid4()),
        session_id=sid,
        prompt=prompt,
        status="triggering",
        target_environment=target,
        created_at=datetime.now(UTC),
    )
    storage.save_run(run)

    client: UpsunClient = request.app.state.upsun
    try:
        body = await client.trigger_task(target, CODING_AGENT_TASK, {"AGENT_PROMPT": prompt})
    except (httpx.HTTPError, KeyError) as exc:
        logger.exception("trigger_task failed for run %s", run.id)
        run = storage.update_run(
            run.id,
            status="failed",
            error=str(exc),
            completed_at=datetime.now(UTC),
        )
        return _render_card(request, run)

    # GIT-857 contract: 202 body is {"status": "created", "_embedded": {"activities": [{...}]}}.
    activities = (body.get("_embedded") or {}).get("activities") or []
    activity_id = activities[0].get("id") if activities else None
    if not activity_id:
        run = storage.update_run(
            run.id,
            status="failed",
            error="trigger response missing activity id",
            completed_at=datetime.now(UTC),
        )
        return _render_card(request, run)
    run = storage.update_run(
        run.id,
        status="running",
        activity_id=activity_id,
    )
    return _render_card(request, run)


async def _poll_activity(client: UpsunClient, run: storage.Run) -> storage.Run:
    """running → succeeded | failed, based on the trigger activity."""
    if not run.activity_id:
        return run
    try:
        activity = await client.get_activity(run.target_environment, run.activity_id)
    except httpx.HTTPError:
        logger.exception("get_activity failed for run %s", run.id)
        return run
    if activity.get("state") != "complete":
        return run

    result = activity.get("result")
    if result != "success":
        return storage.update_run(
            run.id,
            status="failed",
            error=f"task {result or 'failed'}",
            completed_at=datetime.now(UTC),
        )

    # Stdout isn't on the activity object (Q-iter2-7); fetch the log to read markers.
    try:
        log_text = await client.get_activity_log(run.target_environment, run.activity_id)
    except httpx.HTTPError:
        logger.exception("get_activity_log failed for run %s", run.id)
        log_text = ""
    return storage.update_run(
        run.id,
        status="succeeded",
        pr_url=_extract_pr_url(log_text),
        branch_name=_extract_branch(log_text),
        completed_at=datetime.now(UTC),
    )


@app.get("/chat/runs/{run_id}")
async def poll_run(request: Request, run_id: str) -> Response:
    run = storage.get_run(run_id)
    if run is None:
        return Response(status_code=404)
    if run.status in ("succeeded", "failed"):
        return _render_card(request, run)

    client: UpsunClient = request.app.state.upsun
    if run.status == "running":
        run = await _poll_activity(client, run)
    return _render_card(request, run)
