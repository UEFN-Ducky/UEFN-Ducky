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


# --------------------------------------------------------------------------- remote UEFN
# ``--remote-uefn-probe <outdir>`` (a Mac runner): sign in from DUCKY_TEST_ACCOUNT_TOKEN, held
# in memory only, and have the account's online PC run ``uefn_ci_check`` through the Ducky
# link: ping, a Verse build and a viewport screenshot. remote.json + remote-screenshot.png.

REMOTE_CHECK = "uefn_ci_check"
TOKEN_ENV = "DUCKY_TEST_ACCOUNT_TOKEN"
_SHOT_MAX = 20_000  # bytes of PNG: sealed, the answer must stay under ducky_link.SEALED_MAX
_TEXT_MAX = 2_000


class ProbeError(RuntimeError):
    pass


def on_ci_runner() -> bool:
    """A GitHub runner with the test-copy profile (the workflows set both)."""
    import os

    return os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("DUCKY_SANDBOX") == "1"


def _step(fn: Any) -> dict[str, Any]:
    try:
        got = fn()
    except Exception as exc:  # noqa: BLE001 — each step's failure is its result
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:_TEXT_MAX]}
    if isinstance(got, str):
        try:
            got = json.loads(got)
        except ValueError:
            pass
    bad = isinstance(got, dict) and (got.get("ok") is False or bool(got.get("error")))
    out: dict[str, Any] = {"ok": not bad, "result": got}
    if len(json.dumps(got, default=str)) > _TEXT_MAX:
        out["result"] = json.dumps(got, default=str)[:_TEXT_MAX]
    return out


def _small_png(raw: bytes) -> tuple[bytes, str]:
    """The capture shrunk until it fits _SHOT_MAX (a palette PNG, halving the width)."""
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    width = min(img.width, 640)
    while True:
        small = img.resize((width, max(1, round(img.height * width / img.width))))
        buf = io.BytesIO()
        small.quantize(colors=64).save(buf, format="PNG", optimize=True)
        if buf.tell() <= _SHOT_MAX or width <= 80:
            return buf.getvalue(), f"{small.width}x{small.height}"
        width //= 2


def _screenshot() -> dict[str, Any]:
    import base64

    from backend.bridge import send_command
    from backend.tools.uefn.editor import _SCREENSHOT_BRIDGE_TIMEOUT_SEC, _wait_for_screenshot_file

    got = send_command("take_high_res_screenshot", {"width": 1280, "height": 720, "filename": ""},
                       timeout=_SCREENSHOT_BRIDGE_TIMEOUT_SEC)
    if not isinstance(got, dict):
        return {"ok": False, "error": f"no screenshot: {str(got)[:200]}"}
    got = _wait_for_screenshot_file(got)
    raw = base64.b64decode(got["png_base64"]) if got.get("png_base64") else b""
    if not raw and got.get("path") and Path(str(got["path"])).is_file():
        raw = Path(str(got["path"])).read_bytes()
    if not raw:
        return {"ok": False, "error": str(got.get("error") or "the screenshot has no image")}
    png, size = _small_png(raw)
    return {"ok": True, "size": size, "png_b64": base64.b64encode(png).decode("ascii")}


def uefn_check() -> dict[str, Any]:
    """The PC's answer to REMOTE_CHECK: three fixed UEFN checks, no arguments taken."""
    from backend.tools.core.system import ping
    from backend.tools.verse.verse_diagnostics import workspace_compile_verse

    verse = _step(workspace_compile_verse)
    if verse["ok"] and isinstance(verse.get("result"), dict):
        compiled = verse["result"].get("compile") or {}
        verse = {"ok": True, "num_errors": int(compiled.get("numErrors") or 0) if isinstance(compiled, dict) else None}
    try:
        shot = _screenshot()  # not through _step: it would cut the image to _TEXT_MAX
    except Exception as exc:  # noqa: BLE001
        shot = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:_TEXT_MAX]}
    return {"ping": _step(ping), "verse": verse, "screenshot": shot}


def _token() -> dict[str, str]:
    """``{key_id, token, email, base_url?, pc_key?}`` from the env, which is then emptied."""
    import os

    raw = os.environ.pop(TOKEN_ENV, "").strip()
    if not raw:
        raise ProbeError(f"{TOKEN_ENV} is not set")
    try:
        doc = json.loads(raw)
    except ValueError:
        raise ProbeError(f"{TOKEN_ENV} is not the JSON this probe expects") from None
    if not isinstance(doc, dict) or not str(doc.get("token") or "").startswith("dky_v1_") or not doc.get("key_id"):
        raise ProbeError(f"{TOKEN_ENV} needs key_id and a dky_v1_ token")
    return {k: str(v) for k, v in doc.items() if v}


def _sign_in_in_memory(doc: dict[str, str]) -> None:
    """This process's vault becomes a dict: the device key is never written anywhere."""
    from backend.agent import secrets
    from frontend import duckyos_account as acct
    from frontend import remote_trust

    store: dict[str, str] = {}
    secrets.get_key = secrets.get_key_fresh = store.get  # type: ignore[assignment]
    secrets.set_key = lambda k, v: store.__setitem__(k, v)  # type: ignore[assignment]
    secrets.clear_key = lambda k: store.pop(k, None)  # type: ignore[assignment]
    base = acct.normalize_base_url(doc.get("base_url") or acct.resolve_base_url())
    store[acct._CREDENTIALS_KEY] = json.dumps({"base_url": base, "email": doc.get("email", ""),
                                               "device_key": doc["token"], "device_key_id": doc["key_id"]})
    if doc.get("pc_key"):
        store[remote_trust.PC_KEY] = doc["pc_key"]  # the identity the PC approved once
    acct.forget_account_key()


def _drive(link: Any, out: Path, report: dict[str, Any]) -> list[str]:
    import base64

    pcs = [d for d in link.list_duckies().get("devices") or [] if d.get("live") and not d.get("mine")]
    if not pcs:
        raise ProbeError("no online UEFN Ducky PC on the test account")
    pc = pcs[0]
    report["pc"] = {"key_id": pc["keyId"], "name": pc["name"]}
    got = link.call(pc["keyId"], REMOTE_CHECK, {}, timeout=420.0)
    if got.get("pair"):
        # Each run starts with no pin: the first call pins the PC. Once the PC's window
        # approved this runner's key (pc_key in the secret), the second call goes through.
        report["pair"] = str(got.get("error") or "")
        got = link.call(pc["keyId"], REMOTE_CHECK, {}, timeout=420.0)
    if not got.get("ok"):
        raise ProbeError(str(got.get("error") or "the PC did not answer"))
    res = got.get("result") or {}
    shot = dict(res.get("screenshot") or {})
    png = shot.pop("png_b64", "")
    if png:
        (out / "remote-screenshot.png").write_bytes(base64.b64decode(png))
        shot["file"] = "remote-screenshot.png"
    report.update(ping=res.get("ping") or {}, verse=res.get("verse") or {}, screenshot=shot)
    from frontend.window_view import uefn_on_pc

    handoff = uefn_on_pc() or {}
    report["handoff"] = handoff
    failed = [n for n in ("ping", "verse", "screenshot") if not report[n].get("ok")]
    if not png:
        failed.append("screenshot png")
    if {"key_id": pc["keyId"], "name": pc["name"]} not in (handoff.get("open_remote") or []):
        failed.append("handoff")
    else:
        print(f"Open {pc['name']}")  # the Mac chat's hand-off button
    return failed


def remote_uefn(outdir: str, *, link: Any = None) -> int:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"ok": False}
    secret = ""
    try:
        if not on_ci_runner():
            raise ProbeError("--remote-uefn-probe runs only on a CI runner (GITHUB_ACTIONS, DUCKY_SANDBOX=1)")
        doc = _token()
        secret = doc["token"]
        _sign_in_in_memory(doc)
        if link is None:
            from frontend import ducky_link as link
        failed = _drive(link, out, report)
        report["failed"] = failed
        report["ok"] = not failed
    except Exception as exc:  # noqa: BLE001 — the report says why
        report["error"] = f"{type(exc).__name__}: {exc}"
    text = json.dumps(report, indent=2, default=str)
    if secret:
        text = text.replace(secret, "***")
    (out / "remote.json").write_text(text, encoding="utf-8")
    print(f"remote-uefn: {'ok' if report['ok'] else 'FAILED'} {report.get('error') or report.get('failed') or ''}")
    return 0 if report["ok"] else 1
