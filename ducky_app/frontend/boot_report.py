"""Crash reports: when Ducky closes by itself, the next launch says exactly what happened
and asks the user to send it.

Every GUI launch keeps a small boot record (version, Windows, each startup stage reached)
and turns on ``faulthandler``, which writes the Python stack of every thread when the
process crashes in native code. If the previous launch never finished starting, or
crashed later, the next launch shows a small window before anything that could crash
again: what happened, what the report holds (no personal data; the full text one click
away), and Send report / Don't send. Nothing is sent without that click. Sent reports go
to the uefnducky.org feedback form (``source: crash``); unsent ones stay in Settings →
Support with Send and Delete.

A launch whose window opens but whose page never starts (stuck on the loading splash)
counts as a crash too: after ``STUCK_AFTER_S`` the same pop-up shows at once, and if it
is never answered the next launch asks.

Standard library only: this runs before the rest of Ducky has loaded.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

COLLECT_URL = "https://uefnducky.org/api/v1/plugins/custom-forms/collect/submit"
ORIGIN = "https://uefnducky.org"
FORM_ID = "uefn-ducky-feedback"
FIELD_MAX = 7900  # the form keeps ~8 KB per field
SEND_TIMEOUT_S = 6.0
KEEP_REPORTS = 10
PRIVACY_NOTE = (
    "The report has the app and Windows version, the startup steps and the error. "
    "Never your chats, files, name, email or keys."
)

# States a launch can be in without having crashed while starting.
_QUIET_STATES = {"ok", "handoff", "closed", "stopped"}
# States only a deliberate exit writes. A launch left at "ok" never exited on purpose.
# "stopped": something ended it by PID on purpose and said so first (mark_stopped).
_CLEAN_ENDS = {"handoff", "closed", "stopped"}
NO_TRACE = (
    "No crash trace: Ducky ended outside Python code (WebView2, a DLL, running out of "
    "memory) or was ended from Task Manager."
)
# sent = the user sent it; kept = not sent (they chose not to, or sending failed).
_REPORT_ID = re.compile(r"^(?:sent|kept)-\d{8}-\d{6}(?:-\d+)?$")
# How long the window's page has, once the window shows, to run its main script.
STUCK_AFTER_S = 60.0
STUCK_HEADING = "UEFN Ducky got stuck while starting"

_state: dict[str, Any] = {}
_state_lock = threading.Lock()  # stage(), mark() and the heartbeat thread share boot.tmp
_native_file: Any = None
# begin(ask=False): the kept report the pop-up asks about once ask_pending() runs.
_pending: dict[str, Any] = {}
HEARTBEAT_S = 30.0
_heartbeat: dict[str, Any] = {"thread": None}
# The window's page: started = its main script ran; errors = what it hit before that.
# webview = the first window's WebView2 start result ({ok, error}); None until it comes.
_page: dict[str, Any] = {"started": False, "watching": False, "errors": [], "webview": None}


def _dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "UEFN-Ducky" / "crash"


def _boot_path() -> Path:
    return _dir() / "boot.json"


def _native_path() -> Path:
    return _dir() / "native.log"


def _test_copy() -> bool:
    """A test copy (sandbox_launch, the soak harness: both set DUCKY_SANDBOX). Nobody is
    there to answer the crash pop-up, so it never shows; reports are kept, never sent."""
    return bool(os.environ.get("DUCKY_SANDBOX"))


def _stopped_path(folder: Path | None = None) -> Path:
    return (folder or _dir()) / "stopped.json"


def mark_stopped(pid: int, local_appdata: Path | str | None = None) -> bool:
    """Whoever ends a Ducky by PID on purpose (sandbox_stop, a test harness) calls this just
    before the kill, so that Ducky's next start doesn't report "closed by itself".

    *local_appdata* is the stopped Ducky's LOCALAPPDATA (a sandbox's), default this one's.
    A file of its own, not boot.json: the running app rewrites boot.json every 30 s and
    would write over the mark."""
    folder = Path(local_appdata) / "UEFN-Ducky" / "crash" if local_appdata else _dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "stopped.tmp"
        tmp.write_text(json.dumps({"pid": int(pid), "at": time.time()}), encoding="utf-8")
        tmp.replace(_stopped_path(folder))
    except (OSError, ValueError):
        return False
    return True


def _scrub(text: str) -> str:
    try:
        from backend.util.privacy import scrub

        return scrub(text)
    except Exception:
        home = str(Path.home())
        return text.replace(home, "~").replace(home.replace("\\", "/"), "~") if home else text


def _write_state() -> None:
    try:
        with _state_lock:
            path = _boot_path()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(_state), encoding="utf-8")
            tmp.replace(path)
    except (OSError, RuntimeError, ValueError):
        pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _still_running(pid: int) -> bool:
    """True when *pid* is another Ducky process that is still running.

    A launch still starting (slow, not crashed) leaves the same record as one that
    crashed; opening Ducky again must not call it a crash."""
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        )
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
                return False
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return False
            # A reused process id belongs to some other program.
            return Path(buf.value).name.lower() == Path(sys.executable).name.lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _loading_plugins(process: int) -> list[str]:
    """Plugins the crashed process was loading (the crash guard's markers)."""
    folder = Path(_dir().parent) / "uefn_plugins" / ".loading"
    out: list[str] = []
    for marker in folder.glob(f"*.{process}.json") if folder.is_dir() else []:
        info = _read_json(marker)
        if info.get("plugin"):
            out.append(f"{info.get('plugin')} {info.get('version') or ''}".strip())
    return out


_FAULT_HEAD = re.compile(r"(?m)^(?:Windows fatal exception|Fatal Python error)")
NATIVE_MAX = 5000
_CRASHED_STACK_MAX = 2000  # the crashing thread: its top frames come first
_OTHER_STACK_MAX = 1200


_EARLIER_MAX = 600
# faulthandler heads the crashing thread "Current thread 0x…" in an all-threads dump and
# "Stack (most recent call first):" when it dumps that thread alone (all_threads=False).
_CRASHED_HEADS = ("Current thread ", "Stack (most recent call first):")
_CRASHED_TOP = re.compile(
    r"(?m)^(?:Current thread [^\n]*|Stack \(most recent call first\):)\n((?:[ \t]+File[^\n]*(?:\n|$)){1,3})"
)


def _earlier_faults(text: str, starts: list[int]) -> str:
    """One short line per earlier dump of the launch: its fault and the top of the thread that
    crashed. Reports said only "(2 earlier exceptions in this launch not shown)" (fbr4t8v2n6x1c,
    Oct 2026), though the first one may name what started the crash."""
    lines = []
    for a, b in zip(starts[:-1], starts[1:]):
        dump = text[a:b].strip()
        first = dump.splitlines()[0]
        head = _FAULT_HEAD.sub("", first).lstrip(" :") or first
        top = _CRASHED_TOP.search(dump)
        frames = [f.strip() for f in top.group(1).splitlines() if f.strip()] if top else []
        lines.append("; ".join([head, *frames]))
    out = f"Earlier in this launch ({len(lines)}): " + " | ".join(lines)
    return out if len(out) <= _EARLIER_MAX else out[: _EARLIER_MAX - 1] + "…"


def trim_native(native: str, limit: int = NATIVE_MAX) -> str:
    """The last crash dump in the native log, cut to about *limit* without losing what matters.

    A dump is the fault line, then one stack per thread (newest thread first, the main
    thread last). Cutting from the front dropped the fault line and the thread that
    crashed, so the report could not say what crashed. Kept first: the fault line, the
    crashing thread, the main thread; then other threads while they fit.
    """
    text = (native or "").strip()
    starts = [m.start() for m in _FAULT_HEAD.finditer(text)]
    # The earlier dumps' fault lines lose their "Windows fatal exception" head, so the last dump
    # stays the only one a reader (or why_summary) takes for the crash.
    note = _earlier_faults(text, starts) if len(starts) > 1 else ""
    if starts:
        text = text[starts[-1]:]
    if starts and not any("\n" + head in text for head in _CRASHED_HEADS):
        # faulthandler names the crashing thread's stack only when that thread runs Python.
        note = (note + "\n" if note else "") + "(The crash was in a thread that runs no Python code: native code such as WebView2, .NET or a DLL.)"
    if len(text) + len(note) <= limit:
        return "\n\n".join(p for p in (note, text) if p)
    blocks = [b.strip("\n") for b in text.split("\n\n") if b.strip()]

    def cap(block: str, size: int) -> str:
        return block if len(block) <= size else block[:size].rsplit("\n", 1)[0] + "\n  …"

    crashed = {i for i, b in enumerate(blocks) if b.startswith(_CRASHED_HEADS)}
    kept: dict[int, str] = {0: cap(blocks[0], 400)}
    for i in crashed:
        kept[i] = cap(blocks[i], _CRASHED_STACK_MAX)
    kept[len(blocks) - 1] = cap(blocks[-1], _OTHER_STACK_MAX)
    budget = limit - len(note) - 40 - sum(len(b) + 2 for b in kept.values())
    for i, block in enumerate(blocks):
        if i in kept:
            continue
        piece = cap(block, _OTHER_STACK_MAX)
        if len(piece) + 2 <= budget:
            kept[i] = piece
            budget -= len(piece) + 2
    dropped = len(blocks) - len(kept)
    out = [kept[i] for i in sorted(kept)]
    if dropped:
        out.insert(len(out) - 1, f"({dropped} more thread{'s' if dropped != 1 else ''} not shown)")
    return "\n\n".join(([note] if note else []) + out)


def _pc_restarted_since(when: float) -> bool:
    """True when Windows started after *when*: a launch the PC's restart ended is no crash."""
    if os.name != "nt" or when <= 0:
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        booted = time.time() - kernel32.GetTickCount64() / 1000.0
    except Exception:
        return False
    return booted > when


def _webview_dumps_dir() -> Path:
    """Where WebView2's Crashpad keeps its dumps (the profile is ``storage_path`` in webview_app)."""
    return _dir().parent / "webview" / "EBWebView" / "Crashpad" / "reports"


def _dump_folders() -> list[tuple[Path, str, bool]]:
    """(folder, pattern, is the app's own dump)."""
    return [(_webview_dumps_dir(), "*.dmp", False), (_dir(), "ducky-*.dmp", True)]


def crash_dumps(since: float) -> list[dict[str, Any]]:
    """Dumps written since *since* (the previous launch's start), each read down to one line.
    The dump files themselves never leave the PC."""
    try:
        from frontend import minidump
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for folder, pattern, app in _dump_folders():
        for path in minidump.dumps_since(folder, since, pattern):
            info = minidump.read(path)
            try:
                at = path.stat().st_mtime
            except OSError:
                at = 0.0
            out.append({"name": path.name, "at": at, "summary": minidump.summary(info), "cause": minidump.cause(info, app=app)})
    return sorted(out, key=lambda d: d["at"])


def prune_dumps() -> None:
    try:
        from frontend import minidump
    except Exception:
        return
    for folder, pattern, _app in _dump_folders():
        minidump.prune(folder, pattern=pattern)


def _heartbeat_lines(beat: dict[str, Any], ended: float | None) -> list[str]:
    """The last memory heartbeat, and a cause line when the PC was nearly out of memory."""
    at = float(beat.get("at") or 0.0)
    secs = f"{max(0.0, ended - at):.0f} s" if ended else ""
    age = f"{secs} before the end" if secs else f"at most {HEARTBEAT_S:.0f} s before the end"
    parts = []
    free, total = beat.get("free_gb"), beat.get("total_gb")
    if free is not None:
        parts.append(f"{float(free):.1f} GB free" + (f" of {float(total):.0f} GB" if total else ""))
    if beat.get("commit_gb") is not None:
        parts.append(f"commit {float(beat['commit_gb']):.0f} of {float(beat.get('commit_limit_gb') or 0):.0f} GB")
    if beat.get("app_gb") is not None:
        parts.append(f"app {float(beat['app_gb']):.1f} GB")
    if beat.get("webview_gb") is not None:
        parts.append(f"WebView2 {float(beat['webview_gb']):.1f} GB")
    if beat.get("agents") is not None:
        parts.append(f"{int(beat['agents'])} agents")
    if beat.get("agents_n") is not None:
        parts.append(f"agent processes {int(beat['agents_n'])}, {size_bucket(beat.get('agents_mb'))}")
    if beat.get("threads") is not None:
        parts.append(f"{int(beat['threads'])} threads")
    lines = [f"Last heartbeat {age}: " + ", ".join(parts) + "."]
    others = [row for row in beat.get("top_other") or [] if isinstance(row, (list, tuple)) and len(row) == 2]
    if others:
        # Names only here: error_log goes to staff in a report the person chose to send.
        lines.append("Biggest other programs: " + ", ".join(f"{_scrub(str(n))} {size_bucket(m)}" for n, m in others) + ".")
    if free is not None and float(free) < 1.0:
        when = f"{secs} before" if secs else f"less than {HEARTBEAT_S:.0f} s before"
        lines.append(f"Why: The PC had {float(free):.1f} GB free {when} Ducky closed.")
    return lines


def _stage_rows(stages: Any) -> list[tuple[str, float]]:
    """``[[name, seconds], ...]`` as pairs; a row of any other shape (another writer, a newer
    version) is skipped, never raised on: a bad row must not stop the crash record."""
    rows: list[tuple[str, float]] = []
    for row in stages if isinstance(stages, list) else []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            rows.append((str(row[0]), float(row[1])))
        except (TypeError, ValueError):
            continue
    return rows


def _launcher_line(launcher: Any) -> str:
    """The native launcher's part of the record (the ``launcher`` key Ducky-Launcher.exe
    adds to boot.json): its stages and exit code. "" when there is none."""
    if not isinstance(launcher, dict):
        return ""
    stages = _stage_rows(launcher.get("stages"))
    parts = []
    if launcher.get("exit_code") is not None:
        parts.append(f"exit {launcher.get('exit_code')} {launcher.get('exit_reason') or ''}".strip())
    if stages:
        parts.append("stages " + ", ".join(f"{name} {offset:.1f}s" for name, offset in stages[-25:]))
    if launcher.get("error"):
        parts.append("error " + str(launcher["error"])[-500:])
    return ("Launcher: " + "; ".join(parts)) if parts else ""


# A launcher fallback starts this window within the launcher's 15 s hello wait plus its
# own start; an older launcher entry belongs to some other launch.
LAUNCHER_CARRY_S = 120.0


# The ``started`` of the launcher entry begin() carried into this record. The launcher
# writes its entry into whichever record is newest (the backend's own, or the last Python
# window's), so only this mark tells a carried entry from one the launcher wrote here.
LAUNCHER_CARRIED_KEY = "launcher_carried"


def _recent_launcher(launcher: Any, now: float, carried: Any = None) -> dict[str, Any] | None:
    """The launcher's part of the last record when it is from this launch (it fell back to
    this Python window): kept unchanged so the next record still says why. None otherwise,
    and None for the entry an earlier begin() already carried (an earlier launch's)."""
    if not isinstance(launcher, dict):
        return None
    try:
        started = float(launcher.get("started") or 0.0)
    except (TypeError, ValueError):
        return None
    if carried is not None and carried == launcher.get("started"):
        return None
    return dict(launcher) if 0.0 <= now - started <= LAUNCHER_CARRY_S else None


def _launcher_exited_clean(previous: dict[str, Any]) -> bool:
    """The launcher wrote exit 0 for this launch's backend (its window closed on purpose).
    A launcher entry begin() carried from an earlier launch, or one for another backend,
    says nothing about this one."""
    launcher = previous.get("launcher")
    if not isinstance(launcher, dict) or launcher.get("exit_code") != 0:
        return False
    if launcher.get("started") is not None and launcher.get("started") == previous.get(LAUNCHER_CARRIED_KEY):
        return False
    backend = launcher.get("backend_pid")
    return backend is None or backend == previous.get("pid")


def build_report(
    previous: dict[str, Any],
    native: str,
    *,
    restarted: bool = False,
    dumps: list[dict[str, Any]] | None = None,
    ended: float | None = None,
) -> dict[str, str] | None:
    """The form body for a launch that crashed or never finished starting; None if it was fine.

    A launch still at ``ok`` never exited on purpose (every exit marks ``closed``): it
    ended in a crash Python never saw, unless the PC restarted (*restarted*). *dumps* are
    the crash dumps written since it started (``crash_dumps``); *ended* is when it ended,
    if a dump or the native log says so."""
    dumps = list(dumps or [])
    state = str(previous.get("state") or "")
    native = (native or "").strip()
    if not previous or (state in _CLEAN_ENDS and not native):
        return None
    if state == "ok" and not native and restarted:
        return None
    if state == "ok" and not native and not dumps and _launcher_exited_clean(previous):
        return None  # tray Exit under Ducky-Launcher.exe: the launcher said exit 0
    if state == "stuck" and previous.get("reported") and not native:
        return None  # the pop-up already asked while that launch was open
    started = float(previous.get("started") or 0.0)
    stages = _stage_rows(previous.get("stages"))
    last = stages[-1][0] if stages else "launch"
    if native:
        when = "while starting" if state not in _QUIET_STATES else "while running"
        what = f"Ducky crashed {when} (last stage: {last})"
    elif state == "fatal":
        what = f"Ducky showed a startup error (last stage: {last})"
    elif state == "stuck":
        what = f"Ducky got stuck on its loading screen (last stage: {last})"
    elif state == "ok":
        what = "Ducky closed by itself while running"
    else:
        what = f"Ducky closed while starting, last stage reached: {last}"
    lines = [
        what,
        f"App {previous.get('version') or '?'} | {previous.get('os') or '?'} | Python {previous.get('python') or '?'}",
        "Started " + (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)) if started else "?"),
        "Stages: " + ", ".join(f"{name} {offset:.1f}s" for name, offset in stages[-25:]),
    ]
    launcher = _launcher_line(previous.get("launcher"))
    if launcher:
        lines.append(launcher)
    try:
        pid = int(previous.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    plugins = _loading_plugins(pid)
    if plugins:
        lines.append("Loading when it closed: " + ", ".join(plugins))
    if previous.get("error"):
        lines += ["", "Error:", str(previous["error"])]
    if previous.get("managed_error"):
        lines += ["", ".NET exception:", str(previous["managed_error"])]
    for dump in dumps[-3:]:
        lines.append(f"Crash dump {dump.get('name')}: {dump.get('summary')}")
    causes = [str(d["cause"]) for d in dumps if d.get("cause")]
    if causes:
        lines.append("Why: " + causes[-1])
    beat = previous.get("heartbeat")
    if isinstance(beat, dict) and beat.get("at"):
        lines += _heartbeat_lines(beat, ended)
    trend = _trend_line(previous.get("beats") or [])
    if trend:
        lines.append(trend)
    if native:
        lines += ["", "Crash traceback:", trim_native(native)]
    elif state == "ok" and not dumps:
        lines.append(NO_TRACE)
    return {
        "formId": FORM_ID,
        "message": f"Crash report: {what}",
        "source": "crash",
        "app_version": str(previous.get("version") or ""),
        "error_log": _scrub("\n".join(lines))[-FIELD_MAX:],
        "_hp": "",
    }


def report_text(body: dict[str, Any]) -> str:
    """Exactly what the report sends, as the user sees it."""
    parts = (str(body.get("message") or ""), "App " + str(body.get("app_version") or "?"), str(body.get("error_log") or ""))
    return "\n\n".join(p for p in parts if p)


def why_summary(body: dict[str, Any]) -> str:
    """Two or three plain lines for the pop-up: what happened and the likely cause."""
    log = str(body.get("error_log") or "").splitlines()
    out = [log[0] if log else "Ducky closed unexpectedly."]
    out += [line[len("Why: "):] for line in log if line.startswith("Why: ")][:2]
    for line in log:
        if line.startswith("Loading when it closed: "):
            out.append("It was loading the plugin " + line.split(": ", 1)[1] + ".")
            break
    for line in log:
        lowered = line.lower()
        if line == NO_TRACE or "fatal exception" in lowered or line.startswith(("ImportError", "RuntimeError", "OSError", "MemoryError", "Likely cause: ")):
            out.append(line.strip())
            break
    return "\n".join(out)


def _send(body: dict[str, str], version: str) -> bool:
    import urllib.request

    # HTTPS during launch: the Windows certificate store may still be loading on a thread.
    from frontend.ssl_boot import wait as wait_for_ssl_boot

    wait_for_ssl_boot()

    req = urllib.request.Request(
        COLLECT_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Origin": ORIGIN, "User-Agent": f"UEFN-Ducky/{version}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as resp:  # noqa: S310 — fixed https URL
            return 200 <= resp.status < 300
    except Exception:
        return False


def _ask_to_send(body: dict[str, str]) -> bool:
    """The crash pop-up (a plain native window: Ducky's own UI may be what crashes).
    True only when the user presses Send report."""
    try:
        import tkinter as tk

        root = tk.Tk()
    except Exception:
        return False
    choice = {"send": False}
    _fill_popup(root, body, lambda send: choice.update(send=send))
    try:
        root.mainloop()
    except Exception:
        pass
    return choice["send"]


def show_now(tk_root: Any, body: dict[str, str], reload: Callable[[], None] | None = None) -> None:
    """The crash pop-up while this launch is still open (it got stuck). Runs on the Tk
    pump thread. Answered = the next launch won't ask again; unanswered = it will.
    *reload* (reload the window's page) adds a Reload button to a stuck-page pop-up."""
    if _test_copy():
        _save(body, "kept")
        return
    import tkinter as tk

    def done(send: bool) -> None:
        _state["reported"] = True
        _write_state()
        version = str(_state.get("version") or "")
        threading.Thread(
            target=lambda: _save(body, "sent" if send and _send(body, version) else "kept"),
            daemon=True,
            name="crash-report-send",
        ).start()

    try:
        _fill_popup(tk.Toplevel(tk_root), body, done, reload)
    except Exception:
        pass


def _fill_popup(root: Any, body: dict[str, str], done: Callable[[bool], None],
                reload: Callable[[], None] | None = None) -> None:
    """The pop-up's contents in *root* (a Tk root or a Toplevel); *done(send)* runs once.
    A stuck page with *reload* also gets Reload: it closes the pop-up (answered, not sent)
    and reloads the page."""
    import tkinter as tk

    bg, fg, dim, accent, panel = "#121216", "#ededed", "#a1a1aa", "#2563eb", "#1c1c22"
    stuck = "got stuck" in str(body.get("message") or "")
    heading = STUCK_HEADING if stuck else "UEFN Ducky closed unexpectedly"
    root.title(heading)
    root.configure(bg=bg, padx=22, pady=18)
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    wrap = 470
    tk.Label(root, text=heading, bg=bg, fg=fg, font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x")
    tk.Label(root, text=why_summary(body), bg=bg, fg=fg, font=("Segoe UI", 10), justify="left", wraplength=wrap, anchor="w").pack(fill="x", pady=(10, 6))
    tk.Label(
        root,
        text="Send the crash report so we can fix it. " + PRIVACY_NOTE,
        bg=bg, fg=dim, font=("Segoe UI", 9), justify="left", wraplength=wrap, anchor="w",
    ).pack(fill="x")
    details = tk.Text(root, height=14, width=64, bg=panel, fg=fg, relief="flat", font=("Consolas", 9), wrap="word", padx=8, pady=6)
    details.insert("1.0", report_text(body))
    details.configure(state="disabled")
    buttons = tk.Frame(root, bg=bg)
    buttons.pack(fill="x", pady=(16, 0))

    def toggle_details() -> None:
        if details.winfo_ismapped():
            details.pack_forget()
            view.configure(text="View report")
        else:
            details.pack(fill="both", pady=(12, 0), before=buttons)
            view.configure(text="Hide report")

    def answer(send: bool) -> None:
        root.destroy()
        done(send)

    def reload_page() -> None:
        answer(False)
        try:
            reload()
        except Exception:
            pass

    style = {"font": ("Segoe UI", 10), "relief": "flat", "padx": 14, "pady": 6, "cursor": "hand2", "borderwidth": 0}
    view = tk.Button(buttons, text="View report", bg=panel, fg=fg, activebackground=panel, activeforeground=fg, command=toggle_details, **style)
    view.pack(side="left")
    tk.Button(buttons, text="Send report", bg=accent, fg="#ffffff", activebackground=accent, activeforeground="#ffffff", command=lambda: answer(True), **style).pack(side="right")
    tk.Button(buttons, text="Don't send", bg=panel, fg=fg, activebackground=panel, activeforeground=fg, command=lambda: answer(False), **style).pack(side="right", padx=(0, 8))
    if stuck and reload is not None:
        tk.Button(buttons, text="Reload", bg=panel, fg=fg, activebackground=panel, activeforeground=fg, command=reload_page, **style).pack(side="right", padx=(0, 8))
    root.protocol("WM_DELETE_WINDOW", lambda: answer(False))
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{max(0, x)}+{max(0, y)}")
    root.focus_force()


def _save(body: dict[str, str], prefix: str) -> str:
    base = report_id = f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"
    n = 0
    while (_dir() / f"{report_id}.json").exists():  # a test harness restarts within a second
        n += 1
        report_id = f"{base}-{n}"
    try:
        (_dir() / f"{report_id}.json").write_text(json.dumps(body), encoding="utf-8")
    except OSError:
        return ""
    files = sorted(p for p in _dir().glob("*-*.json") if _REPORT_ID.match(p.stem))
    for old in files[:-KEEP_REPORTS]:
        try:
            old.unlink()
        except OSError:
            pass
    return report_id


def _report_path(report_id: str) -> Path | None:
    if not _REPORT_ID.match(report_id or ""):
        return None
    path = _dir() / f"{report_id}.json"
    return path if path.is_file() else None


def list_reports() -> list[dict[str, Any]]:
    """Every saved crash report, newest first, with exactly what was (or would be) sent."""
    out: list[dict[str, Any]] = []
    for path in sorted(_dir().glob("*-*.json"), key=lambda p: p.stem.split("-", 1)[1], reverse=True):
        if not _REPORT_ID.match(path.stem):
            continue
        body = _read_json(path)
        out.append(
            {
                "id": path.stem,
                "sent": path.stem.startswith("sent-"),
                "at": path.stat().st_mtime,
                "message": str(body.get("message") or ""),
                "text": report_text(body),
            }
        )
    return out


def send_report(report_id: str, version: str) -> bool:
    """Send a kept report (the crash pop-up, Settings → Support)."""
    path = _report_path(report_id)
    if path is None or not path.stem.startswith("kept-"):
        return False
    if not _send(_read_json(path), version):
        return False
    try:
        path.replace(path.with_name(path.name.replace("kept-", "sent-", 1)))
    except OSError:
        pass
    return True


def delete_report(report_id: str) -> bool:
    path = _report_path(report_id)
    if path is None:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _ask_and_send(report_id: str, body: dict[str, str], version: str) -> None:
    if not _test_copy() and _ask_to_send(body):
        send_report(report_id, version)


def ask_pending() -> None:
    """Ducky-Launcher.exe's backend, once it said hello: the pop-up for the report
    ``begin(ask=False)`` kept, on a thread of its own (the main thread serves the launcher)."""
    report_id, body = _pending.pop("id", ""), _pending.pop("body", None)
    if not report_id or not body:
        return
    version = str(_state.get("version") or "")

    def ask() -> None:
        import gc

        try:
            _ask_and_send(report_id, body, version)
        finally:
            # Tk garbage freed on another thread ends the process (Tcl_AsyncDelete): free it here.
            gc.collect()

    threading.Thread(target=ask, daemon=True, name="crash-report-ask").start()


def begin(version: str, *, ask: bool = True) -> None:
    """First thing in a GUI launch: if the last launch crashed, ask to send its report;
    then start recording this launch.

    The report is saved (``kept-``) before the pop-up: a launch ended while it is open
    keeps it for Settings → Support. ``ask=False`` (the launcher's backend) only keeps it;
    ``ask_pending`` shows the pop-up after hello. Asked before hello, the launcher saw a
    backend silent for 20 s, ended it and the report with it, and opened the Python window."""
    global _native_file
    try:
        _dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    previous = _read_json(_boot_path())
    try:
        native = _native_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        native = ""
    alive = _still_running(int(previous.get("pid") or 0)) if previous else False
    if alive:
        # This launch will hand off: the running process owns both crash files.
        # Leave this process unarmed so its later stage/mark calls are no-ops too.
        _state.clear()
        return
    stopped = _read_json(_stopped_path())
    if previous and int(stopped.get("pid") or 0) == int(previous.get("pid") or -1):
        previous = dict(previous, state="stopped")
    try:
        _stopped_path().unlink(missing_ok=True)
    except OSError:
        pass
    restarted = _pc_restarted_since(float(previous.get("started") or 0.0)) if previous else False
    dumps = crash_dumps(float(previous.get("started") or 0.0)) if previous.get("started") else []
    ends = [float(d["at"]) for d in dumps if d.get("at")]
    if native.strip():
        try:
            ends.append(_native_path().stat().st_mtime)
        except OSError:
            pass
    body = build_report(previous, native, restarted=restarted, dumps=dumps, ended=max(ends) if ends else None) if previous else None
    prune_dumps()
    if body:
        # Clear the record first: if the pop-up itself fails, the next launch must not ask again.
        try:
            _boot_path().unlink(missing_ok=True)
            _native_path().write_text("", encoding="utf-8")
        except OSError:
            pass
        report_id = _save(body, "kept")
        if not report_id:  # the crash folder can't be written: ask and send as it is
            if ask and not _test_copy() and _ask_to_send(body):
                _send(body, version)
        elif ask:
            _ask_and_send(report_id, body, version)
        else:
            _pending.update(id=report_id, body=body)

    _state.clear()
    _state.update(
        {
            "version": version,
            "started": time.time(),
            "pid": os.getpid(),
            "os": platform.platform(),
            "python": platform.python_version(),
            "state": "starting",
            "stages": [["launch", 0.0]],
        }
    )
    launcher = (
        _recent_launcher(previous.get("launcher"), _state["started"], previous.get(LAUNCHER_CARRIED_KEY))
        if previous
        else None
    )
    if launcher:
        _state["launcher"] = launcher
        _state[LAUNCHER_CARRIED_KEY] = launcher.get("started")
    _write_state()
    try:
        import faulthandler

        if _native_file is not None:  # a second begin(): hand the handler over, close the old file
            faulthandler.disable()
            _native_file.close()
        _native_file = open(_native_path(), "w", encoding="utf-8")  # noqa: SIM115 — must stay open for the crash handler
        # Only the crashing thread: on Windows faulthandler also fires on first-chance exceptions
        # COM and .NET handle themselves (0x8001010d), and walking the other threads' frames while
        # they run crashed the app itself (dump_frame / PyCode_Addr2Line, Oct 28 2026). The app's
        # own minidump still has every native stack.
        faulthandler.enable(file=_native_file, all_threads=False)
    except Exception:
        _native_file = None


def _memory_sample(agents: Callable[[], int] | None) -> dict[str, Any]:
    """PC free memory and commit charge, the app's and its WebView2's private bytes,
    agents and threads. Anything that can't be read is left out."""
    beat: dict[str, Any] = {"at": round(time.time(), 1)}
    gb = 1024**3
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(stat)):
            beat.update(
                free_gb=round(stat.ullAvailPhys / gb, 2),
                total_gb=round(stat.ullTotalPhys / gb, 1),
                commit_gb=round((stat.ullTotalPageFile - stat.ullAvailPageFile) / gb, 1),
                commit_limit_gb=round(stat.ullTotalPageFile / gb, 1),
            )
    except (AttributeError, OSError):
        pass
    try:
        from backend.util import proc_table

        procs = proc_table.table()
        me = procs.get(os.getpid())
        if me is not None:
            beat.update(app_gb=round(me.private / gb, 2), app_mb=round(me.private / 1024**2, 1),
                        threads=me.threads, handles=me.handles)
            # With the Rust launcher, WebView2 is our sibling, not our child.
            # Check the parent's identity/start time before widening the census.
            parent = procs.get(me.ppid)
            launcher = parent if (os.environ.get("UEFN_DUCKY_NATIVE_CHILD") == "1"
                                  and parent is not None and parent.name.lower() == "ducky-launcher.exe"
                                  and parent.started <= me.started) else None
            mine = proc_table.tree(procs, launcher.pid if launcher else me.pid)
            webview = sum(procs[p].private for p in mine if procs[p].name.lower() == WEBVIEW_EXE)
            beat.update(webview_gb=round(webview / gb, 2), webview_mb=round(webview / 1024**2, 1))
            if launcher:
                beat["launcher_mb"] = round(launcher.private / 1024**2, 1)
            beat.update(process_census(procs, me.pid, mine, launcher_pid=launcher.pid if launcher else None))
    except Exception:
        # Out of commit the process table can fail: still say how big Ducky itself was.
        beat.setdefault("threads", threading.active_count())
        own = _own_private_bytes()
        if own:
            beat["app_gb"] = round(own / gb, 2)
    if agents is not None:
        try:
            beat["agents"] = int(agents())
        except Exception:
            pass
    return beat


WEBVIEW_EXE = "msedgewebview2.exe"
TOP_OTHER_N = 3
# Windows' own memory accounting, not programs anyone can close.
_NOT_PROGRAMS = {"system", "registry", "memory compression", "secure system", "idle", "system idle process", ""}


def process_census(procs: dict[int, Any], root: int, mine: list[int] | None = None,
                   *, launcher_pid: int | None = None) -> dict[str, Any]:
    """What else held memory at this beat, from the one process table the beat already read.

    ``agents_mb`` / ``agents_n``: every process Ducky started, and theirs, except WebView2
    (agent CLIs: claude, node, the bridge). ``top_other``: the TOP_OTHER_N biggest programs
    outside Ducky, summed by name, as ``[name, MB]`` (kept on this PC; only the crash report the
    person chooses to send names them)."""
    if mine is None:
        from backend.util import proc_table

        mine = proc_table.tree(procs, root)
    ours = set(mine)
    mb = 1024**2
    agents = [procs[p] for p in mine if p not in (root, launcher_pid) and procs[p].name.lower() != WEBVIEW_EXE]
    by_name: dict[str, int] = {}
    for pid, proc in procs.items():
        if pid in ours or proc.name.lower() in _NOT_PROGRAMS:
            continue
        name = re.sub(r"(?i)[.]exe$", "", proc.name)
        by_name[name] = by_name.get(name, 0) + int(proc.private)
    top = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:TOP_OTHER_N]
    return {
        "agents_mb": round(sum(int(p.private) for p in agents) / mb),
        "agents_n": len(agents),
        "top_other": [[name, round(size / mb)] for name, size in top],
    }


def size_bucket(mb: Any) -> str:
    """A size the report can carry without an exact number: '1-2 GB'."""
    try:
        n = float(mb)
    except (TypeError, ValueError):
        return "?"
    for bound, label in ((512, "under 0.5 GB"), (1024, "0.5-1 GB"), (2048, "1-2 GB"), (4096, "2-4 GB"), (8192, "4-8 GB")):
        if n < bound:
            return label
    return "over 8 GB"


def _own_private_bytes() -> int:
    """This process's private bytes from one GetProcessMemoryInfo call (no process snapshot)."""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                *[(n, ctypes.c_size_t) for n in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                    "PrivateUsage",
                )],
            ]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return int(pmc.PrivateUsage)
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass
    return 0


BEATS_KEPT = 10  # the launch's first beat plus the last ~4.5 minutes


_beat_listeners: list[Callable[[dict[str, Any]], None]] = []


def on_heartbeat(fn: Callable[[dict[str, Any]], None]) -> None:
    """Call *fn* with every heartbeat's memory sample (on the heartbeat thread)."""
    _beat_listeners.append(fn)


def heartbeat(agents: Callable[[], int] | None = None) -> None:
    """Write one memory heartbeat into boot.json (the next launch's best clue when a
    crash leaves no dump), and keep the last few so the report can show a trend."""
    if not _state:
        return
    beat = _memory_sample(agents)
    _state["heartbeat"] = beat
    keep = ("at", "free_gb", "app_gb", "webview_gb")
    beats = _state.get("beats") or []
    # The launch's first beat stays, so the trend covers the whole run, not just the last minutes.
    _state["beats"] = [*beats[:1], *beats[1:][-(BEATS_KEPT - 2):], {k: beat[k] for k in keep if k in beat}]
    _write_state()
    try:
        from frontend import memory_history

        memory_history.append(_dir() / "memory", _state, beat)
    except Exception:
        pass  # full/read-only disk must not stop samples, listeners or the app
    for fn in list(_beat_listeners):
        try:
            fn(beat)
        except Exception:
            pass


def _trend_line(beats: list[dict[str, Any]]) -> str:
    """'Memory trend over 4 min: app 0.6 -> 0.7 GB, WebView2 0.4 -> 2.5 GB, free 3.1 -> 0.2 GB.'"""
    beats = [b for b in beats if isinstance(b, dict) and b.get("at")]
    if len(beats) < 2:
        return ""
    first, last = beats[0], beats[-1]
    parts = [
        f"{label} {float(first[key]):.1f} -> {float(last[key]):.1f} GB"
        for key, label in (("app_gb", "app"), ("webview_gb", "WebView2"), ("free_gb", "free"))
        if first.get(key) is not None and last.get(key) is not None
    ]
    minutes = max(1, round((float(last["at"]) - float(first["at"])) / 60))
    return f"Memory trend over {minutes} min: " + ", ".join(parts) + "." if parts else ""


def start_heartbeat(agents: Callable[[], int] | None = None, every_s: float = HEARTBEAT_S) -> None:
    """One daemon thread writing a heartbeat every *every_s* (never more often). Later calls do nothing."""
    if not _state or _heartbeat["thread"] is not None:
        return

    def loop() -> None:
        while _state.get("state") not in _CLEAN_ENDS:
            heartbeat(agents)
            time.sleep(max(HEARTBEAT_S, every_s))

    _heartbeat["thread"] = threading.Thread(target=loop, daemon=True, name="boot-heartbeat")
    _heartbeat["thread"].start()


_stage_listeners: list[Callable[[str, int], None]] = []
_T0 = time.time()  # an unarmed process (a hand-off) still times its stages from here


def on_stage(fn: Callable[[str, int], None]) -> None:
    """Call *fn(name, ms since start)* at every later stage(): the launcher's ``stage`` line,
    so it can tell a slow backend from a dead one."""
    _stage_listeners.append(fn)


def stage(name: str) -> None:
    """Record one startup stage (cheap; never raises)."""
    started = float(_state.get("started") or _T0)
    offset = time.time() - started
    for fn in list(_stage_listeners):
        try:
            fn(str(name), int(offset * 1000))
        except Exception:
            pass
    if not _state:
        return
    _state.setdefault("stages", []).append([str(name), round(offset, 2)])
    del _state["stages"][:-60]
    _write_state()


def mark(state: str, *, error: str = "") -> None:
    """``ok`` once the window is shown, ``handoff`` when another Ducky took over,
    ``closed`` on a normal exit, ``fatal`` with the error a startup failure showed."""
    if not _state:
        return
    if _state.get("state") == "fatal" and state == "closed":
        return  # the startup error dialog exits through the normal shutdown: keep the error
    # Closing a stuck window is a normal exit, but the launch still got stuck.
    if not (_state.get("state") == "stuck" and state == "closed"):
        _state["state"] = state
    if error:
        _state["error"] = _scrub(error)[-4000:]
    _write_state()
    if state in ("handoff", "closed"):
        # Exiting on purpose: the crash file must not look like a crash next time.
        try:
            import faulthandler

            faulthandler.disable()
            if _native_file is not None:
                _native_file.close()
            _native_path().write_text("", encoding="utf-8")
        except Exception:
            pass


def page_started() -> None:
    """The window's page ran its main script: the app is past its loading splash."""
    if _page["started"]:
        return
    _page["started"] = True
    stage("page_started")


def page_error(message: str) -> None:
    """An error the page hit before it started (index.html's catcher sends these)."""
    if not _page["started"] and len(_page["errors"]) < 8:
        _page["errors"].append(_scrub(str(message or "").strip())[:300])


def managed_exception(text: str) -> None:
    """A .NET exception nobody caught (AppDomain.UnhandledException): the process ends right
    after, and faulthandler only sees a native thread. Written at once for the next launch."""
    if not _state:
        return
    _state["managed_error"] = _scrub(str(text or "").strip())[-2000:]
    _write_state()


def webview_ready(ok: bool, error: str = "") -> None:
    """The main window's WebView2 finished starting, or failed (its InitializationException).
    Only the first result counts: rebuilds and other windows report elsewhere."""
    if _page.get("webview") is None:
        _page["webview"] = {"ok": bool(ok), "error": _scrub(str(error or "").strip())[:300]}


def _webview2_runtime() -> str:
    """The installed WebView2 runtime version, or why there is none."""
    try:
        from Microsoft.Web.WebView2.Core import CoreWebView2Environment  # type: ignore[import-not-found]

        return str(CoreWebView2Environment.GetAvailableBrowserVersionString() or "none found")
    except Exception as exc:
        return f"none found ({type(exc).__name__})"


def watch_page(show: Callable[[dict[str, str]], None], files: Callable[[], list[str]], wait_s: float = STUCK_AFTER_S) -> None:
    """Once the window shows: if its page hasn't started within *wait_s*, this launch is
    stuck; *show* gets the report (the crash pop-up). *files* lists what the page loaded."""
    if not _state or _page["started"] or _page["watching"]:
        return
    _page["watching"] = True

    def check() -> None:
        body = page_stuck(files(), wait_s)
        if body:
            show(body)

    timer = threading.Timer(wait_s, check)
    timer.daemon = True
    timer.start()


def _stuck_cause(files: list[str], errors: list[str], webview: dict[str, Any] | None = None, runtime: str = "") -> str:
    """What most likely kept the page from starting: a script that wasn't sent right
    (the reason it failed) before the page's own error, before nothing at all. With nothing
    asked, *webview* (webview_ready's result, None = never came) says whether WebView2 started."""
    scripts = [f.split(" ") for f in files if f.split(" ")[0].endswith((".js", ".mjs", ".css"))]
    for name, status, *_kind in scripts:
        if status != "200":
            return f"{name} could not be loaded ({status}): files may be missing from the install"
    for name, _status, *kind in scripts:
        want = "text/css" if name.endswith(".css") else "text/javascript"
        if (kind[0] if kind else "") != want:
            return f"{name} was sent as {kind[0] if kind else 'nothing'}, which WebView2 refuses"
    if errors:
        return errors[0]
    if not scripts:
        why = "the window never asked for the app's scripts (WebView2 did not load the page)"
        if webview is None or not webview.get("ok"):
            why += ". Reinstalling the Microsoft Edge WebView2 Runtime usually fixes this"
        on = f", runtime {runtime}" if runtime else ""
        if webview is not None and not webview.get("ok"):
            return f"WebView2 could not start: {webview.get('error') or 'no reason given'}{on}; {why}"
        if webview is None and runtime:
            return f"WebView2 never finished starting{on}; {why}"
        return why
    return ""


def page_stuck(files: list[str], waited: float) -> dict[str, str] | None:
    """Mark this launch stuck on its loading splash and return the report, or None when
    the page started after all (or this process isn't recording)."""
    if not _state or _page["started"]:
        return None
    errors = list(_page["errors"])
    lines = [f"The window opened but its page did not start within {waited:.0f} s."]
    webview = _page.get("webview")
    runtime = "" if files or (webview or {}).get("ok") else _webview2_runtime()
    cause = _stuck_cause(files, errors, webview, runtime)
    if cause:
        lines.insert(0, "Likely cause: " + cause)
    if errors:
        lines += ["Page errors:", *("  " + e for e in errors)]
    lines += ["Page files:", *("  " + f for f in files)] if files else ["Page files: none"]
    mark("stuck", error="\n".join(lines))
    return build_report(dict(_state), "")
