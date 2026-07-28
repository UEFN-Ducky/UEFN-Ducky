"""Open-with / CLI file paths — show any file in the panel editor like VS Code.

Windows Explorer "Open with", dragging onto the EXE, and ``UEFN-Ducky.exe path``
all arrive as argv paths. A second launch hands off to the running panel over
loopback HTTP instead of starting another window.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

# Flags that take a following value — skip both when scanning argv for paths.
_VALUE_FLAGS = frozenset({"--port"})

# Browser → app deep links (e.g. the DuckyOS website Store's "Install in app").
DEEP_LINK_PREFIX = "uefn-ducky://"

_pending_lock = threading.Lock()
_pending_paths: list[str] = []
_pending_links: list[str] = []
_open_handler: Callable[[list[str]], None] | None = None
_link_handler: Callable[[list[str]], None] | None = None


def cli_open_paths(argv: list[str] | None = None) -> list[str]:
    """Absolute paths of existing files passed on the command line (not flags / bridge)."""
    args = list(sys.argv if argv is None else argv)
    if len(args) <= 1:
        return []
    # ``bridge`` is the MCP stdio entry — never treat its args as editor paths.
    if args[1] == "bridge":
        return []
    out: list[str] = []
    skip_next = False
    for arg in args[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg in _VALUE_FLAGS:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        try:
            path = Path(arg).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if path.is_file():
            out.append(str(path))
    return out


def cli_deep_links(argv: list[str] | None = None) -> list[str]:
    """``uefn-ducky://…`` URLs passed on the command line (browser protocol launch)."""
    args = list(sys.argv if argv is None else argv)
    if len(args) <= 1 or args[1] == "bridge":
        return []
    return [a.strip() for a in args[1:] if a.strip().lower().startswith(DEEP_LINK_PREFIX)]


def enqueue_deep_links(links: list[str]) -> None:
    """Queue deep links until React mounts (cold start) or a handler is ready."""
    if not links:
        return
    with _pending_lock:
        seen = set(_pending_links)
        for raw in links:
            link = (raw or "").strip()
            if not link or link in seen:
                continue
            _pending_links.append(link)
            seen.add(link)


def take_pending_deep_links() -> list[str]:
    with _pending_lock:
        out = list(_pending_links)
        _pending_links.clear()
        return out


def set_deep_link_handler(handler) -> None:
    """Panel process registers a callback: show window + fire ``ducky:deep-link``."""
    global _link_handler
    _link_handler = handler


def dispatch_deep_links(links: list[str]) -> bool:
    """Accept deep links in this panel process (live handler, else cold-start queue)."""
    cleaned = [(link or "").strip() for link in links if (link or "").strip()]
    if not cleaned:
        return False
    handler = _link_handler
    if handler is not None:
        try:
            handler(cleaned)
            return True
        except Exception:
            pass
    enqueue_deep_links(cleaned)
    return True


def enqueue_open_paths(paths: list[str]) -> None:
    """Queue absolute paths until React mounts (cold start) or a handler is ready."""
    if not paths:
        return
    with _pending_lock:
        seen = set(_pending_paths)
        for raw in paths:
            p = (raw or "").strip()
            if not p or p in seen:
                continue
            _pending_paths.append(p)
            seen.add(p)


def take_pending_open_paths() -> list[str]:
    with _pending_lock:
        out = list(_pending_paths)
        _pending_paths.clear()
        return out


def set_open_files_handler(handler) -> None:
    """Panel process registers a callback: show window + fire ``ducky:external-files-open``."""
    global _open_handler
    _open_handler = handler


def dispatch_open_files(paths: list[str]) -> bool:
    """Accept paths in this panel process.

    Prefer the live UI handler (second-instance handoff). If the UI is not ready
    yet, queue file paths for ``take_pending_open_paths``. Focus-only (no paths)
    requires a handler — returns False so the caller can retry.
    """
    cleaned = [(p or "").strip() for p in paths if (p or "").strip()]
    handler = _open_handler
    if handler is not None:
        try:
            handler(cleaned)
            return True
        except Exception:
            pass
    if cleaned:
        enqueue_open_paths(cleaned)
        return True
    return False


def try_handoff_to_running(
    paths: list[str],
    *,
    links: list[str] | None = None,
    retries: int = 4,
    delay_s: float = 0.15,
    timeout_s: float = 0.35,
) -> bool:
    """POST paths/deep-links to a living panel's HTTP server. Empty payload still focuses.

    Keep retries/timeout short: a stale panel.pid after a force-kill/update otherwise
    burns ~20s of connect timeouts before cold start can proceed.
    """
    if sys.platform != "win32":
        return False
    from frontend.ui_web.panel_httpd import panel_ui_http_url

    url = f"{panel_ui_http_url().rstrip('/')}/__panel_open_files"
    body = json.dumps({"paths": list(paths), "links": list(links or [])}).encode("utf-8")

    for attempt in range(max(1, retries)):
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if 200 <= getattr(resp, "status", 200) < 300:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        if attempt + 1 < retries:
            time.sleep(delay_s)
    return False


def register_windows_open_with(exe: Path | str | None = None) -> None:
    """HKCU Open-with registration so UEFN Ducky appears for *any* file type.

    Mirrors VS Code's ``Applications\\Code.exe`` pattern: shell\\open\\command plus
    SupportedTypes ``.*`` and ``*\\OpenWithList``. Safe to call on every launch.
    """
    if sys.platform != "win32":
        return
    from frontend.frozen_process import is_uefn_ducky_executable

    try:
        target = Path(exe or sys.executable).resolve()
    except (OSError, RuntimeError):
        return
    if not target.is_file() or not is_uefn_ducky_executable(target):
        return

    import winreg

    exe_name = target.name
    command = f'"{target}" "%1"'
    app_key = rf"Software\Classes\Applications\{exe_name}"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, app_key) as key:
            winreg.SetValueEx(key, "FriendlyAppName", 0, winreg.REG_SZ, "UEFN Ducky")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"{app_key}\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(target))
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"{app_key}\SupportedTypes") as key:
            # ``.*`` = offer for every extension (same trick Code uses).
            winreg.SetValueEx(key, ".*", 0, winreg.REG_SZ, "")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"{app_key}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        # Populate the classic Open-with list for the "*" file class.
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\*\OpenWithList\{exe_name}"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "")
        # Context-menu "Open with UEFN Ducky" is installed by the Setup script
        # (release/installer/UEFN-Ducky.iss) — not rewritten here, so a portable
        # pytest/dev register can't point that verb at a throwaway path.
    except OSError:
        pass


def register_windows_url_protocol(exe: Path | str | None = None) -> None:
    """HKCU ``uefn-ducky://`` protocol so the website Store can open/install in the app.

    Safe to call on every launch (mirrors :func:`register_windows_open_with`).
    """
    if sys.platform != "win32":
        return
    from frontend.frozen_process import is_uefn_ducky_executable

    try:
        target = Path(exe or sys.executable).resolve()
    except (OSError, RuntimeError):
        return
    if not target.is_file() or not is_uefn_ducky_executable(target):
        return

    import winreg

    command = f'"{target}" "%1"'
    proto_key = r"Software\Classes\uefn-ducky"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, proto_key) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:UEFN Ducky")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"{proto_key}\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(target))
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"{proto_key}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    except OSError:
        pass


def panel_process_alive(pid: int) -> bool:
    """True when ``pid`` still refers to a running process (Windows)."""
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os_kill = getattr(__import__("os"), "kill")
            os_kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def read_living_panel_pid() -> int | None:
    """Return panel.pid when that process is still alive; else None."""
    from frontend.frozen_process import panel_pid_path

    path = panel_pid_path()
    if not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if panel_process_alive(pid):
        return pid
    return None
