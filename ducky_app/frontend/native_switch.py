"""Native launcher switch: start Ducky-Launcher.exe, or fall back to the Python window.

The ``native_launcher`` setting (off | on | auto, default off) picks who draws the window.
``off`` never spawns anything: the launch runs today's Python window. ``on`` and ``auto``
start ``Ducky-Launcher.exe`` (it starts ``UEFN-Ducky.exe --backend`` itself) and wait for
``launcher/ready-<launcher pid>.json``; any exit code, a timeout or a spawn error lets the
Python window start in this same process with the same argv.

Launcher exit codes: 0 ok · 20 WebView2 missing or old · 21 protocol mismatch · 22 crash
loop · 23 no hello within 15 s · 24 other start error.

``launcher/crash_loop.json`` ``{failures: [ts...], until: ts}``: 3 failures in 10 min make
``auto`` use Python for 24 h (``on`` still tries the launcher).

Every on/auto window launch records one start (``record_start``) for the rollout numbers:
``launcher/starts.json`` keeps the last starts for :func:`start_summary` (Settings), and
the same start goes to the usage-stats event API.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

LAUNCHER_EXE_NAME = "Ducky-Launcher.exe"
# Points the switch at a launcher build that is not next to this EXE (sandbox builds, tests).
LAUNCHER_EXE_ENV = "UEFN_DUCKY_NATIVE_LAUNCHER_EXE"
# Set for the launcher's children: a backend the launcher started never starts a launcher.
NATIVE_CHILD_ENV = "UEFN_DUCKY_NATIVE_CHILD"
# A hint for the launcher: the EXE to run with --backend (it also looks next to itself).
BACKEND_EXE_ENV = "UEFN_DUCKY_BACKEND_EXE"

EXIT_WEBVIEW2 = 20
EXIT_PROTOCOL = 21
EXIT_CRASH_LOOP = 22
EXIT_NO_HELLO = 23
EXIT_START_ERROR = 24
FALLBACK_EXITS = frozenset({EXIT_WEBVIEW2, EXIT_PROTOCOL, EXIT_CRASH_LOOP, EXIT_NO_HELLO, EXIT_START_ERROR})

EXIT_REASONS = {
    EXIT_WEBVIEW2: "webview2",
    EXIT_PROTOCOL: "protocol",
    EXIT_CRASH_LOOP: "crash_loop",
    EXIT_NO_HELLO: "no_hello",
    EXIT_START_ERROR: "start_error",
}
# Every fallback_reason record_start keeps; anything else is "other".
FALLBACK_REASONS = (
    "webview2", "protocol", "crash_loop", "no_hello", "start_error",
    "timeout", "spawn_error", "no_exe", "crash_loop_active", "exit_other", "other",
)
# A start that says nothing about the launcher's health (no WebView2, no EXE, the 24 h
# hold itself) is not a failure toward the crash loop.
_NOT_A_FAILURE = frozenset({"webview2", "no_exe", "crash_loop_active"})

CRASH_LOOP_FAILURES = 3
CRASH_LOOP_WINDOW_S = 10 * 60
CRASH_LOOP_HOLD_S = 24 * 3600
STARTS_KEPT = 200
_POLL_S = 0.02

_lock = threading.Lock()
# The ready file of the last run_native that returned None: {painted_ms, window_ms}.
last_ready: dict[str, Any] = {}


# --- paths ------------------------------------------------------------------------------


def launcher_dir() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir() / "launcher"


def ready_path(launcher_pid: int) -> Path:
    return launcher_dir() / f"ready-{int(launcher_pid)}.json"


def crash_loop_path() -> Path:
    return launcher_dir() / "crash_loop.json"


def starts_path() -> Path:
    return launcher_dir() / "starts.json"


def launcher_exe() -> Path:
    """Where Ducky-Launcher.exe would be: the env override, else next to this EXE."""
    override = os.environ.get(LAUNCHER_EXE_ENV, "").strip()
    if override:
        return Path(override)
    return Path(sys.executable).resolve().parent / LAUNCHER_EXE_NAME


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


# --- WebView2 ---------------------------------------------------------------------------

_WEBVIEW2_CLIENT = r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
_WEBVIEW2_KEYS = (
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ("HKLM", "SOFTWARE\\" + _WEBVIEW2_CLIENT.split("\\", 1)[1]),
    ("HKCU", _WEBVIEW2_CLIENT),
)


def webview2_ok() -> bool:
    """True when the Evergreen WebView2 runtime is registered (the launcher checks the version)."""
    try:
        import winreg
    except ImportError:
        return False
    for hive, sub in _WEBVIEW2_KEYS:
        root = winreg.HKEY_LOCAL_MACHINE if hive == "HKLM" else winreg.HKEY_CURRENT_USER
        try:
            with winreg.OpenKey(root, sub) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if str(version or "").strip() not in ("", "0.0.0.0"):
            return True
    return False


# --- crash loop -------------------------------------------------------------------------


def _crash_loop_state() -> dict[str, Any]:
    data = _read_json(crash_loop_path())
    return data if isinstance(data, dict) else {}


def _numbers(values: Any) -> list[float]:
    out: list[float] = []
    for v in values if isinstance(values, list) else []:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def crash_loop_active(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    try:
        until = float(_crash_loop_state().get("until") or 0)
    except (TypeError, ValueError):
        return False
    return until > now


def note_failure(now: float | None = None) -> bool:
    """One launcher failure. True when it starts the 24 h hold (3 within 10 min)."""
    now = time.time() if now is None else now
    with _lock:
        state = _crash_loop_state()
        failures = [t for t in _numbers(state.get("failures")) if now - t <= CRASH_LOOP_WINDOW_S]
        failures.append(now)
        state["failures"] = failures
        tripped = len(failures) >= CRASH_LOOP_FAILURES
        if tripped:
            state["until"] = now + CRASH_LOOP_HOLD_S
            state["failures"] = []
        try:
            _write_json(crash_loop_path(), state)
        except OSError:
            return False
        return tripped


# --- the decision -----------------------------------------------------------------------


def decide(setting: str, exe_present: bool, webview2_ok: bool, crash_loop_active: bool) -> str:
    """'native' or 'python' for this launch."""
    from frontend.settings import normalize_native_launcher

    mode = normalize_native_launcher(setting)
    if mode == "off" or not exe_present or not webview2_ok:
        return "python"
    if mode == "auto" and crash_loop_active:
        return "python"
    return "native"


def python_reason(setting: str, exe_present: bool, webview2_ok: bool, crash_loop_active: bool) -> str:
    """Why a launch that is not off ran in Python ("" when it would be native or is off)."""
    from frontend.settings import normalize_native_launcher

    mode = normalize_native_launcher(setting)
    if mode == "off":
        return ""
    if not exe_present:
        return "no_exe"
    if not webview2_ok:
        return "webview2"
    if mode == "auto" and crash_loop_active:
        return "crash_loop_active"
    return ""


# --- running the launcher ---------------------------------------------------------------


def _spawn(cmd: list[str], env: dict[str, str]) -> Any:
    flags = 0
    if os.name == "nt":
        # The launcher outlives this process: it owns the window from here on.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    return subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )


def run_native(
    argv: list[str],
    timeout_s: float = 15,
    *,
    spawn: Callable[[list[str], dict[str, str]], Any] | None = None,
    exe: Path | None = None,
) -> int | None:
    """Start the launcher with this launch's arguments and wait until its page has painted.

    None: ready (the launcher owns the window; this process should exit). An int is the
    launcher's exit code, or 23 for a timeout and 24 for a spawn error: fall back.
    """
    last_ready.clear()
    exe = exe or launcher_exe()
    env = dict(os.environ)
    env[NATIVE_CHILD_ENV] = "1"
    if getattr(sys, "frozen", False):
        env[BACKEND_EXE_ENV] = sys.executable
    cmd = [str(exe), *[str(a) for a in argv[1:]]]
    try:
        proc = (spawn or _spawn)(cmd, env)
    except (OSError, ValueError, subprocess.SubprocessError):
        return EXIT_START_ERROR
    ready = ready_path(proc.pid)
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        if ready.is_file():
            data = _read_json(ready)
            if isinstance(data, dict):
                last_ready.update(data)
                try:
                    ready.unlink()
                except OSError:
                    pass
                return None
        code = proc.poll()
        if code is not None:
            # 0 before painting: the launcher handed this launch to a running Ducky.
            return None if code == 0 else int(code)
        if time.monotonic() >= deadline:
            try:
                proc.kill()
            except OSError:
                pass
            return EXIT_NO_HELLO
        time.sleep(_POLL_S)


def fallback_reason(code: int, *, timed_out: bool = False) -> str:
    if timed_out:
        return "timeout"
    return EXIT_REASONS.get(int(code), "exit_other")


def _setting() -> str:
    try:
        from frontend.settings import PanelSettings

        return PanelSettings.load().native_launcher
    except Exception:
        return "off"


def maybe_start_native(argv: list[str], *, start_hidden: bool = False, setting: str | None = None) -> bool:
    """The launch hook (frontend/launcher.py). True: the launcher painted the window, exit now.
    False: run the Python window in this process, with the same argv.

    Off reads the setting and nothing else: no file checks, no subprocess.
    """
    from frontend.settings import normalize_native_launcher

    started = time.perf_counter()
    mode = normalize_native_launcher(_setting() if setting is None else setting)
    if mode == "off" or start_hidden or os.environ.get(NATIVE_CHILD_ENV) or "--backend" in argv[1:]:
        return False
    exe = launcher_exe()
    present = exe.is_file()
    wv2 = webview2_ok() if present else False
    held = crash_loop_active()
    if decide(mode, present, wv2, held) == "python":
        record_start_soon("python", None, None, python_reason(mode, present, wv2, held))
        return False
    timeout_s = 15.0
    code = run_native(argv, timeout_s, exe=exe)
    if code is None:
        ms = round((time.perf_counter() - started) * 1000)
        # Synchronous: this process exits right after, which would end a daemon thread.
        record_start("native", last_ready.get("window_ms"), last_ready.get("painted_ms", ms), "")
        return True
    timed_out = code == EXIT_NO_HELLO and time.perf_counter() - started >= timeout_s
    reason = fallback_reason(code, timed_out=timed_out)
    if reason not in _NOT_A_FAILURE:
        note_failure()
    record_start_soon("python", None, None, reason)
    return False


# --- rollout numbers --------------------------------------------------------------------


def _ms(value: Any) -> int | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(n)) if n == n and n >= 0 else None


def _reason(value: Any) -> str:
    r = str(value or "").strip().lower()
    return r if not r or r in FALLBACK_REASONS else "other"


def record_start(mode: str, window_ms: Any, painted_ms: Any, fallback_reason: str) -> dict[str, Any]:
    """One launch: which window drew it, how fast, and why it fell back ("" when it did not)."""
    row = {
        "ts": time.time(),
        "mode": "native" if mode == "native" else "python",
        "window_ms": _ms(window_ms),
        "painted_ms": _ms(painted_ms),
        "fallback": _reason(fallback_reason),
    }
    with _lock:
        try:
            data = _read_json(starts_path())
            rows = data.get("starts") if isinstance(data, dict) else None
            rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
            rows.append(row)
            _write_json(starts_path(), {"starts": rows[-STARTS_KEPT:]})
        except OSError:
            pass
    try:
        from frontend import usage_stats

        props: dict[str, Any] = {"mode": row["mode"], "fallback": row["fallback"] or "none"}
        if row["painted_ms"] is not None:
            props["painted_ms"] = row["painted_ms"]
        if row["window_ms"] is not None:
            props["window_ms"] = row["window_ms"]
        usage_stats.track("launcher.start", **props)
        if row["fallback"]:
            # error.code is already on the Store's list, so fallbacks count from day one.
            usage_stats.track("error.code", code="native_launcher_fallback", where="ui",
                              reason=row["fallback"], version=usage_stats._app_version())
    except Exception:
        pass
    return row


def record_start_soon(mode: str, window_ms: Any, painted_ms: Any, fallback_reason: str) -> None:
    """record_start off the launch path (the stats queue opens the database)."""
    threading.Thread(
        target=record_start,
        args=(mode, window_ms, painted_ms, fallback_reason),
        name="native-switch-record",
        daemon=True,
    ).start()


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) // 2


def start_summary() -> dict[str, Any]:
    """The rollout numbers for Settings: starts per window, fallbacks per reason, paint times."""
    data = _read_json(starts_path())
    rows = data.get("starts") if isinstance(data, dict) else None
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    native = [r for r in rows if r.get("mode") == "native"]
    fallbacks: dict[str, int] = {}
    for r in rows:
        reason = _reason(r.get("fallback"))
        if reason:
            fallbacks[reason] = fallbacks.get(reason, 0) + 1
    tried = len(native) + sum(n for r, n in fallbacks.items() if r not in _NOT_A_FAILURE)
    painted = [p for p in (_ms(r.get("painted_ms")) for r in native) if p is not None]
    return {
        "starts": len(rows),
        "native": len(native),
        "python": len(rows) - len(native),
        "fallbacks": fallbacks,
        "fallback_rate": round(1 - len(native) / tried, 3) if tried else 0.0,
        "painted_ms_median": _median(painted),
        "painted_ms_max": max(painted) if painted else None,
        "crash_loop_active": crash_loop_active(),
    }
