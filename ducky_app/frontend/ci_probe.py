"""Checks the Mac CI runs from the packaged app (so macOS privacy is the .app's own).

``--tcc-probe out.json``: 1 s of mic through dictation's capture, one screen grab through the
snip/stream grabber and one osascript Apple Event. Each one reports what it got, or the
"System Settings > Privacy & Security" message when macOS refused it.
``--base-url-probe out.txt``: the Store URL this profile would send to (account and usage).
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

MIC_NOTE = ("UEFN Ducky needs the Microphone permission for dictation. "
            "Turn it on in System Settings > Privacy & Security > Microphone, then try again.")
AUTOMATION_NOTE = ("UEFN Ducky needs the Automation permission to control other apps. "
                   "Turn it on in System Settings > Privacy & Security > Automation, then try again.")
_APPLE_EVENT = 'tell application "System Events" to get name of first application process whose frontmost is true'


def osascript_result(code: int, out: str, err: str) -> dict[str, Any]:
    """-1743 is macOS refusing the Apple Event (Automation off for this app)."""
    if code == 0:
        return {"ok": True, "result": out.strip()}
    if "-1743" in err or "not allowed to send" in err.lower() or "not authorized" in err.lower():
        return {"ok": False, "message": AUTOMATION_NOTE}
    return {"ok": False, "error": (err or out).strip()[:300] or f"osascript exit {code}"}


def _osascript() -> dict[str, Any]:
    try:
        run = subprocess.run(["osascript", "-e", _APPLE_EVENT], capture_output=True, text=True, errors="replace", timeout=20)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "osascript waited 20 s (an unanswered Automation prompt)"}
    return osascript_result(run.returncode, run.stdout, run.stderr)


def _mic(seconds: float = 1.0) -> dict[str, Any]:
    from backend.voice import linux_speech
    from frontend.boot_report import mac_permissions

    if mac_permissions().get("mic") == "denied":
        return {"ok": False, "message": MIC_NOTE}
    started = linux_speech.start_session()
    if not started.get("ok"):
        return {"ok": False, "error": started.get("error")}
    sid = started["session"]
    time.sleep(seconds)
    pcm = b"".join(linux_speech._sessions.get(sid, {}).get("chunks", []))
    linux_speech.cancel_session(sid)
    return {"ok": True, "seconds": round(len(pcm) / (2 * linux_speech._RATE), 2)}


def _snip() -> dict[str, Any]:
    from frontend import native_capture as nc

    # ponytail: the grabber, not the snip's `screencapture -i` picker (needs a drag) or its
    # Open System Settings alert (modal): the same Screen Recording check and message.
    if not nc.mac_screen_access():
        return {"ok": False, "message": nc.SCREEN_RECORDING_NOTE}
    import Quartz  # type: ignore[import-not-found]

    bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    w, h = int(bounds.size.width), int(bounds.size.height)
    grabber = nc.MacGrabber()
    try:
        data = grabber.grab(0, 0, w, h)
    finally:
        grabber.close()
    if not any(data[i] for i in range(0, len(data), 4097)):
        return {"ok": False, "error": f"{w}x{h} grab is all black (Screen Recording refused without a prompt?)"}
    return {"ok": True, "size": f"{w}x{h}", "bytes": len(data)}


def tcc(output: str) -> int:
    report: dict[str, Any] = {}
    for name, probe in (("mic", _mic), ("snip", _snip), ("osascript", _osascript)):
        try:
            report[name] = probe()
        except Exception as exc:  # noqa: BLE001 — one probe's crash is its result, the others still run
            report[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


def base_url(output: str) -> int:
    from frontend.duckyos_account import resolve_base_url

    Path(output).write_text(resolve_base_url() + "\n", encoding="utf-8")
    return 0
