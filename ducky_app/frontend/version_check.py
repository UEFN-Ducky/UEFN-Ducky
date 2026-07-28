"""Remote version check against the DuckyOS Store (uefn-ducky-store collect/app-version)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin

from frontend import __version__
from frontend.bundle_root import is_packaged_runtime

PATREON_URL = "https://www.patreon.com/UEFNDucky"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def version_check_url() -> str:
    from frontend.duckyos_account import resolve_base_url

    return f"{resolve_base_url().rstrip('/')}/api/plugins/uefn-ducky-store/collect/app-version"


def download_page_url() -> str:
    """Portable-install / crash-recovery fallback: open the Store download page."""
    from frontend.duckyos_account import resolve_base_url

    return resolve_base_url().rstrip("/") + "/download"


# Back-compat alias for panel_api / older imports (resolved at call time via download_page_url).
DOWNLOAD_URL = "https://uefnducky.org/download"


def parse_version_tuple(version: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match(str(version or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_remote_newer(local: str, remote: str) -> bool:
    local_t = parse_version_tuple(local)
    remote_t = parse_version_tuple(remote)
    if local_t is None or remote_t is None:
        return False
    return remote_t > local_t


def _extract_remote_version(payload: dict[str, Any]) -> str | None:
    for key in ("currentVersion", "current_version", "version"):
        raw = payload.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def _extract_str(payload: dict[str, Any], key: str) -> str | None:
    raw = payload.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def unwrap_collect_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract the app-version object from a Store collect envelope."""
    if not isinstance(raw, dict):
        return {}
    payload = raw.get("payload")
    if isinstance(payload, dict):
        # Nested: { handled, payload: { currentVersion, … } } or { ok, payload: … }
        inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else None
        if inner is not None and ("currentVersion" in inner or "installerUrl" in inner):
            return inner
        if "currentVersion" in payload or "installerUrl" in payload or "version" in payload:
            return payload
    if "currentVersion" in raw or "installerUrl" in raw or "version" in raw:
        return raw
    return payload if isinstance(payload, dict) else raw


def absolute_installer_url(url: str | None, *, base_url: str) -> str | None:
    if not url:
        return None
    text = str(url).strip()
    if not text:
        return None
    if text.lower().startswith("https://") or text.lower().startswith("http://"):
        return text
    return urljoin(base_url.rstrip("/") + "/", text.lstrip("/"))


def fetch_remote_payload(*, timeout: float = 8.0) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(payload, error)`` for the Store app-version collect endpoint."""
    from frontend.duckyos_account import resolve_base_url

    base = resolve_base_url().rstrip("/")
    url = f"{base}/api/plugins/uefn-ducky-store/collect/app-version"
    # Collect endpoints require Origin to match Host (same as duckyos_account.api_request).
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": base,
            "User-Agent": f"UEFN-Ducky/{__version__}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return None, str(exc)

    try:
        envelope = json.loads(body)
    except (TypeError, ValueError) as exc:
        return None, f"Invalid JSON: {exc}"

    if not isinstance(envelope, dict):
        return None, "Invalid JSON: expected object"
    payload = unwrap_collect_payload(envelope)
    if not isinstance(payload, dict):
        return None, "Invalid collect payload"
    return payload, None


def update_channel(*, installed: bool) -> str:
    """
    Which update path this process is on.

    Store self-update is **installed production only**. Dev EXEs never get
    Store builds (we only publish Setup). Portable / source runs also skip
    in-app update — reinstall via Setup if you want updates.
    """
    from frontend.ui_web.web_dev import is_frozen_dev_exe

    if is_frozen_dev_exe():
        return "dev"
    if installed:
        return "installed"
    return "portable"


def get_app_update_status() -> dict[str, Any]:
    """
    One payload for the panel UI and the updater: remote version compare plus
    installed-vs-portable state. Read-only.
    """
    from frontend.duckyos_account import resolve_base_url
    from frontend.install_info import get_install_info

    install = get_install_info()
    channel = update_channel(installed=bool(install["installed"]))
    download = download_page_url()
    result: dict[str, Any] = {
        "local_version": __version__,
        "remote_version": None,
        "update_available": False,
        "installed": install["installed"],
        "channel": channel,
        "install_location": install["install_location"],
        "install_scope": install["install_scope"],
        "installer_url": None,
        "installer_sha256": None,
        "release_notes": None,
        "download_url": download,
        # none | no_release | up_to_date | update_available | error
        "feed_status": "none",
        "error": None,
    }

    # Only packaged Inno installs poll the Store for self-update.
    if channel != "installed" or not is_packaged_runtime():
        return result

    payload, error = fetch_remote_payload()
    if error or payload is None:
        result["feed_status"] = "error"
        result["error"] = error or "Empty version-check response"
        return result

    remote = _extract_remote_version(payload)
    if not remote:
        # Feed is up; nothing published yet via uds_app_release.
        result["feed_status"] = "no_release"
        return result

    result["remote_version"] = remote
    if is_remote_newer(__version__, remote):
        result["feed_status"] = "update_available"
        result["update_available"] = True
        result["installer_url"] = absolute_installer_url(
            _extract_str(payload, "installerUrl") or _extract_str(payload, "installer_url"),
            base_url=resolve_base_url(),
        )
        result["installer_sha256"] = _extract_str(payload, "installerSha256") or _extract_str(
            payload, "sha256"
        )
        result["release_notes"] = _extract_str(payload, "releaseNotes") or _extract_str(
            payload, "notes"
        )
    else:
        result["feed_status"] = "up_to_date"
    return result
