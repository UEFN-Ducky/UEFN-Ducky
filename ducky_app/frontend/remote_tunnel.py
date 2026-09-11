"""Outbound Cloudflare tunnel so the panel UI can be opened in a browser.

cloudflared is downloaded once to AppData/bin. Starts only when the user is
logged in and Settings → Account → Remote access is on (default off).
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from frontend.settings import default_app_data_dir

_CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-windows-amd64.exe"
)
_QUICK_HOST_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)
_NAMED_CACHE_KEY = "uefn-ducky/remote_named_tunnel"
_REGISTERED = "Registered tunnel connection"

_STOP = threading.Event()
_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_STATUS: dict[str, Any] = {
    "running": False,
    "mode": "",
    "hostname": "",
    "error": "",
    "named_reason": "",
    "site_update_pending": False,
}
_LOG_MAX = 1_000_000


def remote_tunnel_status() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATUS)


def _load_named_cache() -> dict[str, str]:
    try:
        from backend.agent.secrets import get_key

        raw = get_key(_NAMED_CACHE_KEY)
        data = json.loads(raw) if raw else {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    host = str(data.get("hostname") or "").strip()
    token = str(data.get("token") or "").strip()
    if not host or not token:
        return {}
    return {"hostname": host, "token": token, "mode": "named"}


def _save_named_cache(hostname: str, token: str) -> None:
    host = (hostname or "").strip()
    tok = (token or "").strip()
    if not host or not tok:
        return
    try:
        from backend.agent.secrets import set_key

        set_key(_NAMED_CACHE_KEY, json.dumps({"hostname": host, "token": tok}))
    except Exception:
        pass


def _bin_path() -> Path:
    return default_app_data_dir() / "bin" / "cloudflared.exe"


def ensure_cloudflared() -> Path:
    dest = _bin_path()
    if dest.is_file() and dest.stat().st_size > 1_000_000:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    req = urllib.request.Request(_CLOUDFLARED_URL, headers={"User-Agent": "UEFN-Ducky"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as fh:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            fh.write(chunk)
    os.replace(tmp, dest)
    return dest


def _set_status(**kwargs: Any) -> None:
    with _LOCK:
        _STATUS.update(kwargs)


def _cloudflared_log_path() -> Path:
    return default_app_data_dir() / "diagnostics" / "cloudflared.log"


def _append_cloudflared_log(text: str) -> None:
    if not text:
        return
    path = _cloudflared_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > _LOG_MAX:
            path.write_text("", encoding="utf-8")
        with path.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass


def _fetch_tunnel_token() -> dict[str, Any]:
    from frontend.duckyos_account import DuckyOSAccountError, _plugin_collect

    try:
        row = _plugin_collect(
            "uefn-ducky",
            "desktop-remote-tunnel",
            {"action": "ensure"},
            unavailable_code="rpc_unavailable",
            unavailable_msg="Remote plugin is not active on this tenant yet.",
            error_code="remote_tunnel_failed",
            timeout=20.0,
        )
    except DuckyOSAccountError as exc:
        code = str(getattr(exc, "code", "") or "")
        msg = str(getattr(exc, "message", "") or exc)
        if code in ("rpc_unavailable",) or re.search(
            r"not allowed|unknown|404|not active", msg, re.I
        ):
            return {"mode": "pending"}
        raise
    return row if isinstance(row, dict) else {}


def _remove_tunnel() -> None:
    try:
        from frontend.duckyos_account import _plugin_collect

        _plugin_collect(
            "uefn-ducky",
            "desktop-remote-tunnel",
            {"action": "remove"},
            unavailable_code="rpc_unavailable",
            unavailable_msg="Remote plugin is not active on this tenant yet.",
            error_code="remote_tunnel_failed",
            timeout=20.0,
        )
    except Exception:
        pass


def _run_cloudflared(exe: Path, args: list[str], named_host: str = "") -> str:
    """Run until stop or exit. Returns last hostname seen on stderr."""
    hostname = ""
    tail: list[str] = []
    last_named_check = time.monotonic()
    proc = subprocess.Popen(
        [str(exe), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert proc.stdout is not None
        lines: queue.Queue[str | None] = queue.Queue()

        def _reader() -> None:
            try:
                for raw in proc.stdout:
                    lines.put(raw)
            finally:
                lines.put(None)

        threading.Thread(target=_reader, daemon=True, name="ducky-cloudflared-out").start()
        while not _STOP.is_set():
            try:
                line = lines.get(timeout=1.0)
            except queue.Empty:
                line = ""
            if line is None:
                break
            text = (line or "").strip()
            if text:
                tail.append(text)
                if len(tail) > 8:
                    tail.pop(0)
                _append_cloudflared_log(text)
            match = _QUICK_HOST_RE.search(line or "")
            if match:
                hostname = match.group(0).removeprefix("https://")
                _set_status(hostname=hostname, running=True)
            if named_host and _REGISTERED in text:
                hostname = named_host
                _set_status(hostname=named_host, running=True, error="")
            if (
                time.monotonic() - last_named_check > 15
                and str(remote_tunnel_status().get("mode") or "") == "quick"
            ):
                last_named_check = time.monotonic()
                try:
                    row = _fetch_tunnel_token()
                    reason = str(row.get("reason") or "").strip()
                    if reason:
                        _set_status(named_reason=reason[:240])
                    if str(row.get("mode") or "") == "named" and row.get("token"):
                        proc.terminate()
                        break
                except Exception:
                    pass
        if _STOP.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        if proc.poll() is None:
            proc.kill()
    if hostname:
        return hostname
    err = (tail[-1][:240] if tail else "tunnel exited")
    _set_status(running=False, hostname="", error=err)
    return ""


def _loop() -> None:
    from frontend.settings import PanelSettings
    from frontend.ui_web.panel_httpd import PANEL_UI_HTTP_PORT

    while not _STOP.is_set():
        try:
            s = PanelSettings.load()
            if not bool(getattr(s, "remote_access", False)):
                _set_status(running=False, mode="", error="", named_reason="")
                _STOP.wait(2.0)
                continue
            if not str(remote_tunnel_status().get("hostname") or "").strip():
                _set_status(mode="starting", running=False, error="")
            exe = ensure_cloudflared()
            _kill_orphan_cloudflareds()
            try:
                row = _fetch_tunnel_token()
            except Exception as exc:
                row = _load_named_cache()
                if not row.get("token"):
                    _set_status(running=False, error=str(exc)[:240])
                    raise
            mode = str(row.get("mode") or "")
            reason = str(row.get("reason") or "").strip()
            if reason:
                _set_status(named_reason=reason[:240])
            if mode == "pending":
                _set_status(
                    running=False,
                    mode="",
                    site_update_pending=True,
                    error="Site update pending",
                )
                _STOP.wait(15.0)
                continue
            if mode != "named" or not row.get("token"):
                cached = _load_named_cache()
                if cached.get("token"):
                    row = cached
                    mode = "named"
            _set_status(site_update_pending=False)
            url = f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}"
            if mode == "named" and row.get("token"):
                host = str(row.get("hostname") or "")
                _save_named_cache(host, str(row["token"]))
                # Host stays empty until cloudflared registers — iframe-ing early is 530/504.
                _set_status(mode="named", hostname="", running=False, error="", named_reason="")
                _run_cloudflared(
                    exe,
                    [
                        "tunnel",
                        "--no-autoupdate",
                        "--proxy-keepalive-connections",
                        "8",
                        "--no-chunked-encoding",
                        "run",
                        "--token",
                        str(row["token"]),
                    ],
                    named_host=host,
                )
            else:
                _set_status(mode="quick", running=False)
                _run_cloudflared(
                    exe,
                    [
                        "tunnel",
                        "--no-autoupdate",
                        "--proxy-keepalive-connections",
                        "8",
                        "--no-chunked-encoding",
                        "--url",
                        url,
                    ],
                )
        except Exception as exc:
            _set_status(running=False, error=str(exc)[:240])
        if not _STOP.is_set():
            _STOP.wait(3.0)
    _set_status(running=False)


def _kill_orphan_cloudflareds() -> None:
    """FORCECLOSE / crashed panels leave cloudflared on :4199 — 502s and trycloudflare spam."""
    if os.name != "nt":
        return
    marker = str(_bin_path()).replace("'", "''")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=15,
        )
    except Exception:
        pass


def start_remote_tunnel() -> None:
    global _THREAD
    from frontend.settings import PanelSettings
    from frontend.duckyos_account import _load_blob

    s = PanelSettings.load()
    if not bool(getattr(s, "remote_access", False)):
        return
    blob = _load_blob()
    if not (blob.get("device_key") or blob.get("session_value")):
        return
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, daemon=True, name="ducky-remote-tunnel")
        _THREAD.start()


def stop_remote_tunnel(*, deprovision: bool = False) -> None:
    _STOP.set()
    if deprovision:
        _remove_tunnel()
    _set_status(running=False)
    with _LOCK:
        thread = _THREAD
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=4.0)
