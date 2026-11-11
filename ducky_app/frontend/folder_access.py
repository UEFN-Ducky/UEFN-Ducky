"""Windows Controlled folder access (Defender's ransomware protection) lets only trusted apps
write into protected folders such as Documents, where Fortnite Projects live. Turned on, it
blocked UEFN Ducky, and the AI agents it runs, from saving project files until the person
found Windows Security → Ransomware protection → Allow an app through Controlled folder
access (Discord reports, Nov 2026).

Five seconds after a start Ducky tries one write in Documents and reads Defender's own log of
blocked programs (event 1123, readable without admin rights). After an agent turn it reads
that log again. Anything blocked that Ducky runs (its own EXEs, the agents' programs, UEFN) is
asked about in one box, named; Yes adds them to the allowed apps through Windows' admin prompt
(Ducky's EXEs for this install and every downloaded version, by wildcard). When Ducky itself
was blocked it then ends its shared tool servers (UEFN-Ducky-Bridge.exe --shared-daemon outlives
the app; a user's island Content\\Python was refused to one, Nov 2026) and restarts: Windows lets
an allowed app write only from its next start, and every start redeploys the listener into each
island's Content\\Python, so what was refused lands in UEFN with nothing redone by hand. A refused
write names Controlled folder access (refusal_hint). Only Windows' admin prompt can add an app,
so its one Yes is the least it takes.
Shells and script hosts (PowerShell, cmd, bash, python) are never offered: allowed, they
would let anything through. A No is kept for the app version.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from frontend import __version__

_log = logging.getLogger(__name__)

CHECK_AFTER_S = 5.0  # after the window and the crash pop-up, never in the start's way
TURN_CHECK_EVERY_S = 60.0
BLOCK = 1  # EnableControlledFolderAccess: 1 blocks writes; 0 off, 2 audit, 3/4 disk only
EXES = ("UEFN-Ducky.exe", "UEFN-Ducky-Bridge.exe")
DUCKY = "ducky"
# What Ducky's agents and UEFN write project files with: Claude Code, Codex, npm CLIs (Gemini,
# Claude from npm) run on node, Cursor's agent, git, and UEFN itself.
AGENT_PROGRAMS = frozenset({"claude.exe", "codex.exe", "node.exe", "cursor-agent.exe", "gemini.exe",
                            "git.exe", "unrealeditorfortnite-win64-shipping.exe"})
LOG = "Microsoft-Windows-Windows Defender/Operational"
BLOCKED_EVENT = 1123
LOOK_BACK_DAYS = 7
STATE_FILE = "folder_access.json"
TITLE = "UEFN Ducky: Windows is blocking your project files"
BY_HAND = ("Open Windows Security → Virus & threat protection → Ransomware protection → Allow an "
           "app through Controlled folder access, and add UEFN-Ducky.exe (and the agent's program).")
NOT_ALLOWED_TEXT = "Not allowed, so it can't save into Documents yet.\n\nTo allow it later: " + BY_HAND
STILL_BLOCKED_TEXT = ("Windows still blocks UEFN Ducky from saving into Documents. This PC's settings may be "
                      "managed by your organization: ask whoever manages it, or " + BY_HAND[0].lower() + BY_HAND[1:])
RESTART_TEXT = ("UEFN Ducky is allowed now. Close it (tray icon → Exit) and open it again: Windows lets it "
                "save into Documents from its next start.")
REFUSED_HINT = ("Windows Controlled folder access (ransomware protection) refused this write. UEFN Ducky asks "
                "to be allowed when this turn ends or at its next start, and then writes it again; or "
                + BY_HAND[0].lower() + BY_HAND[1:] + " Add UEFN-Ducky-Bridge.exe too.")
PROBE_FOLDERS_MAX = 8
REFUSAL_WINDOW_S = 120
_TOOL_SERVER_STATE = re.compile(r"^shared_mcp(-h\d+)?\.json$", re.IGNORECASE)

_busy = threading.Lock()  # one check at a time: a start's and a turn's never ask together
_last_turn_check = [0.0]


def _state_path() -> Path:
    from frontend.app_versions import versions_dir

    return versions_dir().parent / STATE_FILE


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(state: dict[str, Any]) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def write_blocked(folder: Path) -> bool:
    """True when creating a file in ``folder`` is refused (Access is denied)."""
    probe = folder / f".uefn-ducky-write-check-{os.getpid()}.tmp"
    try:
        fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except PermissionError:
        return True
    except OSError:
        return False  # missing folder, a leftover probe: not a block
    os.close(fd)
    try:
        probe.unlink()
    except OSError:
        pass
    return False


def protection_mode() -> int:
    """Controlled folder access's mode (0 off). Read the way Windows Security shows it."""
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-MpPreference).EnableControlledFolderAccess"],
            capture_output=True, text=True, timeout=30, creationflags=0x08000000,  # no window
        )
        return int((out.stdout or "").strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _dos_path(path: str) -> str:
    r"""``\Device\HarddiskVolume3\x`` as ``C:\x`` (Defender writes either form)."""
    if not path.lower().startswith("\\device\\") or os.name != "nt":
        return path
    import ctypes
    import string

    buf = ctypes.create_unicode_buffer(1024)
    for letter in string.ascii_uppercase:
        if ctypes.windll.kernel32.QueryDosDeviceW(f"{letter}:", buf, len(buf)):
            device = buf.value
            if path.lower().startswith(device.lower() + "\\"):
                return f"{letter}:" + path[len(device):]
    return path


def parse_blocked(xml: str) -> list[tuple[str, str, str]]:
    """``(time, program, refused file)`` per Controlled folder access block in ``wevtutil /f:xml`` text."""
    found = []
    for event in xml.split("</Event>"):
        when = re.search(r"<TimeCreated SystemTime='([^']+)'", event)
        name = re.search(r"<Data Name='Process Name'>([^<]+)</Data>", event)
        target = re.search(r"<Data Name='Path'>([^<]*)</Data>", event)
        if when and name:
            found.append((when.group(1), _dos_path(html.unescape(name.group(1)).strip()),
                          _dos_path(html.unescape(target.group(1)).strip()) if target else ""))
    return found


def _blocks(window_ms: int) -> list[tuple[str, str, str]]:
    query = f"*[System[(EventID={BLOCKED_EVENT}) and TimeCreated[timediff(@SystemTime) <= {window_ms}]]]"
    try:
        out = subprocess.run(["wevtutil", "qe", LOG, f"/q:{query}", "/c:200", "/rd:true", "/f:xml"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=20, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_blocked(out.stdout or "")


def blocked_programs(days: int = LOOK_BACK_DAYS) -> list[tuple[str, str]]:
    """Ducky's EXEs and agents' programs Windows blocked lately, ``(time, path)``, newest first."""
    watched = {exe.lower() for exe in EXES} | AGENT_PROGRAMS
    return [(when, path) for when, path, _ in _blocks(days * 86_400_000) if Path(path).name.lower() in watched]


def refusal_hint(path: str | Path, *, blocks: Callable[[int], list[tuple[str, str, str]]] = _blocks) -> str:
    """What to say about a refused write into ``path`` (a file or its folder): REFUSED_HINT when
    Windows' Controlled folder access refused a write in that folder within the last two minutes,
    else "" (folder permissions: not ours to explain). In the app it also starts the check."""
    if os.name != "nt":
        return ""
    target = Path(path)
    folders = {os.path.normcase(str(target)), os.path.normcase(str(target.parent))}
    if not any(os.path.normcase(str(Path(refused).parent)) in folders
               for _, _, refused in blocks(REFUSAL_WINDOW_S * 1000) if refused):
        return ""
    try:
        after_turn()
    except Exception:  # noqa: BLE001 - the hint stands on its own
        pass
    return REFUSED_HINT


def probe_folders() -> list[Path]:
    """Where a refused write would hurt: Documents (OneDrive's too) and each known project's folder."""
    from frontend.ui_web.known_projects import documents_dir, known_project_roots

    folders = [documents_dir()]
    try:
        folders += [Path(root).parent for root in known_project_roots()]
    except Exception:  # noqa: BLE001 - Documents alone still answers
        pass
    return list(dict.fromkeys(folders))[:PROBE_FOLDERS_MAX]


def _terminate(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def end_tool_servers(terminate: Callable[[int], bool] = _terminate) -> list[int]:
    """End the shared tool servers (``--shared-daemon``, state in ``shared_mcp*.json``): they outlive
    the app's restart and keep Windows' old answer. The next tool call starts an allowed one; IDE
    adapters reconnect by themselves. One the app hosts itself goes with the app's restart."""
    from backend.util import proc_table
    from frontend.settings import default_app_data_dir

    procs = proc_table.table()
    ours = {exe.lower() for exe in EXES}
    # The app's own agent hosts serve tools in-process (shared_mcp-h<slot>.json names them): they
    # end with the app's restart, which carries their chats over; ending them first cut the chats.
    app_tree = set(proc_table.tree(procs, os.getpid()))
    ended = []
    for state in default_app_data_dir().glob("shared_mcp*.json"):
        if not _TOOL_SERVER_STATE.match(state.name):
            continue
        try:
            pid = int(json.loads(state.read_text(encoding="utf-8")).get("pid") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        proc = procs.get(pid)
        if not pid or pid in app_tree or proc is None or proc.name.lower() not in ours:
            continue  # gone, the app or its agent hosts, or a pid Windows gave to another program
        if terminate(pid):
            ended.append(pid)
            try:
                state.unlink()  # its own exit would have cleared it
            except OSError:
                pass
    return ended


def _is_ducky(path: str) -> bool:
    return Path(path).name.lower() in {exe.lower() for exe in EXES}


def ducky_paths() -> list[str]:
    """Ducky's own EXEs: this one's folder, the installed one's, every downloaded version's."""
    from frontend import app_versions

    folders = [Path(sys.executable).parent]
    installed = str(app_versions.read_current().get("dir") or "")
    if installed:
        folders.append(Path(installed))
    paths = [str(folder / exe) for folder in folders for exe in EXES]
    paths += [str(app_versions.versions_dir() / "*" / exe) for exe in EXES]
    return list(dict.fromkeys(os.path.normpath(p) for p in paths))


def allow_command(paths: list[str]) -> str:
    """The admin PowerShell line, as -EncodedCommand text (no quoting through the shell).
    Add-MpPreference adds to the person's own allowed apps; Set-MpPreference would replace them."""
    quoted = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    script = ("$ErrorActionPreference = 'Stop'; "
              f"Add-MpPreference -ControlledFolderAccessAllowedApplications {quoted}")
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def run_as_admin(encoded: str, timeout_s: float = 120.0) -> bool:
    """Windows' admin prompt for one PowerShell command. False when declined or it failed."""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = "powershell.exe"
    info.lpParameters = f"-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded}"
    info.nShow = 0  # SW_HIDE
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return False  # 1223: the person chose No
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.WaitForSingleObject(info.hProcess, int(timeout_s * 1000))
        code = wintypes.DWORD(1)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value == 0
    finally:
        kernel32.CloseHandle(info.hProcess)


def ask_text(items: list[str]) -> str:
    names = ["UEFN Ducky" if i == DUCKY else f"{Path(i).name} ({Path(i).parent})" for i in items]
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    them = "it" if len(names) == 1 else "them"
    agents = "" if items == [DUCKY] else " Your AI agents and UEFN save your projects with these programs."
    after = ", then UEFN Ducky restarts" if DUCKY in items else ""
    return (f"Windows Controlled folder access (ransomware protection) is blocking {who} from saving "
            f"into your protected folders, such as Documents where your Fortnite projects are.{agents}\n\n"
            f"Allow {them}? Windows asks for permission next{after}. "
            "The protection stays on for every other app.")


def _box(text: str, yes_no: bool) -> bool:
    import ctypes

    flags = (0x4 if yes_no else 0x0) | 0x30 | 0x10000 | 0x40000  # Yes/No or OK, warning, in front
    return int(ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags)) in (1, 6)


def _restart() -> bool:
    """End the tool servers, then restart the app (does not return when it restarts)."""
    try:
        end_tool_servers()
    except Exception:  # noqa: BLE001 - the app's restart still helps
        _log.warning("tool servers not ended", exc_info=True)
    from frontend import update_swap

    return bool(update_swap.restart().get("ok"))


def check(
    *,
    probe: bool = True,
    blocked: Callable[[], bool] | None = None,
    mode: Callable[[], int] = protection_mode,
    events: Callable[[], list[tuple[str, str]]] = blocked_programs,
    ask: Callable[[str], bool] = lambda text: _box(text, True),
    tell: Callable[[str], None] = lambda text: _box(text, False),
    allow: Callable[[list[str]], bool] = lambda paths: run_as_admin(allow_command(paths)),
    restart: Callable[[], bool] = _restart,
) -> str:
    """One check; returns what happened (tests read it). ``probe``: also try Ducky's own write."""
    from frontend.bundle_root import is_packaged_runtime

    # From source the writes are python.exe's, and a test copy has nobody to answer.
    if os.name != "nt" or os.environ.get("DUCKY_SANDBOX") or not is_packaged_runtime():
        return "skipped"
    state = _read_state()
    declined = {k for k, v in (state.get("declined") or {}).items() if v == __version__}
    allowed = dict(state.get("allowed") or {})
    seen = str(state.get("seen_until") or "")
    found = [(when, path) for when, path in events() if when > seen]
    state["seen_until"] = max([seen, *(when for when, _ in found)])
    others = list(dict.fromkeys(p for _, p in found if not _is_ducky(p) and p not in declined))
    ducky = False
    if DUCKY not in declined:
        if any(_is_ducky(p) for _, p in found):
            ducky = True
        elif probe:
            if blocked is None:
                blocked = lambda: any(write_blocked(f) for f in probe_folders())  # noqa: E731
            ducky = blocked() and mode() == BLOCK  # refused for another reason: not ours to fix
    if ducky and allowed.get(DUCKY) == __version__:
        ducky = False  # allowed and restarted, still refused: an organization's policy wins
        state["declined"] = {**(state.get("declined") or {}), DUCKY: __version__}
        _write_state(state)
        tell(STILL_BLOCKED_TEXT)
        if not others:
            return "still_blocked"
    items = ([DUCKY] if ducky else []) + others
    if not items:
        _write_state(state)
        return "ok"
    if not ask(ask_text(items)):
        state["declined"] = {**(state.get("declined") or {}), **dict.fromkeys(items, __version__)}
        _write_state(state)
        return "no"
    if not allow((ducky_paths() if ducky else []) + others):
        state["declined"] = {**(state.get("declined") or {}), **dict.fromkeys(items, __version__)}
        _write_state(state)
        tell(NOT_ALLOWED_TEXT)
        return "failed"
    state["allowed"] = {**allowed, **dict.fromkeys(items, __version__)}
    _write_state(state)
    if ducky and not restart():
        tell(RESTART_TEXT)  # a start that could not be launched: the person restarts it
    return "allowed"


def _run(probe: bool, delay_s: float = 0.0) -> None:
    if delay_s:
        time.sleep(delay_s)
    if not _busy.acquire(blocking=False):
        return
    try:
        _log.info("controlled folder access: %s", check(probe=probe))
    except Exception:
        _log.warning("controlled folder access check failed", exc_info=True)
    finally:
        _busy.release()


def check_soon() -> None:
    """After a start (the window's or the launcher backend's hello), on a thread of its own."""
    threading.Thread(target=_run, args=(True, CHECK_AFTER_S), name="folder-access-check", daemon=True).start()


def after_turn() -> None:
    """An agent turn ended: Windows may have blocked the agent's own writes (at most once a
    minute, in the app only: an agent host has no window to ask from or restart)."""
    from backend.process_role import is_app

    now = time.monotonic()
    if not is_app():
        return
    if now - _last_turn_check[0] < TURN_CHECK_EVERY_S:
        return
    _last_turn_check[0] = now
    threading.Thread(target=_run, args=(False,), name="folder-access-turn", daemon=True).start()
