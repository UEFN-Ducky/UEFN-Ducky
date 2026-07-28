"""Native WebView2 browser panes pinned inside app windows (plugin web panes).

A "browser pane" is a real WebView2 control added to the WinForms form that already
hosts the app's own WebView2 — NOT an iframe (sites that send X-Frame-Options /
frame-ancestors still load) and NOT a pywebview window (no js_api bridge is injected,
so foreign pages get a plain browser context with no path back into the app).

React owns the tab chrome (toolbar / URL bar) and reports the content rect in CSS px;
this module keeps the native control pinned to that rect. Navigation state (url / title
/ history / loading) is pushed to the hosting window's React bundle via
``window.__uefnPanelPush`` events of type ``browser_pane_state``.

Only http(s) and about: navigations are allowed — anything else (file:, ms-appx:, …)
is cancelled so a hostile page cannot navigate the pane at local content.

Self-embed / Droste block: navigating the pane to the app's own panel UI
(127.0.0.1 / localhost on the panel HTTP port or Vite :5173) is refused so the shell
cannot render inside itself. Bounds calibration always uses ``form.webview`` (the app's
Dock.Fill host), never a sibling browser pane — bad scale would shrink the pane every
frame (infinite tunnel).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_log = logging.getLogger("browser_overlay")

_lock = threading.RLock()
_panes: dict[str, _Pane] = {}
_main_window: Any = None
# Shared across every in-app browser tab. Creating a second WebView2 with the same
# UserDataFolder *without* reusing this Environment freezes the WinForms UI thread.
_shared_env: Any = None
_pending_env_panes: list[_Pane] = []
# True after the first pane has started opening the profile (CreationProperties).
_env_bootstrap_started: bool = False
# True while EnsureCoreWebView2Async is in flight — serialize ALL pane boots.
_ensure_inflight: bool = False

_SELF_EMBED_MSG = "Cannot navigate to the app's own UI (self-embed blocked)"


@dataclass
class _Pane:
    pane_id: str
    window: Any  # pywebview window whose WinForms form hosts the control
    control: Any = None  # Microsoft.Web.WebView2.WinForms.WebView2
    url: str = ""
    title: str = ""
    can_back: bool = False
    can_forward: bool = False
    loading: bool = False
    ready: bool = False
    failed: str = ""
    pending_url: str = ""
    # Last TLS error (ServerCertificateErrorDetected); cleared on clean navigate.
    cert_error: str = ""
    cert_error_status: str = ""
    # CSS-px rect in the host page's root coordinate space, plus the page viewport
    # size at report time. Mapping to WinForms units self-calibrates against the
    # host WebView2 control (see _apply_bounds) so every DPI/zoom mode is exact.
    css_bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    css_viewport: tuple[float, float] = (0.0, 0.0)
    visible: bool = True
    # Coalesce bounds floods: only one UI-thread apply in flight at a time.
    apply_queued: bool = False
    # Keep delegate refs alive for the lifetime of the control (pythonnet GC).
    _handlers: list[Any] = field(default_factory=list)


def configure(*, main_window: Any) -> None:
    global _main_window
    _main_window = main_window


def _window_for_wid(wid: str) -> Any:
    if not wid or wid == "main":
        return _main_window
    try:
        from frontend.ui_web import focus_windows

        w = focus_windows.window_for_wid(wid)
        if w is not None:
            return w
    except Exception:
        pass
    return _main_window


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    low = u.lower()
    if low.startswith(("http://", "https://", "about:")):
        return u
    if " " in u or "." not in u:
        # Not URL-shaped — let the toolbar turn it into a search; refuse here.
        return ""
    return "https://" + u


def is_app_ui_url(url: str) -> bool:
    """True when *url* points at this app's own panel UI (self-embed / Droste risk)."""
    u = (url or "").strip()
    if not u:
        return False
    try:
        parsed = urlparse(u)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return False
    port = parsed.port
    if port is None:
        port = 443 if (parsed.scheme or "").lower() == "https" else 80
    try:
        from frontend.ui_web.panel_httpd import PANEL_UI_HTTP_PORT
    except Exception:
        PANEL_UI_HTTP_PORT = 4199  # ponytail: fallback if panel_httpd unavailable
    return port in (PANEL_UI_HTTP_PORT, 5173)


def sane_scale(sx: float, sy: float) -> tuple[float, float]:
    """Reject absurd CSS→WinForms scale (bounds death-spiral guard)."""
    if sx < 0.25 or sx > 4.0 or sy < 0.25 or sy > 4.0:
        return (1.0, 1.0)
    if abs(sx - sy) > 0.5:
        return (1.0, 1.0)
    return (sx, sy)


def _is_allowed_url(url: str) -> bool:
    low = (url or "").strip().lower()
    if not low.startswith(("http://", "https://", "about:")):
        return False
    if is_app_ui_url(url):
        return False
    return True


def user_data_dir() -> str:
    from backend.skill import appdata_dir

    p = appdata_dir() / "webview2_browser"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def cdp_enabled() -> bool:
    """Remote debugging for chrome-devtools-mcp. Off by default (Cloudflare-safe).

    Enable with env ``UEFN_DUCKY_BROWSER_CDP=1`` or browser plugin pref ``enableCdp``.
    Takes effect on the next WebView2 Environment bootstrap (restart app / new profile).
    """
    env = (os.environ.get("UEFN_DUCKY_BROWSER_CDP") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from frontend.ui_web.plugin_host_api import prefs_plugin_get

        return bool(prefs_plugin_get("browser").get("enableCdp"))
    except Exception:
        return False


def additional_browser_arguments() -> str:
    """Args for CoreWebView2CreationProperties. Empty = normal Chrome-like browsing."""
    if cdp_enabled():
        # port=0 → OS picks a free port; DevToolsActivePort is written under EBWebView.
        return "--remote-debugging-port=0"
    return ""


def profile_locked() -> bool:
    """True when the shared UserDataFolder is held by a live pane or browser process."""
    with _lock:
        if _panes:
            return True
    port_file = Path(user_data_dir()) / "EBWebView" / "DevToolsActivePort"
    try:
        return port_file.is_file()
    except Exception:
        return False


def list_panes() -> list[dict[str, Any]]:
    with _lock:
        return [_state_dict(p) for p in _panes.values()]


def pick_pane_id(preferred: str = "", *, allow_fallback: bool = True) -> str:
    pid = (preferred or "").strip()
    with _lock:
        if pid and pid in _panes:
            return pid
        # Caller asked for a specific pane that is not open — do not silently
        # return a sibling (that made every padlock show the same YouTube tab).
        if pid and not allow_fallback:
            return ""
        if _panes:
            return next(iter(_panes))
    return ""


def _hide_other_panes(keep_id: str) -> None:
    """Only one native browser pane may be visible — siblings steal the viewport."""
    with _lock:
        others = [p for p in _panes.values() if p.pane_id != keep_id and p.visible]
    for other in others:
        other.visible = False
        other.css_bounds = (0.0, 0.0, 0.0, 0.0)
        if other.window is not None:
            _ui(other.window, lambda p=other: _apply_bounds(p))


def cdp_info() -> dict[str, Any]:
    """Chrome DevTools / chrome-devtools-mcp wiring for the browser pane profile."""
    ud = Path(user_data_dir())
    eb_dir = ud / "EBWebView"
    devtools_port: int | None = None
    devtools_path: str | None = None
    active_port_file = eb_dir / "DevToolsActivePort"
    if active_port_file.is_file():
        try:
            lines = active_port_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            if lines:
                devtools_port = int(lines[0].strip())
            if len(lines) > 1:
                devtools_path = lines[1].strip()
        except Exception:
            pass
    ebwebview_dir = str(eb_dir)
    enabled = cdp_enabled()
    chrome_devtools_mcp_args = [
        "--autoConnect",
        f"--user-data-dir={ebwebview_dir}",
    ]
    return {
        "user_data_dir": str(ud),
        "ebwebview_dir": ebwebview_dir,
        "devtools_port": devtools_port,
        "devtools_path": devtools_path,
        "cdp_enabled": enabled,
        "chrome_devtools_mcp_args": chrome_devtools_mcp_args if enabled else [],
        "hint": (
            None
            if enabled
            else "CDP off (Cloudflare-safe). Enable browser pref enableCdp or UEFN_DUCKY_BROWSER_CDP=1, then restart the app."
        ),
    }


def runtime_info() -> dict[str, Any]:
    """Chrome / WebView2 version + profile paths for the in-app browser trust UI."""
    info = cdp_info()
    browser_version = ""
    user_agent = ""
    with _lock:
        ready = [p for p in _panes.values() if p.ready and p.control is not None]
    if ready:
        pane = ready[0]

        def op() -> dict[str, str]:
            out: dict[str, str] = {}
            try:
                core = pane.control.CoreWebView2
                env = getattr(core, "Environment", None)
                ver = ""
                if env is not None:
                    ver = str(getattr(env, "BrowserVersionString", "") or "")
                if not ver:
                    try:
                        from Microsoft.Web.WebView2.Core import CoreWebView2Environment

                        ver = str(CoreWebView2Environment.GetAvailableBrowserVersionString() or "")
                    except Exception:
                        pass
                out["browser_version"] = ver
                try:
                    settings = getattr(core, "Settings", None)
                    ua = str(getattr(settings, "UserAgent", "") or "") if settings else ""
                    out["user_agent"] = ua
                except Exception:
                    out["user_agent"] = ""
            except Exception as exc:
                out["error"] = str(exc)
            return out

        try:
            box = _ui_sync(pane.window, op, timeout_s=1.5)
        except Exception:
            box = {}
        browser_version = box.get("browser_version", "")
        user_agent = box.get("user_agent", "")
    else:
        try:
            from Microsoft.Web.WebView2.Core import CoreWebView2Environment

            browser_version = str(CoreWebView2Environment.GetAvailableBrowserVersionString() or "")
        except Exception:
            pass

    # Chromium major from "120.0.2210.91" / "126.0.2592.87" style strings.
    chrome_major = ""
    if browser_version:
        chrome_major = browser_version.split(".")[0].strip()

    cdp_on = bool(info.get("cdp_enabled"))
    protections = [
        "Isolated WebView2 profile (not system Chrome)",
        "Host objects disabled in the visited page",
        "WebMessage bridge disabled (page cannot call the app)",
        "App UI self-embed / Droste navigations blocked",
        "Bad TLS certificates cancelled (not auto-trusted)",
        "Only http(s) and about: navigations allowed",
    ]
    if cdp_on:
        protections.append(
            "Remote debugging ON — Cloudflare / bot checks may fail; disable enableCdp for normal browsing"
        )
    else:
        protections.append("Remote debugging OFF (default) — closer to real Chrome for Cloudflare checks")

    return {
        **info,
        "ok": True,
        "engine": "WebView2 (Chromium)",
        "browser_version": browser_version,
        "chrome_major": chrome_major,
        "user_agent": user_agent,
        "secure_profile": True,
        "isolation": "Separate UserDataFolder — not your system Chrome profile",
        "https_only_nav": True,
        "self_embed_blocked": True,
        "host_objects": False,
        "web_message": False,
        "cdp_enabled": cdp_on,
        "protections": protections,
    }


def _cookie_rows(cookies: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if cookies is None:
        return out
    for c in cookies:
        try:
            out.append(
                {
                    "name": str(getattr(c, "Name", "") or ""),
                    "domain": str(getattr(c, "Domain", "") or ""),
                    "path": str(getattr(c, "Path", "") or ""),
                    "secure": bool(getattr(c, "IsSecure", False)),
                    "http_only": bool(getattr(c, "IsHttpOnly", False)),
                    "session": bool(getattr(c, "IsSession", False)),
                }
            )
        except Exception:
            continue
    return out


def _on_ui_thread(window: Any) -> bool:
    """True when the caller is already on the pane's WinForms UI thread."""
    native = getattr(window, "native", None)
    if native is None:
        return False
    try:
        # InvokeRequired False ⇒ we are on the UI thread.
        return not bool(getattr(native, "InvokeRequired", True))
    except Exception:
        return False


def _await_cookie_task(task: Any, timeout_s: float = 2.0) -> tuple[list[dict[str, Any]], str]:
    """Wait for GetCookiesAsync off the UI thread.

    Blocking GetResult/Wait on the WinForms UI thread deadlocks (cookie completion
    needs that same thread) — padlock then hits the plugin bridge 6s timeout and
    the whole app stops taking clicks.
    """
    if task is None:
        return [], ""
    box: dict[str, Any] = {"v": None, "e": ""}
    done = threading.Event()

    def worker() -> None:
        try:
            if hasattr(task, "GetAwaiter"):
                box["v"] = task.GetAwaiter().GetResult()
            else:
                box["v"] = getattr(task, "Result", None)
        except Exception as exc:
            box["e"] = str(exc)
        finally:
            done.set()

    threading.Thread(target=worker, name="browser-cookies", daemon=True).start()
    if not done.wait(timeout_s):
        return [], "cookie read timed out"
    if box["e"]:
        return [], str(box["e"])
    return _cookie_rows(box["v"]), ""


def site_security_info(pane_id: str = "") -> dict[str, Any]:
    """Chrome-like site security summary for the padlock / Settings panel."""
    wanted = (pane_id or "").strip()
    pid = pick_pane_id(wanted, allow_fallback=not wanted)
    with _lock:
        pane = _panes.get(pid) if pid else None
    runtime = runtime_info()
    if pane is None:
        return {
            "ok": False,
            "error": "no browser pane open" if not wanted else f"pane not open: {wanted}",
            "runtime": runtime,
        }

    cookie_task_box: dict[str, Any] = {"task": None, "err": ""}

    def read() -> dict[str, Any]:
        core = pane.control.CoreWebView2 if pane.control is not None else None
        url = pane.url
        title = pane.title
        playing = False
        muted = False
        if core is not None:
            try:
                url = str(core.Source or url or "")
            except Exception:
                pass
            try:
                title = str(core.DocumentTitle or title or "")
            except Exception:
                pass
            try:
                playing = bool(getattr(core, "IsDocumentPlayingAudio", False))
            except Exception:
                pass
            try:
                muted = bool(getattr(core, "IsMuted", False))
            except Exception:
                pass
            # Start only — never Wait/GetResult here (UI thread via _ui_sync).
            try:
                mgr = getattr(core, "CookieManager", None)
                if mgr is not None and url:
                    cookie_task_box["task"] = mgr.GetCookiesAsync(url)
            except Exception as exc:
                cookie_task_box["err"] = str(exc)

        kind = _connection_kind(url, pane.cert_error)
        host = ""
        scheme = ""
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            scheme = (parsed.scheme or "").lower()
        except Exception:
            pass

        if kind == "secure":
            headline = "Connection is secure"
            detail = "Your information (for example passwords or credit cards) is private when it is sent to this site."
            cert_label = "Certificate is valid"
        elif kind == "not_secure":
            headline = "Your connection to this site is not secure"
            detail = "You should not enter any sensitive information on this site (for example, passwords or credit cards), because it could be stolen by attackers."
            cert_label = "No certificate (HTTP)"
        elif kind == "cert_error":
            headline = "Your connection is not private"
            detail = pane.cert_error or "Attackers might be trying to steal your information from this site."
            cert_label = pane.cert_error_status or "Certificate error"
        else:
            headline = "Connection"
            detail = "This page does not use a standard web connection."
            cert_label = "—"

        return {
            "url": url,
            "title": title,
            "host": host,
            "scheme": scheme,
            "connection": kind,
            "headline": headline,
            "detail": detail,
            "certificate": cert_label,
            "cert_error": pane.cert_error,
            "cert_error_status": pane.cert_error_status,
            "cookies": [],
            "cookie_count": 0,
            "cookie_error": "",
            "is_playing_audio": playing,
            "is_muted": muted,
        }

    try:
        site = _ui_sync(pane.window, read) if pane.ready and pane.control else {
            "url": pane.url,
            "title": pane.title,
            "connection": _connection_kind(pane.url, pane.cert_error),
            "headline": "Connection",
            "detail": "Browser pane is still starting…",
            "certificate": "—",
            "cookies": [],
            "cookie_count": 0,
            "is_playing_audio": False,
            "is_muted": False,
            "host": "",
            "scheme": "",
            "cert_error": pane.cert_error,
            "cert_error_status": pane.cert_error_status,
            "cookie_error": "",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "runtime": runtime, "pane_id": pid}

    # Never block the UI thread waiting for cookies — pywebview may call us there.
    if _on_ui_thread(pane.window):
        cookie_err = str(cookie_task_box.get("err") or "") or "cookies skipped on UI thread"
        cookies_out: list[dict[str, Any]] = []
    else:
        cookies_out, cookie_err = _await_cookie_task(cookie_task_box.get("task"))
        if not cookie_err and cookie_task_box.get("err"):
            cookie_err = str(cookie_task_box["err"])
    site["cookies"] = cookies_out
    site["cookie_count"] = len(cookies_out)
    site["cookie_error"] = cookie_err

    return {
        "ok": True,
        "pane_id": pid,
        "site": site,
        "runtime": runtime,
        "protections": runtime.get("protections") or [],
    }


def _ui(window: Any, fn: Any) -> None:
    """Run fn on the form's UI thread without blocking the caller."""
    native = getattr(window, "native", None)
    if native is None:
        return
    try:
        if bool(getattr(native, "InvokeRequired", False)):
            from System import Action

            native.BeginInvoke(Action(fn))
            return
    except Exception:
        pass
    try:
        fn()
    except Exception:
        _log.exception("browser pane UI op failed")


def _ui_sync(window: Any, fn: Any, timeout_s: float = 2.0) -> Any:
    """Run *fn* on the UI thread and return its result (for cookie/cert reads).

    Uses BeginInvoke + wait (never Control.Invoke) so a stuck UI cannot deadlock
    the MCP/plugin caller forever. Timed out calls raise — callers must not retry
    in a tight loop.
    """
    box: dict[str, Any] = {"v": None, "e": None}
    done = threading.Event()

    def wrap() -> None:
        try:
            box["v"] = fn()
        except Exception as exc:
            box["e"] = exc
        finally:
            done.set()

    native = getattr(window, "native", None)
    try:
        if native is not None and bool(getattr(native, "InvokeRequired", False)):
            try:
                from System import Action

                cb: Any = Action(wrap)
            except Exception:
                cb = wrap  # unit tests / no pythonnet
            native.BeginInvoke(cb)
            if not done.wait(float(timeout_s)):
                raise RuntimeError(f"UI sync timed out after {timeout_s}s")
        else:
            wrap()
    except Exception as exc:
        if box["e"] is not None:
            raise box["e"]
        raise RuntimeError(f"UI sync failed: {exc}") from exc
    if box["e"] is not None:
        raise box["e"]
    return box["v"]


def _pane_controls() -> set[Any]:
    with _lock:
        return {p.control for p in _panes.values() if p.control is not None}


def _host_webview(form: Any, own_control: Any) -> Any:
    """The app's own WebView2 control — the calibration reference for CSS→WinForms."""
    host = getattr(form, "webview", None)
    if host is not None and host is not own_control:
        return host
    pane_controls = _pane_controls()
    try:
        best = None
        best_area = 0
        for ctrl in form.Controls:
            if ctrl is own_control or ctrl in pane_controls:
                continue
            if type(ctrl).__name__ == "WebView2":
                area = int(ctrl.ClientSize.Width) * int(ctrl.ClientSize.Height)
                if area > best_area:
                    best_area = area
                    best = ctrl
        return best
    except Exception:
        pass
    return None


def _connection_kind(url: str, cert_error: str = "") -> str:
    u = (url or "").strip().lower()
    if cert_error:
        return "cert_error"
    if u.startswith("https://"):
        return "secure"
    if u.startswith("http://"):
        return "not_secure"
    if u.startswith("about:"):
        return "neutral"
    return "neutral"


def _state_dict(pane: _Pane) -> dict[str, Any]:
    kind = _connection_kind(pane.url, pane.cert_error)
    return {
        "type": "browser_pane_state",
        "pane_id": pane.pane_id,
        "url": pane.url,
        "title": pane.title,
        "can_back": pane.can_back,
        "can_forward": pane.can_forward,
        "loading": pane.loading,
        "ready": pane.ready,
        "failed": pane.failed,
        "connection": kind,
        "cert_error": pane.cert_error,
    }


def _push_state(pane: _Pane) -> None:
    import json

    from frontend.ui_web.ui_dispatch import schedule_evaluate_js

    try:
        payload = json.dumps(_state_dict(pane), ensure_ascii=False)
        schedule_evaluate_js(
            pane.window, f"window.__uefnPanelPush && window.__uefnPanelPush({payload})"
        )
    except Exception:
        pass


def _block_self_embed(pane: _Pane, url: str) -> bool:
    if not is_app_ui_url(url):
        return False
    pane.failed = _SELF_EMBED_MSG
    _push_state(pane)
    return True


def _apply_bounds(pane: _Pane) -> None:
    """UI thread: pin the control to its CSS rect.

    Scale = host WebView2 control size ÷ page CSS viewport — exact by construction
    in every DPI mode (physical vs virtualized WinForms units) and at any page zoom,
    because both describe the same rendered surface. Never trust devicePixelRatio.
    """
    control = pane.control
    pane.apply_queued = False
    if control is None:
        return
    try:
        from System.Drawing import Point, Size
        from System.Windows.Forms import AnchorStyles

        form = pane.window.native
        host = _host_webview(form, control)
        x, y, w, h = pane.css_bounds
        vw, vh = pane.css_viewport
        if host is not None and vw > 1 and vh > 1:
            sx = host.ClientSize.Width / vw
            sy = host.ClientSize.Height / vh
            sx, sy = sane_scale(sx, sy)
            ox, oy = int(host.Left), int(host.Top)
        else:
            sx = sy = 1.0
            ox = oy = 0
        # Bounds are HOST-OWNED, set absolutely every push. Never anchor: WinForms
        # anchoring re-baselines from whatever bounds a mid-resize (stale-scale)
        # apply set, compounding drift the DOM pushes then can't correct.
        control.Anchor = AnchorStyles.Top | AnchorStyles.Left
        control.Location = Point(int(round(ox + x * sx)), int(round(oy + y * sy)))
        control.Size = Size(max(int(round(w * sx)), 1), max(int(round(h * sy)), 1))
        control.Visible = bool(pane.visible and w > 2 and h > 2)
        if control.Visible:
            try:
                # Sibling of the app WebView2 — must stay above it or content is black.
                form.Controls.SetChildIndex(control, 0)
            except Exception:
                pass
            control.BringToFront()
            try:
                control.Focus()
            except Exception:
                pass
    except Exception:
        _log.exception("browser pane bounds failed")


def _try_shared_env() -> Any:
    """Return the process-wide WebView2 Environment once any pane has one."""
    global _shared_env
    if _shared_env is not None:
        return _shared_env
    with _lock:
        for p in _panes.values():
            if not p.ready or p.control is None:
                continue
            try:
                env = p.control.CoreWebView2.Environment
            except Exception:
                continue
            if env is not None:
                _shared_env = env
                return env
    return None


def _queue_for_env(pane: _Pane) -> None:
    if pane not in _pending_env_panes:
        _pending_env_panes.append(pane)
    _log.info(
        "browser pane %s queued until shared WebView2 env / ensure slot is free",
        pane.pane_id,
    )


def _profile_folder_busy(except_pane: _Pane) -> bool:
    """True if another control already holds (or is opening) the shared profile."""
    return any(p is not except_pane and p.control is not None for p in _panes.values())


def _drain_pending_env_panes() -> None:
    """UI thread: start at most one queued pane (Ensure is serialized)."""
    if _ensure_inflight:
        return
    nxt: _Pane | None = None
    with _lock:
        while _pending_env_panes:
            cand = _pending_env_panes.pop(0)
            if cand.control is not None:
                continue
            if cand.pane_id not in _panes:
                continue
            nxt = cand
            break
    if nxt is None:
        return
    try:
        _create_control(nxt)
    except Exception:
        _log.exception("queued browser pane create failed: %s", nxt.pane_id)


def _finish_ensure() -> None:
    """Clear the Ensure slot and start the next queued pane."""
    global _ensure_inflight
    _ensure_inflight = False
    _drain_pending_env_panes()


def _create_control(pane: _Pane) -> None:
    """UI thread: build the WebView2 control and wire CoreWebView2 events."""
    global _shared_env, _env_bootstrap_started, _ensure_inflight
    if pane.control is not None:
        return

    env = _try_shared_env()

    with _lock:
        # Serialize every EnsureCoreWebView2Async — overlapping boots freeze WinForms
        # even when the Environment is already shared.
        if _ensure_inflight:
            _queue_for_env(pane)
            return
        if env is None:
            # Never open UserDataFolder a second time via CreationProperties while
            # any sibling control exists (ready OR still initing OR failed-but-alive).
            if _profile_folder_busy(pane) or _env_bootstrap_started:
                _queue_for_env(pane)
                return
            _env_bootstrap_started = True

    try:
        from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2
        from System.Drawing import Color

        form = pane.window.native
        control = WebView2()
        if env is None:
            # First pane only — later panes must reuse _shared_env.
            props = CoreWebView2CreationProperties()
            props.UserDataFolder = user_data_dir()
            # CDP off by default — always-on remote debugging makes Cloudflare Turnstile loop.
            args = additional_browser_arguments()
            if args:
                try:
                    props.AdditionalBrowserArguments = args
                except Exception:
                    pass
            control.CreationProperties = props
        try:
            control.DefaultBackgroundColor = Color.FromArgb(255, 16, 16, 16)
        except Exception:
            pass
        pane.control = control
        _ensure_inflight = True
        form.Controls.Add(control)
        _apply_bounds(pane)

        def on_source_changed(sender: Any, args: Any) -> None:
            try:
                pane.url = str(control.CoreWebView2.Source or "")
            except Exception:
                pass
            _push_state(pane)

        def on_title_changed(sender: Any, args: Any) -> None:
            try:
                pane.title = str(control.CoreWebView2.DocumentTitle or "")
            except Exception:
                pass
            _push_state(pane)

        def on_history_changed(sender: Any, args: Any) -> None:
            try:
                core = control.CoreWebView2
                pane.can_back = bool(core.CanGoBack)
                pane.can_forward = bool(core.CanGoForward)
            except Exception:
                pass
            _push_state(pane)

        def on_nav_starting(sender: Any, args: Any) -> None:
            try:
                uri = str(args.Uri or "")
                if is_app_ui_url(uri):
                    args.Cancel = True
                    pane.failed = _SELF_EMBED_MSG
                    _push_state(pane)
                    return
                if not _is_allowed_url(uri):
                    args.Cancel = True
                    return
            except Exception:
                pass
            pane.failed = ""
            pane.cert_error = ""
            pane.cert_error_status = ""
            pane.loading = True
            _push_state(pane)

        def on_cert_error(sender: Any, args: Any) -> None:
            # Match Chrome: do not silently trust bad certs. Surface the error.
            try:
                status = str(getattr(args, "ErrorStatus", "") or "")
                uri = str(getattr(args, "RequestUri", "") or getattr(args, "Uri", "") or "")
                pane.cert_error = f"Certificate error on {uri or pane.url}".strip()
                pane.cert_error_status = status
                pane.failed = pane.cert_error
                try:
                    # Cancel = block the request (safe default, like Chrome warning).
                    from Microsoft.Web.WebView2.Core import CoreWebView2ServerCertificateErrorAction

                    args.Action = CoreWebView2ServerCertificateErrorAction.Cancel
                except Exception:
                    pass
            except Exception:
                pane.cert_error = "Certificate error"
            _push_state(pane)

        def on_nav_completed(sender: Any, args: Any) -> None:
            pane.loading = False
            _push_state(pane)

        def on_new_window(sender: Any, args: Any) -> None:
            # Real-browser behavior: popups / target=_blank / middle-click open a NEW
            # app tab. The hosting React bundle listens for this and opens an
            # instanced plugin tab whose pane starts at the requested URL.
            try:
                args.Handled = True
                uri = str(args.Uri or "")
                if not _is_allowed_url(uri):
                    return
                import json

                from frontend.ui_web.ui_dispatch import schedule_evaluate_js

                payload = json.dumps(
                    {"type": "browser_pane_new_window", "pane_id": pane.pane_id, "url": uri},
                    ensure_ascii=False,
                )
                schedule_evaluate_js(
                    pane.window,
                    f"window.__uefnPanelPush && window.__uefnPanelPush({payload})",
                )
            except Exception:
                pass

        def on_init(sender: Any, args: Any) -> None:
            global _shared_env, _env_bootstrap_started
            try:
                if not bool(args.IsSuccess):
                    pane.failed = "WebView2 runtime failed to initialize"
                    try:
                        form.Controls.Remove(control)
                    except Exception:
                        pass
                    try:
                        control.Dispose()
                    except Exception:
                        pass
                    pane.control = None
                    # Bootstrap failed — allow a later pane to retry profile open.
                    if _shared_env is None:
                        _env_bootstrap_started = False
                    _push_state(pane)
                    return
                core = control.CoreWebView2
                try:
                    if _shared_env is None:
                        _shared_env = core.Environment
                except Exception:
                    pass
                try:
                    core.Settings.AreHostObjectsAllowed = False
                    core.Settings.IsWebMessageEnabled = False
                except Exception:
                    pass
                core.SourceChanged += on_source_changed
                core.DocumentTitleChanged += on_title_changed
                core.HistoryChanged += on_history_changed
                core.NavigationStarting += on_nav_starting
                core.NavigationCompleted += on_nav_completed
                core.NewWindowRequested += on_new_window
                try:
                    core.ServerCertificateErrorDetected += on_cert_error
                except Exception:
                    pass
                pane.ready = True
                pane.failed = ""
                target = pane.pending_url
                pane.pending_url = ""
                if target:
                    if _block_self_embed(pane, target):
                        return
                    core.Navigate(target)
                _push_state(pane)
            except Exception:
                _log.exception("browser pane init failed")
                pane.failed = "browser pane init failed"
                _push_state(pane)
            finally:
                _finish_ensure()

        pane._handlers.extend(
            [on_source_changed, on_title_changed, on_history_changed, on_nav_starting,
             on_nav_completed, on_new_window, on_cert_error, on_init]
        )
        control.CoreWebView2InitializationCompleted += on_init
        # Shared Environment for tab 2+; None only for CreationProperties bootstrap.
        control.EnsureCoreWebView2Async(env)
    except Exception:
        _log.exception("browser pane create failed")
        pane.failed = "WebView2 control could not be created (runtime missing?)"
        if pane.control is None and _shared_env is None:
            _env_bootstrap_started = False
        _ensure_inflight = False
        _push_state(pane)
        _drain_pending_env_panes()


def open_pane(pane_id: str, url: str = "", wid: str = "") -> dict[str, Any]:
    if sys.platform != "win32":
        return {"ok": False, "error": "browser panes are Windows-only"}
    pid = (pane_id or "").strip()
    if not pid:
        return {"ok": False, "error": "pane_id required"}
    target = normalize_url(url)
    if target and is_app_ui_url(target):
        return {"ok": False, "error": _SELF_EMBED_MSG}
    window = _window_for_wid(wid)
    if window is None:
        return {"ok": False, "error": "no host window"}
    with _lock:
        pane = _panes.get(pid)
        if pane is not None and pane.window is not window:
            # Tab moved to another OS window — rebuild the control there.
            _destroy_pane_control(pane)
            pane = None
            _panes.pop(pid, None)
        if pane is None:
            pane = _Pane(pane_id=pid, window=window, pending_url=target)
            _panes[pid] = pane
            _ui(window, lambda: _create_control(pane))
            return {"ok": True, **_state_dict(pane)}
    # Existing pane: empty url = reconnect only (tab remount). Non-empty = go there
    # (popup / explicit open). Never treat "" as "navigate to homepage".
    if target and target != pane.url:
        navigate(pid, target)
    else:
        _push_state(pane)
    return {"ok": True, **_state_dict(pane)}


def set_bounds(
    pane_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    viewport_w: float = 0.0,
    viewport_h: float = 0.0,
    visible: bool = True,
) -> dict[str, Any]:
    pid = (pane_id or "").strip()
    with _lock:
        pane = _panes.get(pid)
    if pane is None:
        return {"ok": False, "error": "pane not open"}
    show = bool(visible)
    was_visible = bool(pane.visible)
    pane.css_bounds = (float(x), float(y), float(width), float(height))
    pane.css_viewport = (float(viewport_w or 0.0), float(viewport_h or 0.0))
    pane.visible = show
    # Showing one tab must bury every other native pane (otherwise URL bar and
    # page content disagree — inactive YouTube sitting on top of active DDG).
    if show:
        _hide_other_panes(pid)
    # Coalesce identical show ticks only. Visibility flips always flush so a hide
    # cannot leave apply_queued stuck and drop the next show (black pane forever).
    if show and was_visible and pane.apply_queued:
        return {"ok": True}
    pane.apply_queued = True
    _ui(pane.window, lambda: _apply_bounds(pane))
    return {"ok": True}


def navigate(pane_id: str, url: str) -> dict[str, Any]:
    with _lock:
        pane = _panes.get((pane_id or "").strip())
    if pane is None:
        return {"ok": False, "error": "pane not open"}
    target = normalize_url(url)
    if not target:
        return {"ok": False, "error": f"not a navigable url: {url!r}"}
    if is_app_ui_url(target):
        pane.failed = _SELF_EMBED_MSG
        _push_state(pane)
        return {"ok": False, "error": _SELF_EMBED_MSG}
    if not pane.ready:
        pane.pending_url = target
        return {"ok": True, "queued": True}

    # Navigating implies the tab wants the pane on-screen (un-hide after cover/scrub).
    pane.visible = True
    if pane.css_bounds[2] > 2 and pane.css_bounds[3] > 2:
        _ui(pane.window, lambda: _apply_bounds(pane))

    def op() -> None:
        try:
            pane.control.CoreWebView2.Navigate(target)
        except Exception:
            _log.warning("navigate failed: %s", target)

    _ui(pane.window, op)
    return {"ok": True}


def command(pane_id: str, cmd: str) -> dict[str, Any]:
    with _lock:
        pane = _panes.get((pane_id or "").strip())
    if pane is None or not pane.ready:
        return {"ok": False, "error": "pane not open"}
    action = (cmd or "").strip().lower()
    if action not in ("back", "forward", "reload", "stop"):
        return {"ok": False, "error": f"unknown command: {cmd!r}"}

    def op() -> None:
        try:
            core = pane.control.CoreWebView2
            if action == "back" and core.CanGoBack:
                core.GoBack()
            elif action == "forward" and core.CanGoForward:
                core.GoForward()
            elif action == "reload":
                core.Reload()
            elif action == "stop":
                core.Stop()
        except Exception:
            _log.warning("browser command failed: %s", action)

    _ui(pane.window, op)
    return {"ok": True}


def get_state(pane_id: str) -> dict[str, Any]:
    with _lock:
        pane = _panes.get((pane_id or "").strip())
    if pane is None:
        return {"ok": False, "error": "pane not open"}
    return {"ok": True, **_state_dict(pane)}


def _destroy_pane_control(pane: _Pane) -> None:
    control = pane.control
    window = pane.window
    if control is None:
        return

    def op() -> None:
        try:
            form = window.native
            form.Controls.Remove(control)
        except Exception:
            pass
        try:
            control.Dispose()
        except Exception:
            pass

    _ui(window, op)
    pane.control = None
    pane.ready = False


def close_pane(pane_id: str) -> dict[str, Any]:
    with _lock:
        pane = _panes.pop((pane_id or "").strip(), None)
    if pane is None:
        return {"ok": True, "closed": False}
    _destroy_pane_control(pane)
    return {"ok": True, "closed": True}


def hide_all_panes() -> dict[str, Any]:
    """Emergency: hide every native browser pane so they cannot steal clicks."""
    with _lock:
        panes = list(_panes.values())
    for pane in panes:
        pane.visible = False
        pane.css_bounds = (0, 0, 0, 0)
        if pane.window is not None:
            _ui(pane.window, lambda p=pane: _apply_bounds(p))
    return {"ok": True, "hidden": len(panes)}


def _await_clear_task(task: Any, timeout_s: float = 8.0) -> str:
    """Wait for ClearBrowsingDataAsync off the UI thread. Empty string = ok."""
    if task is None:
        return "no clear task"
    box: dict[str, Any] = {"e": ""}
    done = threading.Event()

    def worker() -> None:
        try:
            if hasattr(task, "GetAwaiter"):
                task.GetAwaiter().GetResult()
            else:
                _ = getattr(task, "Result", None)
        except Exception as exc:
            box["e"] = str(exc)
        finally:
            done.set()

    threading.Thread(target=worker, name="browser-clear", daemon=True).start()
    if not done.wait(timeout_s):
        return "clear timed out"
    return str(box["e"] or "")


def clear_browsing_data(kinds: str = "all") -> dict[str, Any]:
    """Clear WebView2 profile data under the shared browser UserDataFolder.

    *kinds*: ``all`` | ``cache`` | ``cookies`` | ``history``.

    Prefer Profile.ClearBrowsingDataAsync while a pane is open. Never shutil the
    live UserDataFolder — deleting Cookies/Network while WebView2 holds them
    freezes the WinForms UI (the freeze users hit from MCP browser_clear_data).
    """
    import shutil

    kind = (kinds or "all").strip().lower()
    if kind not in ("all", "cache", "cookies", "history"):
        return {"ok": False, "error": f"unknown kinds: {kinds!r}"}

    flags = {
        "all": 4095,  # AllProfile
        "cache": 64 | 16,  # DiskCache | CacheStorage
        "cookies": 32,  # Cookies
        "history": 1024 | 128,  # BrowsingHistory | DownloadHistory
    }.get(kind, 4095)

    with _lock:
        ready = [p for p in _panes.values() if p.ready and p.control is not None]
    if ready:
        pane = ready[0]
        task_box: dict[str, Any] = {"task": None, "err": ""}

        def start() -> None:
            try:
                core = pane.control.CoreWebView2
                profile = getattr(core, "Profile", None)
                if profile is None:
                    task_box["err"] = "no Profile"
                    return
                clearer = getattr(profile, "ClearBrowsingDataAsync", None)
                if clearer is None:
                    task_box["err"] = "ClearBrowsingDataAsync unavailable"
                    return
                task_box["task"] = clearer(flags)
            except Exception as exc:
                task_box["err"] = str(exc)

        try:
            _ui_sync(pane.window, start, timeout_s=2.0)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "kinds": kind, "via": "webview2"}
        if task_box["err"]:
            return {"ok": False, "error": task_box["err"], "kinds": kind, "via": "webview2"}
        err = _await_clear_task(task_box.get("task"))
        if err:
            return {"ok": False, "error": err, "kinds": kind, "via": "webview2"}
        return {"ok": True, "via": "webview2", "kinds": kind}

    # No ready pane — only wipe on disk when nothing holds the profile.
    if profile_locked():
        return {
            "ok": False,
            "error": "close all Browser tabs first (profile still locked) — disk wipe while WebView2 runs freezes the app",
            "kinds": kind,
        }

    ud = Path(user_data_dir()) / "EBWebView"
    removed: list[str] = []
    errors: list[str] = []

    def _rm(path: Path) -> None:
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=False)
                removed.append(str(path))
            elif path.is_file():
                path.unlink(missing_ok=True)
                removed.append(str(path))
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    if not ud.is_dir():
        return {"ok": True, "via": "disk", "kinds": kind, "removed": removed, "errors": errors}

    for profile in ud.iterdir():
        if not profile.is_dir() or profile.name.startswith("."):
            continue
        if kind in ("all", "cache"):
            for name in ("Cache", "Code Cache", "GPUCache", "Service Worker", "GrShaderCache", "ShaderCache"):
                _rm(profile / name)
        if kind in ("all", "cookies"):
            for name in ("Cookies", "Cookies-journal", "Network"):
                _rm(profile / name)
        if kind in ("all", "history"):
            for name in ("History", "History-journal", "Visited Links", "Top Sites", "Top Sites-journal"):
                _rm(profile / name)

    return {
        "ok": len(errors) == 0 or len(removed) > 0,
        "via": "disk",
        "kinds": kind,
        "removed": removed,
        "errors": errors,
    }
