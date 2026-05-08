import os

from auth import verify_password
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI()
templates = Jinja2Templates(directory="templates")

PUBLIC_PATHS = frozenset({"/login", "/health"})


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
    return templates.TemplateResponse(request, "chat.html", {})
