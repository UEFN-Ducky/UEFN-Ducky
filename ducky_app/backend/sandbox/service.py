"""The sandbox's work, done in the Ducky window's process.

The window owns every sandbox program (their jobs, logs and ports), its terminals, and
the chat panel that shows them; an agent's tool server is another process, so the tools
hand calls here (backend.tools.core.sandbox_tools). Approval cards are asked before the
hand-off, in the agent's own chat; nothing here asks.

Nothing grows without bound: dead programs leave the registry the next time anything
looks, every log is capped, and a stopped program's job, handles and log thread are
freed at once.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.sandbox import capture, manifest, paths
from backend.sandbox.env import free_port, sandbox_env
from backend.sandbox.jobs import Launched, read_tail
from backend.sandbox.policy import refusal

MAX_PROGRAMS_PER_CHAT = 8
MAX_PROGRAMS = 24
_SIZE_TTL_S = 30.0
_HTTP_MAX = 200_000
_LOCK_POLL_S = 2.0


@dataclass
class _Program:
    conv_id: str
    name: str
    argv: list[str]
    cwd: str
    started: float
    ports: dict[str, int]
    run: Launched
    exit_code: int | None = None
    stopped: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


_lock = threading.RLock()
_programs: dict[int, _Program] = {}
_sizes: dict[str, tuple[float, int]] = {}


def _sandbox(conv_id: str) -> Path:
    return paths.sandbox_dir(conv_id, create=True)


def _reap() -> None:
    """Drop programs that ended on their own (their job, handles and log thread too)."""
    with _lock:
        for pid, prog in list(_programs.items()):
            if not prog.run.alive():
                prog.exit_code = prog.run.stop(timeout_s=2.0)
                _programs.pop(pid, None)
                try:
                    manifest.record(paths.sandbox_dir(prog.conv_id), "exited", pid=pid, name=prog.name,
                                    exit_code=prog.exit_code)
                except (ValueError, OSError):
                    pass


def _program_row(pid: int, prog: _Program, *, detail: bool = False, procs: Any = None,
                 listening: dict[int, set[int]] | None = None, cpu: dict[int, float] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "pid": pid, "name": prog.name, "started": round(prog.started, 1), "ports": prog.ports,
        "alive": prog.run.alive(), "log": str(prog.run.log),
    }
    if detail:
        row["command"] = prog.argv
        row["cwd"] = prog.cwd
        row.update(_usage(prog.run.tree(), procs=procs, listening=listening, cpu=cpu))
    return row


def _listening() -> dict[int, set[int]]:
    """Listening TCP ports by process: one system-wide table read, not one per process."""
    try:
        import psutil

        conns = psutil.net_connections(kind="tcp")
    except Exception:  # noqa: BLE001 - no psutil, or AccessDenied on some systems
        return {}
    out: dict[int, set[int]] = {}
    for conn in conns:
        if conn.status == "LISTEN" and conn.laddr and conn.pid:
            out.setdefault(int(conn.pid), set()).add(int(conn.laddr.port))
    return out


def _usage(pids: list[int], *, procs: Any = None, listening: dict[int, set[int]] | None = None,
           cpu: dict[int, float] | None = None) -> dict[str, Any]:
    from backend.util import proc_table

    procs = proc_table.table() if procs is None else procs
    listening = _listening() if listening is None else listening
    mem = sum(procs[p].rss for p in pids if p in procs)
    out: dict[str, Any] = {"processes": len(pids), "memory_mb": round(mem / 1024**2, 1),
                           "listening": sorted({port for p in pids for port in listening.get(p, ())})}
    if cpu is not None:
        out["cpu_percent"] = round(sum(cpu.get(p, 0.0) for p in pids), 1)
    return out


def size_of(conv_id: str) -> int:
    now = time.monotonic()
    hit = _sizes.get(conv_id)
    if hit and now - hit[0] < _SIZE_TTL_S:
        return hit[1]
    size = paths.dir_size(paths.sandbox_dir(conv_id)) if paths.exists(conv_id) else 0
    _sizes[conv_id] = (now, size)
    if len(_sizes) > 500:
        _sizes.pop(next(iter(_sizes)))
    return size


def _runtimes() -> dict[str, str]:
    from backend.sandbox.runtimes import installed

    return installed()


# ── what an agent calls (through backend.tools.core.sandbox_tools) ──────────────

def open_sandbox(conv_id: str) -> dict[str, Any]:
    box = _sandbox(conv_id)
    _reap()
    size = size_of(conv_id)
    env = sandbox_env(box)
    return {
        "ok": True,
        "path": str(box),
        "work": str(box / "work"),
        "downloads": str(box / "downloads"),
        "profile": {k: env[k] for k in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP")},
        "size_mb": round(size / 1024**2, 1),
        "size_warning": size > paths.SIZE_WARN_BYTES,
        "runtimes": _runtimes(),
        "programs": [_program_row(pid, p) for pid, p in _programs.items() if p.conv_id == conv_id],
        "manifest": manifest.summary(box, last=10),
    }


def run_command(conv_id: str, command: str, shell: str = "bash", timeout_s: float = 600.0) -> dict[str, Any]:
    """Run ``command`` in the chat's sandbox terminal (visible in the header's terminal list)."""
    why = refusal(command)
    if why:
        return {"ok": False, "error": why}
    box = _sandbox(conv_id)
    from frontend.ui_web.agent_modes import get_panel_push
    from frontend.ui_web.terminal import get_terminal_manager

    mgr = get_terminal_manager()
    push = get_panel_push()
    if push is not None:
        mgr.set_push(push)
    env = sandbox_env(box)
    changed = {k: v for k, v in env.items() if os.environ.get(k) != v}
    manifest.record(box, "run", command=command)
    from frontend.ui_web.terminal import repo_lock

    lock = _source_repo(box, command) if repo_lock.is_heavy(command) else ""
    waited: list[str] = []

    def waiting(holder: str) -> None:
        waited.append(f"Waiting for {holder}'s test run in this repo")
        manifest.record(box, "waiting", note=waited[0])

    if lock:
        # Sandboxes of one repo in different chats share the person's CPU and memory: one
        # test or build run per source repo, as in a project terminal.
        repo_lock.acquire(lock, _chat_label(conv_id), on_wait=waiting)
    session, result = "", None
    try:
        session, result = _run_in_terminal(mgr, box, conv_id, command, shell, timeout_s, changed)
    finally:
        if lock:
            _release_when_done(mgr, session, lock, result)
    paths.touch(box)
    _sizes.pop(conv_id, None)
    if not isinstance(result, dict):
        return {"ok": False, "error": "no result"}
    return {"session_id": session, **result, **({"waited": waited[0]} if waited else {})}


def _run_in_terminal(mgr: Any, box: Path, conv_id: str, command: str, shell: str, timeout_s: float,
                     changed: dict[str, str]) -> tuple[str, Any]:
    session, result = "", None
    for reuse in (True, False):
        opened = mgr.spawn(shell=shell, cwd=str(box / "work"), title="sandbox", push_open=True, conv_id=conv_id,
                           env_extra=changed, activate=False, reuse_idle=reuse)
        session = str(opened.get("session_id") or "")
        if not session:
            return "", {"ok": False, "error": str(opened.get("error") or "The sandbox terminal did not open.")}
        result = mgr.run_agent_command(session, command, source="sandbox_run", conv_id=conv_id, wait=True,
                                       command_timeout_s=max(5.0, min(float(timeout_s), 3600.0)), auto_approve=True)
        # The idle-terminal cleanup can close a reused shell just as it is handed out: once more in a new one.
        if not (isinstance(result, dict) and result.get("error") == "session not found"):
            break
    return session, result


def _source_repo(box: Path, command: str) -> str:
    """The person's repo a sandbox test run tests: its worktree's source (the one named in
    the command when there are several). '' when the sandbox has no worktree."""
    from backend.sandbox import gitops
    from frontend.ui_web.terminal import repo_lock

    rows = [r for r in gitops._records(box) if r.get("repo")]
    repos = {repo_lock.repo_of(r["repo"]) for r in rows}
    if len(repos) == 1:
        return repos.pop()
    named = [r for r in rows if r.get("path") and Path(r["path"]).name in command]
    return repo_lock.repo_of(named[0]["repo"]) if len(named) == 1 else ""


def _chat_label(conv_id: str) -> str:
    try:
        from frontend.ui_web.terminal.manager import _chat_title

        return _chat_title(conv_id) or "another sandbox"
    except Exception:
        return "another sandbox"


def _release_when_done(mgr: Any, session: str, repo: str, result: Any) -> None:
    """Release now, or, for a run that came back early (still_running), once its terminal
    is no longer busy: the program still runs and still holds the repo."""
    from frontend.ui_web.terminal import repo_lock

    if not (isinstance(result, dict) and result.get("still_running") and session):
        repo_lock.release(repo)
        return

    def watch() -> None:
        # ponytail: polls every 2 s; a done event from the manager would be exact.
        try:
            while True:
                s = mgr.get_session(session)
                if s is None or not s.is_busy():
                    return
                time.sleep(_LOCK_POLL_S)
        finally:
            repo_lock.release(repo)

    threading.Thread(target=watch, daemon=True, name=f"sandbox-lock-{session}").start()


def launch(conv_id: str, program: str, args: list[str] | None = None, cwd: str = "", name: str = "",
           ports: list[str] | None = None, env: dict[str, str] | None = None,
           files: dict[str, str] | None = None, memory_mb: int = 4096) -> dict[str, Any]:
    """Start an app or server inside a job, with the sandbox's environment and free ports."""
    argv = [program, *[str(a) for a in (args or [])]]
    why = refusal(" ".join(shlex.quote(a) for a in argv))
    if why:
        return {"ok": False, "error": why}
    box = _sandbox(conv_id)
    _reap()
    with _lock:
        mine = [p for p in _programs.values() if p.conv_id == conv_id]
        if len(mine) >= MAX_PROGRAMS_PER_CHAT or len(_programs) >= MAX_PROGRAMS:
            return {"ok": False, "error": f"Too many sandbox programs running ({len(mine)} here, {len(_programs)} in all). "
                                          "Stop some with sandbox_stop first."}
    workdir = Path(cwd) if cwd else box / "work"
    if not workdir.is_absolute():
        # Relative to the work folder ("app"), or to the sandbox itself ("work/app", "downloads/x").
        rel = workdir
        workdir = box / "work" / rel
        if not workdir.is_dir() and (box / rel).is_dir():
            workdir = box / rel
    if not paths.is_sandbox_path(str(workdir), conv_id) or not workdir.is_dir():
        return {"ok": False, "error": f"cwd must be a folder inside this chat's sandbox: {workdir}"}
    exe = Path(program)
    if exe.is_absolute() or os.sep in program or "/" in program:
        exe = exe if exe.is_absolute() else workdir / exe
        if not exe.exists():
            return {"ok": False, "error": f"No such program: {exe}"}
        argv[0] = str(exe)
    for rel, content in (files or {}).items():
        target = box / rel
        if not paths.is_sandbox_path(str(target), conv_id):
            return {"ok": False, "error": f"files must stay inside the sandbox: {rel}"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
    port_map = {str(var): free_port() for var in (ports or []) if str(var).strip()}
    run_env = sandbox_env(box, {**(env or {}), **{k: str(v) for k, v in port_map.items()}})
    if not Path(argv[0]).is_absolute():
        # Windows looks a bare name up on Ducky's own PATH, not the sandbox's runtimes.
        found = shutil.which(argv[0], path=run_env.get("PATH"))
        if not found:
            return {"ok": False, "error": f"{program} isn't on the sandbox's PATH (sandbox_setup a runtime, or pass a path)."}
        argv[0] = found
    log = box / "logs" / f"{int(time.time() * 1000)}.log"
    try:
        run = Launched(argv, cwd=str(workdir), env=run_env, log=log, memory_mb=memory_mb)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"Could not start {program}: {exc}"}
    prog = _Program(conv_id=conv_id, name=name or Path(argv[0]).name, argv=argv, cwd=str(workdir),
                    started=time.time(), ports=port_map, run=run)
    with _lock:
        _programs[run.pid] = prog
    # program/args/cwd/env too: the Apps menu runs it again later from this record (relaunch).
    manifest.record(box, "launch", pid=run.pid, name=prog.name, command=argv, ports=port_map,
                    program=program, args=[str(a) for a in (args or [])], cwd=str(workdir), env=env or {})
    paths.touch(box)
    time.sleep(1.0)
    alive = run.alive()
    out = {"ok": alive, "pid": run.pid, "name": prog.name, "ports": port_map, "log": str(log),
           "output": read_tail(log, 1500)}
    if not alive:
        _reap()
        out["error"] = "It exited right away. Its output is above."
    else:
        out["next"] = ("sandbox_look(pid) shows its windows, output and listening ports (capture=true for a picture); "
                       "browser_open('localhost:<port>') shows a web app; sandbox_http / sandbox_mcp talk to it.")
    return out


def look(conv_id: str, pid: int = 0, capture_window: bool = False, hwnd: int = 0, tail_chars: int = 4000) -> dict[str, Any]:
    _reap()
    with _lock:
        mine = {p: prog for p, prog in _programs.items() if prog.conv_id == conv_id}
    if not pid:
        return {"ok": True, "programs": _rows(mine.items())}
    prog = mine.get(int(pid))
    if prog is None:
        return {"ok": False, "error": f"No running sandbox program {pid} in this chat (it may have exited; see sandbox_open)."}
    tree = prog.run.tree()
    out: dict[str, Any] = {"ok": True, **_program_row(int(pid), prog, detail=True),
                           "output": read_tail(prog.run.log, max(500, min(int(tail_chars), 20000))),
                           "windows": capture.windows_of(tree)}
    if capture_window or hwnd:
        wins = out["windows"]
        target = next((w for w in wins if w["hwnd"] == int(hwnd)), None) if hwnd else (wins[0] if wins else None)
        if target is None:
            out["capture"] = {"ok": False, "error": "It shows no window right now."}
        else:
            shot = capture.capture(target, conv_id)
            out["capture"] = shot
            if shot.get("ok"):
                manifest.record(paths.sandbox_dir(conv_id), "evidence", pid=pid, picture=shot.get("path"))
    return out


def stop(conv_id: str, pid: int = 0) -> dict[str, Any]:
    with _lock:
        targets = [(p, prog) for p, prog in _programs.items()
                   if prog.conv_id == conv_id and (not pid or p == int(pid))]
    if pid and not targets:
        _reap()
        return {"ok": False, "error": f"No running sandbox program {pid} in this chat."}
    stopped = []
    for p, prog in targets:
        code = prog.run.stop()
        with _lock:
            _programs.pop(p, None)
        stopped.append({"pid": p, "name": prog.name, "exit_code": code})
        manifest.record(paths.sandbox_dir(conv_id), "stop", pid=p, name=prog.name)
    return {"ok": True, "stopped": stopped}


def stop_everything() -> int:
    """Every sandbox program of every chat (Ducky quitting, or self-care)."""
    with _lock:
        items = list(_programs.items())
    for p, prog in items:
        try:
            prog.run.stop(timeout_s=3.0)
        except Exception:
            pass
        with _lock:
            _programs.pop(p, None)
    return len(items)


def clear(conv_id: str) -> dict[str, Any]:
    """Stop the chat's programs, remove its worktrees, delete its sandbox folder."""
    stop(conv_id)
    from backend.sandbox import gitops

    removed_worktrees = gitops.remove_worktrees(conv_id) if paths.exists(conv_id) else []
    failed = paths.remove_tree(paths.sandbox_dir(conv_id))
    _sizes.pop(conv_id, None)
    return {"ok": not failed, "worktrees_removed": removed_worktrees, "could_not_remove": failed[:10]}


def owned_ports(conv_id: str = "") -> set[int]:
    """Ports given to, or listened on by, sandbox programs (one chat's, or every chat's)."""
    _reap()
    ports: set[int] = set()
    with _lock:
        progs = [p for p in _programs.values() if not conv_id or p.conv_id == conv_id]
    listening = _listening() if progs else {}
    for prog in progs:
        ports.update(int(v) for v in prog.ports.values())
        for pid in prog.run.tree():
            ports.update(listening.get(pid, ()))
    return ports


def http(conv_id: str, port: int, method: str = "GET", path: str = "/", body: Any = None,
         headers: dict[str, str] | None = None, timeout_s: float = 30.0) -> dict[str, Any]:
    """Call a sandbox program's own local web API (only ports its sandbox owns)."""
    if int(port) not in owned_ports(conv_id):
        return {"ok": False, "error": f"Port {port} isn't one this chat's sandbox programs use."}
    url = f"http://127.0.0.1:{int(port)}/{str(path or '/').lstrip('/')}"
    data = None
    sent = dict(headers or {})
    if body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        sent.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=(method or "GET").upper(), headers=sent)
    try:
        with urllib.request.urlopen(req, timeout=max(1.0, min(float(timeout_s), 300.0))) as resp:
            raw = resp.read(_HTTP_MAX + 1)
            status = resp.status
            ctype = resp.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        raw, status, ctype = exc.read(_HTTP_MAX + 1), exc.code, exc.headers.get("content-type", "")
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "error": f"Nothing answered on port {port}: {exc}"}
    text = raw[:_HTTP_MAX].decode("utf-8", "replace")
    out: dict[str, Any] = {"ok": status < 400, "status": status, "content_type": ctype,
                           "truncated": len(raw) > _HTTP_MAX}
    try:
        out["json"] = json.loads(text) if "json" in ctype else None
    except ValueError:
        out["json"] = None
    if out["json"] is None:
        out["text"] = text
    return out


def status(conv_id: str) -> dict[str, Any]:
    """What the chat panel's Sandbox section shows."""
    if not paths.exists(conv_id):
        return {"ok": True, "exists": False}
    box = paths.sandbox_dir(conv_id)
    _reap()
    with _lock:
        mine = [(p, prog) for p, prog in _programs.items() if prog.conv_id == conv_id]
    progs = _rows(mine)
    events = manifest.summary(box, last=40)
    from frontend.ui_web.conversation_attachments import build_chat_attachment_url

    pictures = [{"path": str(e["picture"]), "media_url": build_chat_attachment_url(conv_id, Path(str(e["picture"])).name)}
                for e in events["recent"] if e.get("kind") == "evidence" and e.get("picture")]
    size = size_of(conv_id)
    return {"ok": True, "exists": True, "path": str(box), "size_mb": round(size / 1024**2, 1),
            "size_warning": size > paths.SIZE_WARN_BYTES, "programs": progs, "events": events,
            "pictures": pictures[-8:]}


def _rows(progs: Any, procs: Any = None, cpu: dict[int, float] | None = None) -> list[dict[str, Any]]:
    """Detailed rows for several programs from one process table and one port table, built
    outside the registry lock."""
    progs = list(progs)
    if not progs:
        return []
    from backend.util import proc_table

    procs = proc_table.table() if procs is None else procs
    listening = _listening()
    return [_program_row(p, prog, detail=True, procs=procs, listening=listening, cpu=cpu) for p, prog in progs]


def all_programs(procs: Any = None, cpu: dict[int, float] | None = None) -> list[dict[str, Any]]:
    """Every running sandbox program (for ducky_health, which passes its own process table and CPU)."""
    _reap()
    with _lock:
        progs = list(_programs.items())
    return [{"conv_id": prog.conv_id, **row} for (_, prog), row in zip(progs, _rows(progs, procs, cpu))]


def _launches(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The latest launch event of each app name."""
    return {str(e["name"]): e for e in events if e.get("kind") == "launch" and e.get("name")}


def _events(box: Path) -> list[dict[str, Any]]:
    return [e for e in manifest.read(box).get("events") or [] if isinstance(e, dict)]


def apps() -> dict[str, Any]:
    """Every app a sandbox launched, in every chat: running ones first, then newest."""
    _reap()
    with _lock:
        running = {(p.conv_id, p.name): (pid, p) for pid, p in _programs.items()}
    listening = _listening() if running else {}
    from frontend.ui_web.conversation_attachments import build_chat_attachment_url

    out: list[dict[str, Any]] = []
    for row in paths.list_sandboxes():
        conv = row["conv_id"]
        events = _events(Path(row["path"]))
        pics = [str(e["picture"]) for e in events if e.get("kind") == "evidence" and e.get("picture")]
        picture = build_chat_attachment_url(conv, Path(pics[-1]).name) if pics else ""
        for name, e in _launches(events).items():
            hit = running.get((conv, name))
            ports = sorted({*(hit[1].ports.values() if hit else ()),
                            *(port for pid in (hit[1].run.tree() if hit else ()) for port in listening.get(pid, ()))})
            out.append({"conv_id": conv, "name": name, "running": bool(hit), "pid": hit[0] if hit else 0,
                        "ports": ports, "launched": e.get("at") or 0, "picture": picture})
    out.sort(key=lambda a: (not a["running"], -float(a["launched"])))
    return {"ok": True, "apps": out}


def relaunch(conv_id: str, name: str) -> dict[str, Any]:
    """Start an app again the way its last launch did (already running: that one)."""
    with _lock:
        for pid, prog in _programs.items():
            if prog.conv_id == conv_id and prog.name == name:
                return {"ok": True, "pid": pid, "name": name, "ports": prog.ports, "already_running": True}
    e = _launches(_events(paths.sandbox_dir(conv_id))).get(name) if paths.exists(conv_id) else None
    if e is None:
        return {"ok": False, "error": f"No app named {name!r} was launched in this sandbox."}
    program, args = e.get("program"), e.get("args")
    if not program:  # launched before the record kept program/args
        command = [str(c) for c in e.get("command") or []]
        if not command:
            return {"ok": False, "error": f"{name} has no recorded command."}
        program, args = command[0], command[1:]
    # ponytail: manifest fields are cut at 600 chars / 40 items; a longer command won't replay.
    return launch(conv_id, str(program), list(args or []), cwd=str(e.get("cwd") or ""), name=name,
                  ports=list(e.get("ports") or {}), env=e.get("env") or None)


def prune(chat_exists: Any = None) -> list[str]:
    """Delete sandboxes unused for two weeks, or whose chat is gone (none with a running program,
    none that launched an app: those stay until the person deletes them in the Apps menu)."""
    with _lock:
        busy = {p.conv_id for p in _programs.values()}
    removed = []
    for conv_id in paths.stale(chat_exists=chat_exists):
        if conv_id not in busy and not _launches(_events(paths.sandbox_dir(conv_id))):
            clear(conv_id)
            removed.append(conv_id)
    return removed


def fetch(conv_id: str, url: str, name: str = "", sha256: str = "", max_mb: int = 800) -> dict[str, Any]:
    from backend.sandbox.fetch import FetchError, download, safe_name

    box = _sandbox(conv_id)
    dest = box / "downloads" / safe_name(name or url)
    try:
        got = download(url, dest, expect_sha256=sha256, max_bytes=max(1, min(int(max_mb), 800)) * 1024**2)
    except FetchError as exc:
        return {"ok": False, "error": str(exc)}
    manifest.record(box, "download", url=url, path=got["path"], sha256=got["sha256"], bytes=got["bytes"])
    _sizes.pop(conv_id, None)
    return {"ok": True, **got}


def _clone(conv_id: str, repo: str, ref: str = "", name: str = "") -> dict[str, Any]:
    from backend.sandbox import gitops

    return gitops.clone(conv_id, repo, ref=ref, name=name)


def _worktree(conv_id: str, repo_path: str, commit: str = "HEAD", name: str = "", copy: list[str] | None = None,
              copy_from: str = "") -> dict[str, Any]:
    from backend.sandbox import gitops

    return gitops.worktree(conv_id, repo_path, commit=commit, name=name, copy=copy, copy_from=copy_from)


def _setup(conv_id: str, runtime: str) -> dict[str, Any]:
    from backend.sandbox import runtimes

    out = runtimes.setup(runtime)
    if out.get("ok"):
        manifest.record(_sandbox(conv_id), "setup", runtime=runtime, path=out.get("path"), version=out.get("version"))
    return out


def _mcp(conv_id: str, **kwargs: Any) -> dict[str, Any]:
    from backend.sandbox import mcp_client

    return mcp_client.call(conv_id, **kwargs)


_last_prune = 0.0


def prune_now_and_then() -> None:
    """At most once an hour: old sandboxes, and ones whose chat was deleted."""
    global _last_prune
    if time.monotonic() - _last_prune < 3600:
        return
    _last_prune = time.monotonic()
    try:
        from backend.tools.panel.permission_prompt import _chat_exists

        prune(chat_exists=_chat_exists)
    except Exception:
        pass


_CALLS: dict[str, Any] = {
    "open": open_sandbox, "run": run_command, "launch": launch, "look": look, "stop": stop, "clear": clear,
    "http": http, "status": status, "fetch": fetch, "clone": _clone, "worktree": _worktree, "setup": _setup,
    "mcp": _mcp, "relaunch": relaunch,
}


def call(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """One sandbox call by name (the panel API's ``sandbox_call`` lands here)."""
    fn = _CALLS.get(str(name or ""))
    if fn is None:
        return {"ok": False, "error": f"unknown sandbox call {name!r}"}
    kwargs = dict(args or {})
    try:
        paths.valid_conv(str(kwargs.get("conv_id") or ""))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    prune_now_and_then()
    try:
        out = fn(**kwargs)
    except TypeError as exc:
        return {"ok": False, "error": f"bad arguments for sandbox {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - every failure is the agent's to read, never a crash
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return out if isinstance(out, dict) else {"ok": True, "result": out}
