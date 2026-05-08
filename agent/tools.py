"""Tools exposed to the LLM. See SPEC.md §4.6.

Four tools, deliberately minimal:
- read_file
- list_dir
- write_file
- run_bash
"""
import subprocess
from pathlib import Path

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the repo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the repo root.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List the contents of a directory in the repo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to the repo root. Use '.' for the root.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Overwrite a file in the repo with the given content. Creates parent directories if missing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the repo root.",
                },
                "content": {
                    "type": "string",
                    "description": "Full file contents to write.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_bash",
        "description": "Run a shell command in the repo root. Use for tasks not covered by the other tools (e.g. running a syntax check, grepping). Output is truncated to 4000 chars.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run.",
                },
            },
            "required": ["command"],
        },
    },
]


def _safe_path(workdir: str, rel: str) -> Path:
    p = (Path(workdir) / rel).resolve()
    base = Path(workdir).resolve()
    if base != p and base not in p.parents:
        raise ValueError(f"path escapes workdir: {rel}")
    return p


def _read_file(workdir: str, path: str) -> str:
    p = _safe_path(workdir, path)
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    try:
        return p.read_text()
    except UnicodeDecodeError:
        return f"ERROR: binary or non-UTF8 file: {path}"


def _list_dir(workdir: str, path: str) -> str:
    p = _safe_path(workdir, path)
    if not p.is_dir():
        return f"ERROR: not a directory: {path}"
    entries = []
    for child in sorted(p.iterdir()):
        kind = "dir" if child.is_dir() else "file"
        entries.append(f"{kind}\t{child.name}")
    return "\n".join(entries) if entries else "(empty)"


def _write_file(workdir: str, path: str, content: str) -> str:
    p = _safe_path(workdir, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {path} ({len(content)} chars)"


def _run_bash(workdir: str, command: str) -> str:
    result = subprocess.run(
        command,
        shell=True,
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = result.stdout or ""
    if result.stderr:
        out += f"\n[stderr]\n{result.stderr}"
    if len(out) > 4000:
        out = out[:4000] + "\n... (truncated)"
    return f"exit {result.returncode}\n{out}"


def dispatch_tool(name: str, args: dict, workdir: str) -> str:
    try:
        if name == "read_file":
            return _read_file(workdir, args["path"])
        if name == "list_dir":
            return _list_dir(workdir, args["path"])
        if name == "write_file":
            return _write_file(workdir, args["path"], args["content"])
        if name == "run_bash":
            return _run_bash(workdir, args["command"])
        return f"ERROR: unknown tool: {name}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
