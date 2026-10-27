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


def trim_native(native: str, limit: int = NATIVE_MAX) -> str:
    """The last crash dump in the native log, cut to about *limit* without losing what matters.

    A dump is the fault line, then one stack per thread (newest thread first, the main
    thread last). Cutting from the front dropped the fault line and the thread that
    crashed, so the report could not say what crashed. Kept first: the fault line, the
    crashing thread, the main thread; then other threads while they fit.
    """
    text = (native or "").strip()
    starts = [m.start() for m in _FAULT_HEAD.finditer(text)]
    earlier = max(0, len(starts) - 1)
    if starts:
        text = text[starts[-1]:]
    note = f"({earlier} earlier exception{'s' if earlier != 1 else ''} in this launch not shown)" if earlier else ""
    if starts and "\nCurrent thread " not in text:
        # faulthandler marks the crashing thread "Current thread" when it runs Python.
        note = (note + "\n" if note else "") + "(The crash was in a thread that runs no Python code: native code such as WebView2, .NET or a DLL.)"
    if len(text) + len(note) <= limit:
        return "\n\n".join(p for p in (note, text) if p)
    blocks = [b.strip("\n") for b in text.split("\n\n") if b.strip()]

    def cap(block: str, size: int) -> str:
        return block if len(block) <= size else block[:size].rsplit("\n", 1)[0] + "\n  …"

    crashed = {i for i, b in enumerate(blocks) if b.startswith("Current thread")}
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
    if beat.get("threads") is not None:
        parts.append(f"{int(beat['threads'])} threads")
    lines = [f"Last heartbeat {age}: " + ", ".join(parts) + "."]
    if free is not None and float(free) < 1.0:
        when = f"{secs} before" if secs else f"less than {HEARTBEAT_S:.0f} s before"
        lines.append(f"Why: The PC had {float(free):.1f} GB free {when} Ducky closed.")
    return lines


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
    if state == "stuck" and previous.get("reported") and not native:
        return None  # the pop-up already asked while that launch was open
    started = float(previous.get("started") or 0.0)
    stages = previous.get("stages") or []
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
    plugins = _loading_plugins(int(previous.get("pid") or 0))
    if plugins:
        lines.append("Loading when it closed: " + ", ".join(plugins))
    if previous.get("error"):
        lines += ["", "Error:", str(previous["error"])]
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


def show_now(tk_root: Any, body: dict[str, str]) -> None:
    """The crash pop-up while this launch is still open (it got stuck). Runs on the Tk
    pump thread. Answered = the next launch won't ask again; unanswered = it will."""
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
        _fill_popup(tk.Toplevel(tk_root), body, done)
    except Exception:
        pass


def _fill_popup(root: Any, body: dict[str, str], done: Callable[[bool], None]) -> None:
    """The pop-up's contents in *root* (a Tk root or a Toplevel); *done(send)* runs once."""
    import tkinter as tk

    bg, fg, dim, accent, panel = "#121216", "#ededed", "#a1a1aa", "#2563eb", "#1c1c22"
    heading = STUCK_HEADING if "got stuck" in str(body.get("message") or "") else "UEFN Ducky closed unexpectedly"
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

    style = {"font": ("Segoe UI", 10), "relief": "flat", "padx": 14, "pady": 6, "cursor": "hand2", "borderwidth": 0}
    view = tk.Button(buttons, text="View report", bg=panel, fg=fg, activebackground=panel, activeforeground=fg, command=toggle_details, **style)
    view.pack(side="left")
    tk.Button(buttons, text="Send report", bg=accent, fg="#ffffff", activebackground=accent, activeforeground="#ffffff", command=lambda: answer(True), **style).pack(side="right")
    tk.Button(buttons, text="Don't send", bg=panel, fg=fg, activebackground=panel, activeforeground=fg, command=lambda: answer(False), **style).pack(side="right", padx=(0, 8))
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
    """Send a kept report (Settings → Support)."""
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


def begin(version: str) -> None:
    """First thing in a GUI launch: if the last launch crashed, ask to send its report;
    then start recording this launch."""
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
        sent = not _test_copy() and _ask_to_send(body) and _send(body, version)
        _save(body, "sent" if sent else "kept")

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
    _write_state()
    try:
        import faulthandler

        _native_file = open(_native_path(), "w", encoding="utf-8")  # noqa: SIM115 — must stay open for the crash handler
        faulthandler.enable(file=_native_file, all_threads=True)
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
            beat.update(app_gb=round(me.private / gb, 2), threads=me.threads)
            beat["webview_gb"] = round(
                sum(procs[p].private for p in proc_table.tree(procs, me.pid) if procs[p].name.lower() == "msedgewebview2.exe") / gb, 2
            )
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


def stage(name: str) -> None:
    """Record one startup stage (cheap; never raises)."""
    if not _state:
        return
    started = float(_state.get("started") or time.time())
    _state.setdefault("stages", []).append([str(name), round(time.time() - started, 2)])
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
