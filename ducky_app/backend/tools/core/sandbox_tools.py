"""sandbox_* tools: a private workspace per chat to download, install, build, run and study
programs (test copies of Ducky included) without touching the person's projects or profile.

The approval card is asked here, in the calling agent's chat; the work itself runs in the
Ducky window's process (backend.sandbox.service), which owns the programs, their jobs and
the chat panel's Sandbox section.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from backend.server import mcp
from backend.util.json_util import tool_json

# Long steps (a build, a runtime download) may run this long before the window answers.
_TIMEOUTS = {"run": 3700.0, "setup": 1200.0, "worktree": 900.0, "clone": 900.0, "fetch": 900.0, "mcp": 1800.0}


def _chat() -> str:
    try:
        from backend.tools.panel.panel_ui import _resolve_ask_user_conv_id

        return _resolve_ask_user_conv_id()
    except Exception:
        return ""


def _forward(call: str, /, **args: Any) -> dict[str, Any]:
    """Hand ``call`` to the window. Positional-only: several tools have their own ``name`` argument."""
    from frontend.ui_web import agent_modes

    args["conv_id"] = args.get("conv_id") or _chat()
    if not agent_modes._in_bridge_process():
        from backend.sandbox import service

        return service.call(call, args)
    from frontend.settings import PANEL_LISTENER_PORT

    body = json.dumps({"args": {"name": call, "args": args}}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_api/sandbox_call", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUTS.get(call, 180.0)) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Ducky's window didn't answer ({exc}). Is Ducky open?"}
    result = payload.get("result", payload) if isinstance(payload, dict) else None
    return result if isinstance(result, dict) else {"ok": False, "error": "Ducky's window sent an unreadable answer."}


def _approved(what: str, detail: str) -> str:
    """'' when the person allowed it (now or for the whole chat), else why not."""
    from backend.sandbox.policy import ask_to_run

    conv = _chat()
    if not conv:
        return "A sandbox belongs to a chat, and this call has no chat."
    answer = ask_to_run(conv, what, detail)
    return "" if answer in ("once", "sandbox") else answer


def _refused(command: str) -> str:
    from backend.sandbox.policy import refusal

    return refusal(command)


@mcp.tool()
def sandbox_open(pretty: bool = False) -> str:
    """This chat's sandbox: its folders (work, downloads), its own profile folders, size, portable
    runtimes ready, programs running and what happened so far. Creates it the first time.

    Use a sandbox for anything you would otherwise do in a scratch folder: try a library or a
    program, clone and build a repo, reproduce a bug, run a test copy of Ducky (only when tests
    can't cover the change). Never the
    project, never the OS temp folder. Recipe: skill_read_subskill("ducky", "sandbox")."""
    return tool_json(_forward("open"), pretty=pretty)


@mcp.tool()
def sandbox_fetch(url: str, name: str = "", sha256: str = "", pretty: bool = False) -> str:
    """Download one public file into the sandbox's downloads folder (asks first; up to 800 MB).
    Pass sha256 when the publisher lists one and it is checked. Returns path, size and SHA-256."""
    why = _approved(f"Download {url}", f"File: {name or url}\nSaved in this chat's sandbox only.")
    if why:
        return tool_json({"ok": False, "error": why}, pretty=pretty)
    return tool_json(_forward("fetch", url=url, name=name, sha256=sha256), pretty=pretty)


@mcp.tool()
def sandbox_clone(repo: str, ref: str = "", name: str = "", pretty: bool = False) -> str:
    """Shallow-clone a public repo (https URL or owner/name on GitHub) into the sandbox's work
    folder; ref = branch, tag or commit. Asks first. Needs git (sandbox_setup(runtime='git'))."""
    why = _approved(f"Clone {repo}" + (f" at {ref}" if ref else ""), "Into this chat's sandbox work folder.")
    if why:
        return tool_json({"ok": False, "error": why}, pretty=pretty)
    return tool_json(_forward("clone", repo=repo, ref=ref, name=name), pretty=pretty)


@mcp.tool()
def sandbox_worktree(repo_path: str, commit: str = "HEAD", name: str = "", copy: list[str] | None = None,
                     copy_from: str = "", pretty: bool = False) -> str:
    """A clean, detached git worktree of one of the person's projects at ``commit``, inside the
    sandbox: a build or test sees exactly that commit, never uncommitted work from other chats.
    ``copy`` lists ignored files the build needs (relative paths, taken from the repo or from
    ``copy_from``, another checkout of it). Removed when the sandbox is cleared. Asks first."""
    why = _approved(f"Check out {commit} of {repo_path} into the sandbox",
                    "Adds a git worktree (removed again when the sandbox is cleared). Your files don't change."
                    + (f"\nCopies: {', '.join(copy or [])}" if copy else ""))
    if why:
        return tool_json({"ok": False, "error": why}, pretty=pretty)
    return tool_json(_forward("worktree", repo_path=repo_path, commit=commit, name=name, copy=list(copy or []),
                              copy_from=copy_from), pretty=pretty)


@mcp.tool()
def sandbox_setup(runtime: str, pretty: bool = False) -> str:
    """Make a portable runtime ready for every sandbox: node (Node 24 + npm), python (3.12 via
    uv), uv, or git (MinGit). Pinned versions, checked against the publisher's SHA-256, kept in
    Ducky's own tools folder; nothing installs system-wide. Asks first the first time."""
    why = _approved(f"Set up portable {runtime}", "Downloaded once into Ducky's tools folder; nothing changes on the PC.")
    if why:
        return tool_json({"ok": False, "error": why}, pretty=pretty)
    return tool_json(_forward("setup", runtime=runtime), pretty=pretty)


@mcp.tool()
def sandbox_run(command: str, shell: str = "bash", timeout_s: float = 600.0, expect_minutes: float = 0,
                pretty: bool = False) -> str:
    """Run a command in the sandbox's own terminal (cwd = its work folder, its own profile
    folders and caches; visible in the header's terminal list) and wait for it. Asks first
    unless the chat allows sandbox commands. For servers and apps that keep running, use
    sandbox_launch instead. Never runs: admin, system installers, registry/PATH changes,
    scheduled tasks, releases/publishes/deploys, git push.

    expect_minutes: how long this run should take; shows the person a progress bar, and a run
    three times slower (at least 2 min) comes back still running."""
    why = _refused(command) or _approved("Run a command", command)
    if why:
        return tool_json({"ok": False, "error": why}, pretty=pretty)
    from backend.tools.panel.ducky_panel_terminal import early_return_hint

    # ponytail: the read hint is ducky_terminal_read_output, not sandbox_look: look reads
    # launched programs only, a sandbox_run lives in a terminal session.
    return tool_json(early_return_hint(_forward("run", command=command, shell=shell, timeout_s=timeout_s,
                                                     expect_minutes=max(0.0, float(expect_minutes or 0)))),
                     pretty=pretty)


@mcp.tool()
def sandbox_launch(program: str, args: list[str] | None = None, cwd: str = "", name: str = "",
                   ports: list[str] | None = None, env: dict[str, str] | None = None,
                   files: dict[str, str] | None = None, memory_mb: int = 4096, pretty: bool = False) -> str:
    """Start an app or server that keeps running, inside the sandbox (its own profile folders,
    a memory cap, stopped with its whole process tree by sandbox_stop or when Ducky closes).

    program: a path inside the sandbox, or a runtime on its PATH (node, python, npm.cmd, uv).
    ports: environment variable names that each get a free port (e.g. ["PORT"]; a test Ducky:
    ["UEFN_DUCKY_PANEL_PORT", "UEFN_DUCKY_PORT"]); the ports come back and count as this
    sandbox's. "%PORT%" (any ports or env name) in args becomes its value. files: {path inside the sandbox: text} written first (e.g. a program's settings
    file under appdata/Local). Then sandbox_look, browser_open('localhost:<port>'), sandbox_http
    or sandbox_mcp. Asks first unless the chat allows sandbox commands."""
    command = " ".join([program, *[str(a) for a in (args or [])]])
    why = _refused(command) or _approved("Start a program", command)
    if why:
        return tool_json({"ok": False, "error": why}, pretty=pretty)
    return tool_json(_forward("launch", program=program, args=list(args or []), cwd=cwd, name=name,
                              ports=list(ports or []), env=dict(env or {}), files=dict(files or {}),
                              memory_mb=memory_mb), pretty=pretty)


@mcp.tool()
def sandbox_look(pid: int = 0, capture: bool = False, hwnd: int = 0, tail_chars: int = 4000, pretty: bool = False) -> Any:
    """A sandbox program's state: alive, memory, CPU, listening ports, recent output and its
    windows; capture=true (or an hwnd) saves a picture of its window in this chat (open it
    with view_image). No pid: every program running in this chat's sandbox."""
    out = _forward("look", pid=pid, capture_window=bool(capture), hwnd=hwnd, tail_chars=tail_chars)
    shot = out.get("capture") if isinstance(out.get("capture"), dict) else {}
    path = str(shot.get("path") or "")
    if path:
        try:
            from backend.tools.core.model_picture import model_picture

            return [tool_json(out, pretty=pretty), model_picture(path)]
        except Exception:
            pass
    return tool_json(out, pretty=pretty)


@mcp.tool()
def sandbox_http(port: int, method: str = "GET", path: str = "/", body: Any = None,
                 headers: dict[str, str] | None = None, pretty: bool = False) -> str:
    """Call a sandbox program's own local web API (127.0.0.1, only ports this chat's sandbox
    programs use), e.g. a test Ducky's panel API: POST path='__panel_api/<method>' with
    body={"args": {...}} on its panel port (UEFN_DUCKY_PANEL_PORT minus one)."""
    return tool_json(_forward("http", port=port, method=method, path=path, body=body, headers=dict(headers or {})),
                     pretty=pretty)


@mcp.tool()
def sandbox_mcp(command: str = "", args: list[str] | None = None, url: str = "", calls: list[dict] | None = None,
                list_tools: bool = False, env: dict[str, str] | None = None, pretty: bool = False) -> str:
    """Call tools on an MCP server the sandbox runs, in one session: start one (command = a
    program inside the sandbox, e.g. a test Ducky's UEFN-Ducky-Bridge.exe with args
    ["bridge", "--port", "<its UEFN_DUCKY_PORT>"] and env {"UEFN_DUCKY_PANEL_PORT": ..., "DUCKY_CONV_ID": ...})
    or reach one at url on a port the sandbox owns. calls = [{"tool": name, "arguments": {...}}, ...].
    Pictures a tool returns are saved in this chat. Asks first when it starts a program."""
    if command:
        why = _refused(" ".join([command, *[str(a) for a in (args or [])]])) or _approved(
            "Start an MCP server", " ".join([command, *[str(a) for a in (args or [])]]))
        if why:
            return tool_json({"ok": False, "error": why}, pretty=pretty)
    return tool_json(_forward("mcp", command=command, args=list(args or []), url=url, calls=list(calls or []),
                              list_tools=bool(list_tools), env=dict(env or {})), pretty=pretty)


@mcp.tool()
def sandbox_stop(pid: int = 0, pretty: bool = False) -> str:
    """Stop one sandbox program with everything it started (pid), or all of this chat's (pid=0)."""
    return tool_json(_forward("stop", pid=pid), pretty=pretty)


@mcp.tool()
def sandbox_clear(pretty: bool = False) -> str:
    """Stop this chat's sandbox programs, remove its worktrees and delete the whole sandbox."""
    return tool_json(_forward("clear"), pretty=pretty)
