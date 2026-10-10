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

Standard library only: this runs before the rest of Ducky has loaded.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
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
_QUIET_STATES = {"ok", "handoff", "closed"}
# States only a deliberate exit writes. A launch left at "ok" never exited on purpose.
_CLEAN_ENDS = {"handoff", "closed"}
NO_TRACE = (
    "No crash trace: Ducky ended outside Python code (WebView2, a DLL, running out of "
    "memory) or was ended from Task Manager."
)
# sent = the user sent it; kept = not sent (they chose not to, or sending failed).
_REPORT_ID = re.compile(r"^(?:sent|kept)-\d{8}-\d{6}$")

_state: dict[str, Any] = {}
_native_file: Any = None


def _dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "UEFN-Ducky" / "crash"


def _boot_path() -> Path:
    return _dir() / "boot.json"


def _native_path() -> Path:
    return _dir() / "native.log"


def _scrub(text: str) -> str:
    try:
        from backend.util.privacy import scrub

        return scrub(text)
    except Exception:
        home = str(Path.home())
        return text.replace(home, "~").replace(home.replace("\\", "/"), "~") if home else text


def _write_state() -> None:
    try:
        path = _boot_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_state), encoding="utf-8")
        tmp.replace(path)
    except OSError:
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


def build_report(previous: dict[str, Any], native: str, *, restarted: bool = False) -> dict[str, str] | None:
    """The form body for a launch that crashed or never finished starting; None if it was fine.

    A launch still at ``ok`` never exited on purpose (every exit marks ``closed``): it
    ended in a crash Python never saw, unless the PC restarted (*restarted*)."""
    state = str(previous.get("state") or "")
    native = (native or "").strip()
    if not previous or (state in _CLEAN_ENDS and not native):
        return None
    if state == "ok" and not native and restarted:
        return None
    started = float(previous.get("started") or 0.0)
    stages = previous.get("stages") or []
    last = stages[-1][0] if stages else "launch"
    if native:
        when = "while starting" if state not in _QUIET_STATES else "while running"
        what = f"Ducky crashed {when} (last stage: {last})"
    elif state == "fatal":
        what = f"Ducky showed a startup error (last stage: {last})"
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
    if native:
        lines += ["", "Crash traceback:", trim_native(native)]
    elif state == "ok":
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
    for line in log:
        if line.startswith("Loading when it closed: "):
            out.append("It was loading the plugin " + line.split(": ", 1)[1] + ".")
            break
    for line in log:
        lowered = line.lower()
        if line == NO_TRACE or "fatal exception" in lowered or line.startswith(("ImportError", "RuntimeError", "OSError", "MemoryError")):
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
    except Exception:
        return False
    bg, fg, dim, accent, panel = "#121216", "#ededed", "#a1a1aa", "#2563eb", "#1c1c22"
    choice = {"send": False}
    try:
        root = tk.Tk()
    except Exception:
        return False
    root.title("UEFN Ducky closed unexpectedly")
    root.configure(bg=bg, padx=22, pady=18)
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    wrap = 470
    tk.Label(root, text="UEFN Ducky closed unexpectedly", bg=bg, fg=fg, font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x")
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

    def send() -> None:
        choice["send"] = True
        root.destroy()

    style = {"font": ("Segoe UI", 10), "relief": "flat", "padx": 14, "pady": 6, "cursor": "hand2", "borderwidth": 0}
    view = tk.Button(buttons, text="View report", bg=panel, fg=fg, activebackground=panel, activeforeground=fg, command=toggle_details, **style)
    view.pack(side="left")
    tk.Button(buttons, text="Send report", bg=accent, fg="#ffffff", activebackground=accent, activeforeground="#ffffff", command=send, **style).pack(side="right")
    tk.Button(buttons, text="Don't send", bg=panel, fg=fg, activebackground=panel, activeforeground=fg, command=root.destroy, **style).pack(side="right", padx=(0, 8))
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{max(0, x)}+{max(0, y)}")
    root.focus_force()
    try:
        root.mainloop()
    except Exception:
        pass
    return choice["send"]


def _save(body: dict[str, str], prefix: str) -> str:
    report_id = f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"
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
    restarted = _pc_restarted_since(float(previous.get("started") or 0.0)) if previous else False
    body = build_report(previous, native, restarted=restarted) if previous else None
    if body:
        # Clear the record first: if the pop-up itself fails, the next launch must not ask again.
        try:
            _boot_path().unlink(missing_ok=True)
            _native_path().write_text("", encoding="utf-8")
        except OSError:
            pass
        sent = _ask_to_send(body) and _send(body, version)
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
