"""Coding-agent task entry point.

Runs an LLM loop with file/edit tools, commits and pushes the result to a
new branch on the configured GitHub repo, then opens a PR. The PR triggers
an active preview environment via the GitHub integration (build_pull_requests).

Spec: SPEC.md §4.

Configuration via environment variables (see SPEC.md §4.2):
- GITHUB_REPO     — required, "<owner>/<name>" (e.g. "nicogommen/upsun-task-playground")
- GITHUB_TOKEN    — required, fine-grained PAT with Contents + Pull requests RW
- ANTHROPIC_API_KEY — required, Anthropic API key
- AGENT_PROMPT    — required, the natural-language instruction
- AGENT_MODEL     — optional, defaults to DEFAULT_MODEL below
- GIT_USER_NAME   — optional, commit author name
- GIT_USER_EMAIL  — optional, commit author email
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import anthropic
from tools import TOOLS, dispatch_tool

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
WORKDIR = "/tmp/work"
MAX_ITERATIONS = 25
MAX_TOKENS = 4096

# Branch format: "<prefix>-<6 hex>-<slug>", total <= 39 chars (per SPEC §4.7).
BRANCH_PREFIX = "coding"
BRANCH_SLUG_MAX = 39 - len(BRANCH_PREFIX) - len("-XXXXXX-")  # 25

SYSTEM_PROMPT = """You are an automated coding agent running in an Upsun task container.

You receive a natural-language instruction and have access to a Flask web app
checked out at the repo root. The homepage lives in templates/index.html, the
Flask code is in app.py. All tool paths are relative to the repo root.

Rules:
- Make the smallest change that satisfies the request.
- Do NOT modify .upsun/, coding-agent/, pyproject.toml, uv.lock, or SPEC.md
  unless the prompt explicitly asks you to.
- Do NOT git commit or git push. The caller handles that after the loop ends.
- When done, stop emitting tool calls and produce a brief summary.
- If the request is unclear or impossible, explain briefly and stop.
"""


def get_repo() -> tuple[str, str]:
    """Parse GITHUB_REPO env var into (owner, name)."""
    raw = os.environ.get("GITHUB_REPO", "").strip()
    if not raw or "/" not in raw:
        raise RuntimeError(
            "GITHUB_REPO must be set to '<owner>/<name>' (e.g. 'nicogommen/upsun-task-playground')"
        )
    owner, _, name = raw.partition("/")
    name = name.removesuffix(".git")
    if not owner or not name:
        raise RuntimeError(f"Invalid GITHUB_REPO format: {raw!r}")
    return owner, name


def get_model() -> str:
    """Return the LLM model — AGENT_MODEL override, else DEFAULT_MODEL."""
    return os.environ.get("AGENT_MODEL", "").strip() or DEFAULT_MODEL


def dump_env_for_probe() -> None:
    """Probe what Upsun exposes (SPEC.md §4.2 Q1)."""
    print("=" * 60, flush=True)
    print("ENV PROBE (PLATFORM_* / UPSUN_* / *TASK*):", flush=True)
    for k in sorted(os.environ):
        upper = k.upper()
        if upper.startswith(("PLATFORM_", "UPSUN_")) or "TASK" in upper:
            v = os.environ[k]
            preview = v if len(v) < 200 else v[:200] + "..."
            print(f"  {k}={preview}", flush=True)
    print("=" * 60, flush=True)


def resolve_prompt() -> str | None:
    """Read the prompt from `AGENT_PROMPT`.

    The Upsun task-trigger payload (`variables.env.AGENT_PROMPT`) lands as a
    plain env var (SPEC §4.2 / §7 Q1).
    """
    prompt = os.environ.get("AGENT_PROMPT", "").strip()
    if not prompt:
        return None
    print("prompt source: AGENT_PROMPT", flush=True)
    return prompt


def slugify(text: str, max_len: int = BRANCH_SLUG_MAX) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "noslug"


# Matches `scheme://user:pass@host` so we can redact credentials from logs.
_URL_CREDS_RE = re.compile(r"://([^/@\s:]+):([^/@\s]+)@")


def _redact(s: str) -> str:
    """Redact passwords from URL-embedded basic-auth credentials."""
    return _URL_CREDS_RE.sub(r"://\1:<redacted>@", s)


def run_cmd(cmd: list[str], cwd: str | None = None) -> str:
    """Run a command, raise on failure, return stdout.

    Logs are redacted to avoid leaking GITHUB_TOKEN if it appears in argv
    (e.g. as part of a clone/push URL).
    """
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        redacted = [_redact(a) for a in cmd]
        sys.stderr.write(
            f"$ {' '.join(redacted)}\nstdout: {result.stdout}\nstderr: {result.stderr}\n"
        )
        raise RuntimeError(f"command failed (rc={result.returncode}): {' '.join(redacted)}")
    return result.stdout


def clone_repo(workdir: str, owner: str, name: str) -> None:
    token = os.environ["GITHUB_TOKEN"]
    auth_url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
    if Path(workdir).exists():
        shutil.rmtree(workdir)
    print(f"cloning {owner}/{name} into {workdir}", flush=True)
    run_cmd(["git", "clone", "--depth", "20", auth_url, workdir])


def configure_git(workdir: str) -> None:
    name = os.environ.get("GIT_USER_NAME", "upsun-task-playground-coding-agent")
    email = os.environ.get("GIT_USER_EMAIL", "coding-agent@playground.local")
    run_cmd(["git", "config", "user.name", name], cwd=workdir)
    run_cmd(["git", "config", "user.email", email], cwd=workdir)


def run_llm_loop(client: anthropic.Anthropic, prompt: str, workdir: str, model: str) -> None:
    messages = [{"role": "user", "content": prompt}]

    for turn in range(MAX_ITERATIONS):
        print(f"--- turn {turn + 1} ---", flush=True)
        resp = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        for block in resp.content:
            if block.type == "text":
                print(f"[assistant text]\n{block.text}", flush=True)
            elif block.type == "tool_use":
                args_preview = json.dumps(block.input)[:300]
                print(f"[tool_use] {block.name}({args_preview})", flush=True)

        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "end_turn":
            print("loop end (end_turn)", flush=True)
            return
        if resp.stop_reason != "tool_use":
            print(f"loop end (stop_reason={resp.stop_reason})", flush=True)
            return

        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            output = dispatch_tool(block.name, block.input, workdir)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )
            preview = output[:300].replace("\n", " ")
            print(f"[tool_result] {block.name}: {preview}", flush=True)

        messages.append({"role": "user", "content": tool_results})

    print(f"loop end (hit MAX_ITERATIONS={MAX_ITERATIONS})", flush=True)


def commit_and_push(workdir: str, branch: str, prompt: str) -> bool:
    """Commit any uncommitted changes and push. Returns True if anything was pushed."""
    status = run_cmd(["git", "status", "--porcelain"], cwd=workdir)
    if status.strip():
        print("uncommitted changes:\n" + status, flush=True)
        run_cmd(["git", "add", "-A"], cwd=workdir)
        commit_msg = f"Agent: {prompt[:72]}"
        run_cmd(["git", "commit", "-m", commit_msg], cwd=workdir)
        print(f"committed: {commit_msg}", flush=True)
    else:
        print("no uncommitted changes", flush=True)

    ahead = run_cmd(["git", "rev-list", "--count", "origin/main..HEAD"], cwd=workdir).strip()
    if ahead == "0":
        print("NO_CHANGES — branch has no new commits over origin/main", flush=True)
        return False

    run_cmd(["git", "push", "origin", branch], cwd=workdir)
    print(f"PUSHED {branch} ({ahead} commit(s))", flush=True)
    return True


def open_pr(branch: str, prompt: str, owner: str, name: str) -> str | None:
    """Open a pull request on GitHub for the agent's branch.

    Returns the PR URL on success, None on failure. Failure does not propagate
    — the branch is already pushed, the user can open the PR manually.
    """
    token = os.environ["GITHUB_TOKEN"]
    title = f"Agent: {prompt[:60]}"
    body = (
        "Generated by the coding-agent task running on Upsun.\n\n"
        f"**Prompt:** {prompt}\n\n"
        "Review the diff before merging. The PR triggers an active preview "
        "environment automatically (build_pull_requests)."
    )
    payload = json.dumps({"title": title, "head": branch, "base": "main", "body": body}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{name}/pulls",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data.get("html_url")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"PR creation failed: HTTP {e.code} — {err}", flush=True)
        return None
    except urllib.error.URLError as e:
        print(f"PR creation failed: {e}", flush=True)
        return None


def main() -> int:
    dump_env_for_probe()

    prompt = resolve_prompt()
    if not prompt:
        print(
            "NO_PROMPT_FOUND — set AGENT_PROMPT env var or pass "
            '{"variables":{"env":{"AGENT_PROMPT":"..."}}} in the trigger payload',
            flush=True,
        )
        return 2

    owner, name = get_repo()
    model = get_model()
    print(f"repo: {owner}/{name}", flush=True)
    print(f"model: {model}", flush=True)
    print(f"prompt: {prompt}", flush=True)

    clone_repo(WORKDIR, owner, name)
    configure_git(WORKDIR)

    branch = f"{BRANCH_PREFIX}-{secrets.token_hex(3)}-{slugify(prompt)}"
    run_cmd(["git", "checkout", "-b", branch], cwd=WORKDIR)
    print(f"branch: {branch}", flush=True)

    client = anthropic.Anthropic()
    run_llm_loop(client, prompt, WORKDIR, model)

    if not commit_and_push(WORKDIR, branch, prompt):
        return 0

    pr_url = open_pr(branch, prompt, owner, name)
    if pr_url:
        # Machine-readable markers parsed by the admin app from the activity log
        # (ITERATION-2 §6.5). One per line, no trailing whitespace — this is a contract.
        print(f"BRANCH={branch}", flush=True)
        print(f"PR_URL={pr_url}", flush=True)
    else:
        print(
            "Branch is pushed but PR was not opened — open it manually if needed.",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
