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

# States a launch can end in without being a crash.
_QUIET_STATES = {"ok", "handoff", "closed"}
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


def _loading_plugins(process: int) -> list[str]:
    """Plugins the crashed process was loading (the crash guard's markers)."""
    folder = Path(_dir().parent) / "uefn_plugins" / ".loading"
    out: list[str] = []
    for marker in folder.glob(f"*.{process}.json") if folder.is_dir() else []:
        info = _read_json(marker)
        if info.get("plugin"):
            out.append(f"{info.get('plugin')} {info.get('version') or ''}".strip())
    return out


def build_report(previous: dict[str, Any], native: str) -> dict[str, str] | None:
    """The form body for a launch that crashed or never finished starting; None if it was fine."""
    state = str(previous.get("state") or "")
    native = (native or "").strip()
    if not previous or (state in _QUIET_STATES and not native):
        return None
    started = float(previous.get("started") or 0.0)
    stages = previous.get("stages") or []
    last = stages[-1][0] if stages else "launch"
    if native:
        when = "while starting" if state not in _QUIET_STATES else "while running"
        what = f"Ducky crashed {when} (last stage: {last})"
    elif state == "fatal":
        what = f"Ducky showed a startup error (last stage: {last})"
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
        lines += ["", "Crash traceback:", native[-5000:]]
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
        if "fatal exception" in lowered or line.startswith(("ImportError", "RuntimeError", "OSError", "MemoryError")):
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
    body = build_report(previous, native) if previous else None
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
