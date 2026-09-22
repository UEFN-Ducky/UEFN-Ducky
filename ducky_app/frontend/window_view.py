"""Look at other desktop windows through the remote panel tunnel.

ponytail: WebRTC crops a getDisplayMedia screen track to this window
(primary-monitor assumption — fit_window moves it onto the work area and
keeps the viewer's aspect so the video fills the phone with no bars).
No JPEG path: a stream that cannot go peer-to-peer reports why. Covered
windows show whatever is on top; the stream brings the target to the
front so clicks hit the same pixels. Windows Graphics Capture if they need
occluded GPU windows. STUN-only — TURN is the upgrade for strict NATs.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

_KIND_ORDER = {"desktop": 0, "monitor": 1, "uefn": 2, "blender": 3, "app": 4}
_JUNK_TITLES = frozenset({"Program Manager", "DWM Notification Window", "Windows Input Experience"})
_MIN_VIEW = 100


def window_fit_size(
    width: int,
    height: int,
    max_w: int = 3840,
    max_h: int = 2160,
) -> tuple[int, int]:
    """Viewer pixels → window size: at least 400×300, scaled down to fit the
    work area while keeping the viewer's aspect (a portrait phone must not
    push the window off-screen — the capture only sees on-screen pixels)."""
    w = max(400, int(width or 0))
    h = max(300, int(height or 0))
    max_w = max(400, int(max_w or 0))
    max_h = max(300, int(max_h or 0))
    scale = min(1.0, max_w / w, max_h / h)
    if scale < 1.0:
        w = max(400, int(w * scale))
        h = max(300, int(h * scale))
    return min(w, max_w), min(h, max_h)


def kind_for(title: str, exe: str = "") -> str:
    blob = f"{title} {exe}".lower().replace("\\", "/")
    base = blob.rsplit("/", 1)[-1]
    if "unrealeditorfortnite" in blob or "unreal editor for fortnite" in blob:
        return "uefn"
    if "blender" in base or "blender" in blob:
        return "blender"
    return "app"


def _keep_window(
    *,
    title: str,
    owned: bool,
    toolwindow: bool,
    cloaked: bool,
    iconic: bool,
    width: int,
    height: int,
) -> bool:
    """Alt-Tab-ish: titled, unowned, not a tool/cloaked/tiny shell HWND."""
    if not title or title in _JUNK_TITLES:
        return False
    if owned or toolwindow or cloaked:
        return False
    if not iconic and (width < _MIN_VIEW or height < _MIN_VIEW):
        return False
    return True


def _screen_view_rows(monitors: list[tuple[int, int, int, int]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [{"id": "desktop", "title": "Entire desktop", "kind": "desktop"}]
    if len(monitors) < 2:
        return rows
    for i, (_l, _t, w, h) in enumerate(monitors):
        rows.append({"id": f"monitor:{i}", "title": f"Display {i + 1} ({w}\u00d7{h})", "kind": "monitor"})
    return rows


def _view_rect(
    view_id: str,
    *,
    desktop: tuple[int, int, int, int],
    monitors: list[tuple[int, int, int, int]],
) -> tuple[int, int, int, int] | None:
    if view_id == "desktop":
        sl, st, sw, sh = desktop
        return sl, st, sl + sw, st + sh
    if view_id.startswith("monitor:"):
        try:
            i = int(view_id.split(":", 1)[1])
        except ValueError:
            return None
        if 0 <= i < len(monitors):
            left, top, w, h = monitors[i]
            return left, top, left + w, top + h
    return None


def _is_screen_view(view: object) -> bool:
    s = str(view or "").strip()
    return s == "desktop" or s.startswith("monitor:")


def _as_hwnd(view: object) -> int:
    s = str(view or "").strip()
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    return 0


def list_window_views() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    rows = _screen_view_rows(_monitors()) + _enum_windows()
    rows.sort(key=lambda r: (_KIND_ORDER.get(str(r.get("kind") or "app"), 9), str(r.get("title") or "").lower()))
    return rows


_UEFN_EDITOR_EXE = "UnrealEditorFortnite.exe"
_UEFN_SHIPPING_EXE = "UnrealEditorFortnite-Win64-Shipping.exe"
_UEFN_REL_EXES = (
    Path("FortniteGame") / "Binaries" / "Win64" / _UEFN_SHIPPING_EXE,
    Path("Engine") / "Binaries" / "Win64" / _UEFN_EDITOR_EXE,
)
_UEFN_NOT_FOUND = (
    "Unreal Editor for Fortnite not found. Install UEFN from the Epic Games Launcher."
)


def uefnproject_path(root: str | os.PathLike[str] | None = None):
    """Resolve the current island's ``*.uefnproject`` (editor argv, not startfile)."""
    if root is None:
        from frontend.settings import PanelSettings

        raw = (PanelSettings.load().uefn_project_root or "").strip()
        if not raw:
            raise RuntimeError("No project selected")
        root = raw
    p = Path(root)
    if p.is_file() and p.suffix.lower() == ".uefnproject":
        return p
    if p.is_dir():
        matches = sorted(p.glob("*.uefnproject"))
        if matches:
            return matches[0]
    raise RuntimeError(f"No .uefnproject in {p}")


def kill_uefn_cmd() -> list[str]:
    return ["taskkill", "/IM", _UEFN_SHIPPING_EXE, "/IM", _UEFN_EDITOR_EXE, "/F"]


def launch_uefn_cmd(
    exe: object,
    project: object | None = None,
    extra: object = None,
) -> list[str]:
    """Open UEFN through the editor binary.

    A bare ``.uefnproject`` path is ignored (the hub still highlights the last
    island). ``-ValkyrieProject=<path>`` is the switch the shipping editor
    actually opens. Verified: hub selected ExampleProject1, this argv opened Tycoony.
    """
    cmd = [str(exe)]
    if extra:
        cmd.extend(str(a) for a in extra if str(a).strip())
    if project:
        cmd.append(f"-ValkyrieProject={project}")
    return cmd


def fortnite_roots_from_launcher_dat(raw: str) -> list[str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[str] = []
    for item in data.get("InstallationList") or []:
        if not isinstance(item, dict):
            continue
        loc = str(item.get("InstallLocation") or "").strip()
        blob = f"{item.get('AppName', '')} {item.get('ArtifactId', '')} {loc}".lower()
        if loc and "fortnite" in blob:
            out.append(loc)
    return out


def fortnite_studio_launch_from_item(raw: str) -> tuple[str, list[str]] | None:
    """Parse one Epic ``.item``; Fortnite Studio only (never the game client)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    app = str(data.get("AppName") or "")
    display = str(data.get("DisplayName") or "").lower()
    if app != "Fortnite_Studio" and "unreal editor for fortnite" not in display:
        return None
    loc = str(data.get("InstallLocation") or "").strip()
    rel = str(data.get("LaunchExecutable") or "").replace("/", os.sep).strip()
    if not loc or not rel:
        return None
    extra = [a for a in shlex.split(str(data.get("LaunchCommand") or ""), posix=False) if a]
    return str(Path(loc) / rel), extra


def _editor_on_root(root: Path) -> str | None:
    for rel in _UEFN_REL_EXES:
        cand = root / rel
        if cand.is_file():
            return str(cand)
    return None


def _programdata() -> Path:
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))


def uefn_editor_launch() -> tuple[str, list[str]]:
    """Studio shipping exe + Epic LaunchCommand args, then legacy editor exe."""
    manifests = _programdata() / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    if manifests.is_dir():
        for item in sorted(manifests.glob("*.item")):
            try:
                raw = item.read_text(encoding="utf-8")
            except OSError:
                continue
            parsed = fortnite_studio_launch_from_item(raw)
            if parsed and Path(parsed[0]).is_file():
                return parsed
    roots: list[str] = [
        str(Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Epic Games" / "Fortnite")
    ]
    for dat in (
        _programdata() / "Epic" / "UnrealEngineLauncher" / "LauncherInstalled.dat",
        _programdata() / "Epic" / "EpicGamesLauncher" / "Data" / "LauncherInstalled.dat",
    ):
        try:
            raw = dat.read_text(encoding="utf-8")
        except OSError:
            continue
        roots.extend(fortnite_roots_from_launcher_dat(raw))
    seen: set[str] = set()
    for loc in roots:
        key = loc.lower()
        if key in seen:
            continue
        seen.add(key)
        found = _editor_on_root(Path(loc))
        if found:
            return found, []
    raise RuntimeError(_UEFN_NOT_FOUND)


def uefn_editor_exe() -> str:
    return uefn_editor_launch()[0]


def _kill_uefn_editor() -> bool:
    if sys.platform != "win32":
        return False
    import subprocess

    # taskkill returns non-zero when either image is missing, even if the one
    # that was running got killed. Trust the process check, not the exit code.
    was = _uefn_running()
    subprocess.run(
        kill_uefn_cmd(),
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if not was:
        return False
    return not _uefn_running()


def _start_uefn(exe: object, project: object | None = None, extra: object = None) -> None:
    import subprocess

    flags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    subprocess.Popen(
        launch_uefn_cmd(exe, project, extra),
        cwd=str(Path(str(exe)).parent),
        close_fds=True,
        creationflags=flags,
    )


def launch_uefn() -> dict[str, Any]:
    """Editor hub only — no island argv."""
    exe, extra = uefn_editor_launch()
    _start_uefn(exe, None, extra)
    return {"ok": True, "exe": exe, "path": ""}


def launch_uefn_project(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    exe, extra = uefn_editor_launch()
    path = uefnproject_path(root) if root else uefnproject_path()
    _start_uefn(exe, path, extra)
    return {"ok": True, "exe": exe, "path": str(path)}


_WM_CLOSE = 0x0010
_CLOSE_WAIT_S = 30.0
_READY_CAP_S = 300.0


def _request_uefn_close() -> int:
    """Post WM_CLOSE to UEFN's top-level windows. 0 when none are up."""
    if sys.platform != "win32":
        return 0
    try:
        from backend.tools.core.uefn_modal import _enum_uefn_windows
    except Exception:
        return 0
    wins = _enum_uefn_windows()
    mains = [w for w in wins if not w.get("owned")] or list(wins)
    if not mains:
        return 0
    import ctypes

    user32 = ctypes.windll.user32
    posted = 0
    for win in mains:
        if user32.PostMessageW(int(win["hwnd"]), _WM_CLOSE, 0, 0):
            posted += 1
    return posted


def _uefn_running() -> bool:
    if sys.platform != "win32":
        return False
    import subprocess

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for image in (_UEFN_SHIPPING_EXE, _UEFN_EDITOR_EXE):
        # /FO CSV — the default table truncates the image name, so the shipping
        # exe never matched and a live editor looked already closed.
        r = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            creationflags=flags,
        )
        if image.lower() in (r.stdout or "").lower():
            return True
    return False


def _wait_uefn_exit(timeout: float) -> bool:
    import time

    try:
        from backend.tools.core.uefn_modal import auto_dismiss_save_modal
    except Exception:
        auto_dismiss_save_modal = None  # type: ignore[assignment]
    deadline = time.time() + max(0.0, float(timeout))
    while time.time() < deadline:
        if not _uefn_running():
            return True
        if auto_dismiss_save_modal is not None:
            try:
                auto_dismiss_save_modal()
            except Exception:
                pass
        time.sleep(0.4)
    return not _uefn_running()


def close_uefn(*, timeout: float = _CLOSE_WAIT_S) -> dict[str, Any]:
    """Ask UEFN to close, press Save if that modal appears, then taskkill.

    Graceful path is WM_CLOSE plus the existing save-modal press. ``taskkill /F``
    runs only when the process is still up after ``timeout`` seconds.
    """
    if _request_uefn_close() and _wait_uefn_exit(timeout):
        return {"ok": True, "graceful": True, "killed": False}
    return {"ok": True, "graceful": False, "killed": _kill_uefn_editor()}


def wait_uefn_ready(
    *,
    timeout: float = 180.0,
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Poll listener health until it is online and the open island matches."""
    import time

    from backend.bridge.client import listener_get_health
    from backend.bridge.status import _project_from_health
    from frontend.settings import PANEL_LISTENER_PORT, PanelSettings

    cap = min(max(float(timeout or 0), 1.0), _READY_CAP_S)
    started = time.time()
    deadline = started + cap
    if root:
        try:
            selected = str(Path(uefnproject_path(root)).parent)
        except Exception:
            selected = str(root)
    else:
        selected = (PanelSettings.load().uefn_project_root or "").strip()
    last_name = ""
    while time.time() < deadline:
        health = listener_get_health(int(PANEL_LISTENER_PORT), timeout=0.8)
        if isinstance(health, dict) and health.get("status") == "ok":
            _proj_dir, name, match = _project_from_health(
                health, selected_project_root=selected
            )
            last_name = name
            # Health says match=True before a project name exists. Require the name
            # when a project was requested, so "hub, no island" is not ready.
            if match and (name or not selected):
                return {
                    "ok": True,
                    "seconds": round(time.time() - started, 1),
                    "project_name": name,
                    "project_match": True,
                }
        time.sleep(2.0)
    return {
        "ok": False,
        "error": "timed out waiting for UEFN",
        "seconds": round(time.time() - started, 1),
        "project_name": last_name,
        "project_match": False,
    }


def restart_uefn_project(
    root: str | os.PathLike[str] | None = None,
    *,
    wait: bool = False,
    timeout: float = 180.0,
) -> dict[str, Any]:
    closed = close_uefn()
    out = launch_uefn_project(root)
    out["killed"] = bool(closed.get("killed"))
    out["graceful"] = bool(closed.get("graceful"))
    if wait:
        ready = wait_uefn_ready(timeout=timeout, root=root)
        out["ready"] = ready
        if not ready.get("ok"):
            out["ok"] = False
            out["error"] = ready.get("error") or "UEFN did not come online"
    return out


def window_box(hwnd: object) -> dict[str, int]:
    """On-screen rect plus virtual/primary metrics for the WebRTC crop."""
    if sys.platform != "win32":
        return {}
    sl, st, sw, sh, pw, ph = _screen_metrics()
    extras = {
        "screen_left": sl,
        "screen_top": st,
        "screen_w": sw,
        "screen_h": sh,
        "primary_w": pw,
        "primary_h": ph,
    }
    vid = str(hwnd or "").strip()
    if _is_screen_view(vid):
        rect = _view_rect(vid, desktop=(sl, st, sw, sh), monitors=_monitors())
        if not rect:
            return {}
        left, top, right, bottom = rect
        return {"left": left, "top": top, "right": right, "bottom": bottom, **extras}
    hid = _as_hwnd(vid)
    if hid <= 0:
        return {}
    box = _window_box(hid)
    if not box:
        return {}
    left, top, right, bottom = box
    return {"left": left, "top": top, "right": right, "bottom": bottom, **extras}


def map_norm_to_screen(box: tuple[int, int, int, int], nx: float, ny: float) -> tuple[int, int]:
    left, top, right, bottom = box
    width = max(1, right - left)
    height = max(1, bottom - top)
    nx = 0.0 if nx < 0 else 1.0 if nx > 1 else float(nx)
    ny = 0.0 if ny < 0 else 1.0 if ny > 1 else float(ny)
    return left + int(nx * (width - 1)), top + int(ny * (height - 1))


# hwnd -> monotonic time of the last forced raise. A viewer that reconnects in
# a loop must not steal focus from the user on every attempt.
_LAST_RAISE: dict[int, float] = {}
RAISE_COOLDOWN_S = 15.0


def _raise_allowed(hwnd: int) -> bool:
    import time

    now = time.monotonic()
    last = _LAST_RAISE.get(hwnd, 0.0)
    if now - last < RAISE_COOLDOWN_S:
        return False
    _LAST_RAISE[hwnd] = now
    return True


def bring_to_front(hwnd: int) -> bool:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return False
    if _foreground_hwnd() == hwnd:
        return True
    if not _raise_allowed(hwnd):
        return False
    return _raise_window(hwnd)


def set_window_topmost(hwnd: int, on: bool) -> bool:
    """Pin (or unpin) the window above all others for the stream's lifetime.

    getDisplayMedia captures on-screen pixels, so an always-on-top occluder
    (a pinned Discord, a media overlay) would cover the watched window even
    after we raise it. Holding the target TOPMOST while a viewer is connected
    guarantees the capture sees it; teardown clears the flag.
    """
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return False
    import ctypes

    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    insert_after = -1 if on else -2  # HWND_TOPMOST / HWND_NOTOPMOST
    flags = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
    user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags)
    if on and _foreground_hwnd() != hwnd and _raise_allowed(hwnd):
        _raise_window(hwnd)
    return True


def fit_window(hwnd: int, width: int, height: int) -> bool:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return False
    box = _window_box(hwnd)
    if not box:
        return False
    left, top, right, bottom = box
    ox, oy, aw, ah = _primary_work_area()
    w, h = window_fit_size(width, height, aw, ah)
    if (
        abs((right - left) - w) < 8
        and abs((bottom - top) - h) < 8
        and abs(left - ox) < 8
        and abs(top - oy) < 8
    ):
        return False
    import ctypes

    # SWP_NOZORDER | SWP_NOACTIVATE — resize without a focus fight that hitchs UEFN.
    ctypes.windll.user32.SetWindowPos(hwnd, 0, int(ox), int(oy), int(w), int(h), 0x0014)
    # A just-fitted window can land behind whatever occupied that spot; the
    # capture would then stream the wrong window. Surface it once.
    if _foreground_hwnd() != hwnd:
        _raise_window(hwnd)
    return True


# MOVE | ABSOLUTE | VIRTUALDESK — click at a pixel without SetCursorPos (which
# un-hides the cursor and a later button event can miss by a move).
_ABS = 0x0001 | 0x8000 | 0x4000
_BUTTON_DOWN = {0: 0x0002, 1: 0x0020, 2: 0x0008}
_BUTTON_UP = {0: 0x0004, 1: 0x0040, 2: 0x0010}


def _abs_xy(x: int, y: int) -> tuple[int, int]:
    sl, st, sw, sh, _pw, _ph = _screen_metrics()
    return int((x - sl) * 65535 / max(1, sw - 1)), int((y - st) * 65535 / max(1, sh - 1))


def _pointer_on_box(
    box: tuple[int, int, int, int] | None,
    kind: str,
    nx: float,
    ny: float,
    *,
    button: int = 0,
    delta: int = 0,
    dx: int | None = None,
    dy: int | None = None,
) -> None:
    if kind == "move" and dx is not None:
        _send_mice([(int(dx), int(dy or 0), 0, 0x0001)])
        return
    down = _BUTTON_DOWN.get(int(button), 0x0002)
    up = _BUTTON_UP.get(int(button), 0x0004)
    if kind in ("down", "up") and box is None:
        _send_mice([(0, 0, 0, down if kind == "down" else up)])
        return
    if not box:
        return
    ax, ay = _abs_xy(*map_norm_to_screen(box, nx, ny))
    if kind == "move":
        _send_mice([(ax, ay, 0, _ABS)])
        return
    if kind == "wheel":
        _send_mice([(ax, ay, 0, _ABS), (0, 0, int(delta) * 120, 0x0800)])
        return
    if kind == "dblclick":
        _send_mice(
            [
                (ax, ay, 0, _ABS | down),
                (ax, ay, 0, _ABS | up),
                (ax, ay, 0, _ABS | down),
                (ax, ay, 0, _ABS | up),
            ]
        )
        return
    if kind == "down":
        _send_mice([(ax, ay, 0, _ABS | down)])
        return
    if kind == "up":
        _send_mice([(ax, ay, 0, _ABS | up)])


def inject_pointer(
    hwnd: int,
    kind: str,
    nx: float,
    ny: float,
    *,
    button: int = 0,
    delta: int = 0,
    dx: int | None = None,
    dy: int | None = None,
    xy: bool = True,
) -> None:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return
    if kind == "move" and dx is not None:
        _pointer_on_box(None, kind, nx, ny, dx=dx, dy=dy)
        return
    if not xy and kind in ("down", "up"):
        # Button-only: no teleport, no ALT-raise (look / stuck-LMB clear).
        _pointer_on_box(None, kind, 0.0, 0.0, button=button)
        return
    box = _window_box(hwnd)
    if not box:
        return
    # Raise before a new press, never between down/up or the two clicks of a dblclick.
    if kind not in ("move", "up"):
        bring_to_front(hwnd)
    _pointer_on_box(box, kind, nx, ny, button=button, delta=delta, dx=dx, dy=dy)


def inject_key(hwnd: int, key: str, *, down: bool = True) -> None:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return
    vk = _vk_for_key(key)
    if not vk:
        return
    if down:
        bring_to_front(hwnd)
    _send_key(vk, down=down)


def handle_stream_message(view: object, payload: bytes) -> None:
    try:
        event = json.loads(payload.decode("utf-8"))
    except Exception:
        return
    if not isinstance(event, dict):
        return
    kind = str(event.get("type") or "")
    hid = _as_hwnd(view)
    screen = _is_screen_view(view)
    if kind == "size":
        if hid:
            fit_window(hid, int(event.get("w") or 0), int(event.get("h") or 0))
        return
    if kind in ("down", "up", "move", "wheel", "dblclick"):
        rel = "dx" in event
        has_xy = "x" in event
        dx = int(event.get("dx") or 0) if rel else None
        dy = int(event.get("dy") or 0) if rel else None
        nx = float(event.get("x") or 0) if has_xy else 0.0
        ny = float(event.get("y") or 0) if has_xy else 0.0
        button = int(event.get("button") or 0)
        delta = int(event.get("delta") or 0)
        if screen:
            if sys.platform != "win32":
                return
            if not has_xy and kind in ("down", "up"):
                _pointer_on_box(None, kind, 0.0, 0.0, button=button)
                return
            box = _view_rect(
                str(view).strip(),
                desktop=_screen_metrics()[:4],
                monitors=_monitors(),
            )
            _pointer_on_box(box, kind, nx, ny, button=button, delta=delta, dx=dx, dy=dy)
        else:
            inject_pointer(hid, kind, nx, ny, button=button, delta=delta, dx=dx, dy=dy, xy=has_xy)
        return
    if kind in ("key", "keydown", "keyup"):
        key = str(event.get("key") or "")
        if screen:
            if sys.platform != "win32":
                return
            vk = _vk_for_key(key)
            if vk:
                _send_key(vk, down=kind != "keyup")
        else:
            inject_key(hid, key, down=kind != "keyup")


def _enum_windows() -> list[dict[str, Any]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    me = os.getpid()
    out: list[dict[str, Any]] = []

    def _text(hwnd: int) -> str:
        n = int(user32.GetWindowTextLengthW(hwnd) or 0)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return (buf.value or "").strip()

    def _callback(hwnd, _lparam):
        hwnd = int(hwnd)
        if not user32.IsWindow(hwnd):
            return True
        iconic = bool(user32.IsIconic(hwnd))
        visible = bool(user32.IsWindowVisible(hwnd))
        if not visible and not iconic:
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) == me:
            return True
        exe = _exe_name(int(pid.value))
        title = _text(hwnd)
        owner = bool(user32.GetWindow(hwnd, 4))  # GW_OWNER
        get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        ex = int(get_long(hwnd, -20) or 0)  # GWL_EXSTYLE
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if not _keep_window(
            title=title,
            owned=owner,
            toolwindow=bool(ex & 0x00000080),  # WS_EX_TOOLWINDOW
            cloaked=_hwnd_cloaked(hwnd),
            iconic=iconic,
            width=int(rect.right - rect.left),
            height=int(rect.bottom - rect.top),
        ):
            return True
        out.append(
            {
                "id": str(int(hwnd)),
                "title": title[:80],
                "kind": kind_for(title, exe),
            }
        )
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    return out


def _foreground_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    import ctypes

    return int(ctypes.windll.user32.GetForegroundWindow() or 0)


def _raise_window(hwnd: int) -> bool:
    """Bring the target above every other window and, when safe, give it focus.

    getDisplayMedia captures the monitor's actual pixels, so a covered target
    streams whatever sits on top. The TOPMOST→NOTOPMOST flip (NOACTIVATE)
    un-occludes it without touching focus. Focus is only stolen when the
    foreground window is NOT ours: yanking activation away from the Ducky panel
    sends input-synchronous WM_ACTIVATE/WM_KILLFOCUS into our UI thread while
    it may be mid-COM-call into WebView2 — that is the AppHangXProcB1
    (host ↔ msedgewebview2.exe) deadlock that froze the panel. Never
    AttachThreadInput to the foreground thread for the same reason: a shared
    input queue turns one stuck thread into two.
    """
    import ctypes

    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE

    flags = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)
    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)

    fg = int(user32.GetForegroundWindow() or 0)
    if fg and _is_our_hwnd(fg):
        return True

    # Drop the foreground-lock timeout so SetForegroundWindow is honored.
    try:
        prev = ctypes.c_uint(0)
        user32.SystemParametersInfoW(0x2000, 0, ctypes.byref(prev), 0)  # SPI_GETFOREGROUNDLOCKTIMEOUT
        user32.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(0), 0)  # SPI_SETFOREGROUNDLOCKTIMEOUT
    except Exception:
        prev = None
    try:
        # ALT tap unlocks SetForegroundWindow for background callers.
        _send_key(0x12, down=True)
        _send_key(0x12, down=False)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if prev is not None:
            try:
                user32.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(prev.value), 0)
            except Exception:
                pass
    return True


def _primary_work_area() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the primary monitor minus the taskbar."""
    if sys.platform != "win32":
        return 0, 0, 1920, 1080
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
        return 0, 0, 1920, 1080
    return (
        int(rect.left),
        int(rect.top),
        max(400, int(rect.right - rect.left)),
        max(300, int(rect.bottom - rect.top)),
    )


def _hwnd_cloaked(hwnd: int) -> bool:
    import ctypes

    v = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        return False
    return bool(v.value)


def _monitors() -> list[tuple[int, int, int, int]]:
    """(left, top, width, height), primary first, then left-to-right."""
    if sys.platform != "win32":
        return [(0, 0, 1920, 1080)]
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32 = ctypes.windll.user32
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )
    found: list[tuple[int, int, int, int, int]] = []

    def _cb(hmon, _hdc, _prect, _lp):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append(
                (
                    int(r.left),
                    int(r.top),
                    int(r.right - r.left),
                    int(r.bottom - r.top),
                    0 if info.dwFlags & 1 else 1,
                )
            )
        return True

    user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_cb), 0)
    found.sort(key=lambda m: (m[4], m[0], m[1]))
    return [(l, t, w, h) for l, t, w, h, _p in found] or [(0, 0, 1920, 1080)]


def _screen_metrics() -> tuple[int, int, int, int, int, int]:
    if sys.platform != "win32":
        return 0, 0, 1920, 1080, 1920, 1080
    import ctypes

    user32 = ctypes.windll.user32
    return (
        int(user32.GetSystemMetrics(76)),
        int(user32.GetSystemMetrics(77)),
        int(user32.GetSystemMetrics(78) or 1920),
        int(user32.GetSystemMetrics(79) or 1080),
        int(user32.GetSystemMetrics(0) or 1920),
        int(user32.GetSystemMetrics(1) or 1080),
    )


def _window_box(hwnd: int) -> tuple[int, int, int, int] | None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _hwnd_pid(hwnd: int) -> int:
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _is_our_hwnd(hwnd: int) -> bool:
    try:
        return _hwnd_pid(hwnd) == os.getpid()
    except Exception:
        return True


def _input_structs():
    import ctypes

    extra = ctypes.c_ulong(0)

    class Mouse(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class Key(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class Union(ctypes.Union):
        _fields_ = [("mi", Mouse), ("ki", Key)]

    class Input(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("union", Union)]

    return ctypes, extra, Mouse, Key, Input


def _send_mice(events: list[tuple[int, int, int, int]]) -> None:
    """One SendInput for N mouse events. Each tuple is (dx, dy, data, flags)."""
    if not events:
        return
    ctypes, _extra, Mouse, _Key, Input = _input_structs()
    n = len(events)
    arr = (Input * n)()
    extras = []
    for i, (dx, dy, data, flags) in enumerate(events):
        extra = ctypes.c_ulong(0)
        extras.append(extra)
        arr[i].type = 0
        arr[i].union.mi = Mouse(int(dx), int(dy), int(data) & 0xFFFFFFFF, int(flags), 0, ctypes.pointer(extra))
    ctypes.windll.user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(Input))


def _send_key(vk: int, *, down: bool) -> None:
    ctypes, extra, _Mouse, Key, Input = _input_structs()
    inp = Input()
    inp.type = 1
    inp.union.ki = Key(int(vk) & 0xFFFF, 0, 0 if down else 0x0002, 0, ctypes.pointer(extra))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(Input))


_NAMED_VK = {
    "Enter": 0x0D,
    "Tab": 0x09,
    "Backspace": 0x08,
    "Escape": 0x1B,
    "Esc": 0x1B,
    " ": 0x20,
    "Space": 0x20,
    "ArrowLeft": 0x25,
    "ArrowUp": 0x26,
    "ArrowRight": 0x27,
    "ArrowDown": 0x28,
    "Delete": 0x2E,
    "Home": 0x24,
    "End": 0x23,
    "PageUp": 0x21,
    "PageDown": 0x22,
    "Shift": 0x10,
    "Control": 0x11,
    "Alt": 0x12,
    "Meta": 0x5B,
    "CapsLock": 0x14,
}


def _vk_for_key(key: str) -> int:
    if not key:
        return 0
    if key in _NAMED_VK:
        return _NAMED_VK[key]
    if key.startswith("F") and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1
    if len(key) == 1:
        if sys.platform != "win32":
            return ord(key.upper())
        import ctypes

        scan = int(ctypes.windll.user32.VkKeyScanW(ord(key)))
        return scan & 0xFF if scan != -1 else 0
    return 0


def _exe_name(pid: int) -> str:
    if pid <= 0:
        return ""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(260)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        path = buf.value or ""
        return path.replace("\\", "/").rsplit("/", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)
