"""macOS self-update: UEFN Ducky.app swaps itself for the feed's .app.zip (build/macos/package.py).

``updater`` re-exports these names. Shared helpers (download, progress, watchdog start) are
looked up on ``updater`` at call time, so a test that replaces one there replaces it here too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from frontend import updater as _u

# The AppImage watchdog for UEFN Ducky.app: the app is a folder, so it starts the executable
# inside it and a rollback removes the new folder before moving <app>.prev back.
MAC_WATCHDOG_SH = r"""
app=$1 prev=$2 ok=$3 failed=$4 timeout=$5 old=$6 new=$7 pid=$8 exe=$9
"$app/Contents/MacOS/$exe" --after-update "$pid" &
child=$!
i=0
while [ "$i" -lt "$timeout" ]; do
  [ -e "$ok" ] && exit 0
  kill -0 "$child" 2>/dev/null || break
  sleep 1
  i=$((i+1))
done
[ -e "$ok" ] && exit 0
kill "$child" 2>/dev/null
[ -d "$prev" ] || exit 1
rm -rf "$app" && mv "$prev" "$app" || exit 1
printf '{"from": "%s", "to": "%s", "at": %s}' "$new" "$old" "$(date +%s)" > "$failed"
exec "$app/Contents/MacOS/$exe"
"""
_OPEN_THE_DMG = "This copy can't replace itself here — open the new DMG from {url} and drag UEFN Ducky to Applications."


def mac_app_bundle(exe: str | Path | None = None) -> Path | None:
    """The ``.app`` the running executable lives in (``X.app/Contents/MacOS/<exe>``)."""
    path = Path(exe or sys.executable).resolve()
    app = path.parent.parent.parent
    return app if path.parent.name == "MacOS" and app.suffix == ".app" else None


def _mac_run(cmd: list[str]) -> Any:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=300)


def mac_signature_kind(app: Path, run: Any = None) -> str:
    """``developer-id`` when ``app`` is Developer ID signed, else ``adhoc`` (today's builds)."""
    done = (run or _mac_run)(["codesign", "-dv", "--verbose=2", str(app)])
    text = f"{getattr(done, 'stdout', '') or ''}{getattr(done, 'stderr', '') or ''}"
    return "developer-id" if "Authority=Developer ID Application" in text else "adhoc"


def verify_mac_app(new: Path, *, current_kind: str, run: Any = None) -> str | None:
    """``codesign --verify --strict`` passes for ad-hoc and Developer ID alike; a Developer
    ID app only takes an update Gatekeeper accepts too (``spctl -a``)."""
    run = run or _mac_run
    if getattr(run(["codesign", "--verify", "--strict", str(new)]), "returncode", 1) != 0:
        return "The update's code signature doesn't verify — refusing to install it."
    if current_kind == "developer-id" and getattr(
            run(["spctl", "-a", "-t", "exec", "-vv", str(new)]), "returncode", 1) != 0:
        return "macOS Gatekeeper rejected the update — refusing to install it."
    return None


def install_mac_app(app: Path, archive: Path, *, sha256: str, version: str, run: Any = None) -> str | None:
    """Check ``archive``'s sha256, unpack it next to ``app``, verify the signature and swap:
    the old app stays as ``<app>.prev``. On any error ``app`` is untouched."""
    from frontend import app_versions

    run = run or _mac_run
    stage = app.with_name(f".{app.name}.new")
    shutil.rmtree(stage, ignore_errors=True)
    try:
        error = _u._verify_sha256(archive, sha256)
        if not error and getattr(run(["ditto", "-x", "-k", str(archive), str(stage)]), "returncode", 1) != 0:
            error = "The update couldn't be unpacked."
        new = stage / app.name
        if not error and not (new / "Contents" / "Info.plist").is_file():
            error = "The download isn't a UEFN Ducky app."
        kind = mac_signature_kind(app, run)
        if not error:
            error = verify_mac_app(new, current_kind=kind, run=run)
        if error:
            return error
        previous = app.with_name(app.name + ".prev")
        shutil.rmtree(previous, ignore_errors=True)
        try:
            os.replace(app, previous)  # a running .app can be renamed; it keeps running
            os.replace(new, app)
        except OSError as exc:
            if previous.is_dir() and not app.exists():
                os.replace(previous, app)
            return f"Couldn't replace the app: {exc}"
    finally:
        _u._unlink_quiet(archive)
        shutil.rmtree(stage, ignore_errors=True)
    print(f"update {_u.__version__} → {version}: verified ({kind}), swapped")
    app_versions.begin_appimage_swap(version, from_version=_u.__version__, app=app, previous=previous)
    return None


def apply_mac_update(*, status: dict[str, Any] | None = None, app: Path | None = None) -> dict[str, Any]:
    """'Restart' on UEFN Ducky.app: download the .app.zip, verify, swap, restart under the
    rollback watchdog. An app in a folder this user can't write (/Applications owned by an
    admin) gets the DMG instead (``action: manual``)."""
    result = _u._result
    status = status or _u.linux_update_status(kind="app")
    if status.get("channel") == "dev":
        return result(ok=False, error="Dev builds cannot self-update — only production installs update from the Store.",
                      stage="check")
    if not status["update_available"]:
        return result(ok=False, error=status.get("error") or "No update available.", stage="check")
    asset = status.get("asset") or {}
    url, sha256 = asset.get("url"), asset.get("sha256")
    if not url:
        return result(ok=False, error="The update feed has no app for this Mac.", stage="check")
    if not sha256:
        return result(ok=False, error="Update feed is missing the app's sha256 — refusing to install.", stage="check")
    if not _u.installer_url_allowed(url):
        return result(ok=False, error=f"Refusing non-HTTPS update URL: {url}", stage="check")
    app = app or mac_app_bundle()
    if app is None or not (os.access(app, os.W_OK) and os.access(app.parent, os.W_OK)):
        manual = result(ok=False, error=_OPEN_THE_DMG.format(url=status.get("download_url") or url), stage="check")
        return {**manual, "action": "manual"}
    _u._stop_all_agents()
    archive = app.with_name(f".{app.name}.zip")
    error = _u._download(url, archive)
    if error:
        _u._unlink_quiet(archive)
        return result(ok=False, error=error, stage="download")
    _u._set_progress(stage="verify", error=None)
    version = str(status["remote_version"])
    error = _u.install_mac_app(app, archive, sha256=sha256, version=version)
    if error:
        return result(ok=False, error=error, stage="verify")
    _u._set_progress(stage="restarting", error=None)
    try:
        _u.start_watchdog(app, version=version, previous=app.with_name(app.name + ".prev"),
                          script=MAC_WATCHDOG_SH, extra=(Path(sys.executable).name,))
    except OSError as exc:
        shutil.rmtree(app, ignore_errors=True)
        os.replace(app.with_name(app.name + ".prev"), app)
        return result(ok=False, error=f"Couldn't start the new version: {exc}", stage="launch")
    _u._exit_self_after_delay()
    return result(ok=True, error=None, stage="restarting")
