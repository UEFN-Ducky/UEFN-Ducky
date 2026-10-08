"""Publish UEFN Ducky plugins from the app to the Store (fixed contract).

The account plugin calls this module. A publish compiles the plugin with the build
engine (no readable Python ships), zips the private source separately, and uploads
both to the Store over a chunked collect protocol. A team release goes live for the
team at once (after the server's Manage-plugins check); a public release goes to
review, which happens inside Ducky: a reviewer downloads the private source, rebuilds
it locally, and the server signs and publishes the reviewer's build.

Private source is never served by the normal download path — only ``item-source``
(members with Manage plugins) and ``review-source`` (reviewers) return it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

from backend.uefn_plugins.compile import store_version
from backend.uefn_plugins.store import (
    PLUGIN_MANIFEST,
    appdata_ai_plugins_dir,
    normalize_plugin_id,
    plugin_dir,
)

_log = logging.getLogger("uefn_plugins.publishing")

ProgressFn = Callable[[float, str], None]

# Source trees never ship repo internals or build junk (backend .py IS kept — it is
# the private source a reviewer rebuilds).
_SOURCE_SKIP_DIRS = frozenset({".git", ".github", ".pytest_cache", "__pycache__", "deploy", "node_modules"})
_SOURCE_SKIP_SUFFIX = frozenset({".pyc", ".pyo", ".zip", ".bin"})


def _progress(fn: ProgressFn | None, frac: float, msg: str) -> None:
    if fn is not None:
        try:
            fn(max(0.0, min(1.0, frac)), msg)
        except Exception:
            pass


def _store(event: str, body: dict[str, Any] | None = None, *, timeout: float = 60.0) -> dict[str, Any]:
    from frontend.duckyos_account import _store_collect

    return _store_collect(event, body or {}, timeout=timeout)


def _error_text(exc: BaseException) -> str:
    return str(getattr(exc, "message", "") or exc)


def _staff_binding() -> str:
    """A short-lived Store staff binding for this account (same path the website's
    Store admin uses). Raises with a plain message when the account is not staff."""
    from frontend.duckyos_account import api_request

    status, parsed, _raw = api_request(
        "POST", "/api/v1/acl/plugin-binding", {"plugin_id": "uefn-ducky-store"}, timeout=20.0
    )
    binding = ""
    if 200 <= int(status) < 300 and isinstance(parsed, dict):
        binding = str(parsed.get("binding") or "")
    if not binding:
        raise PermissionError("Reviewing needs Store staff access on this account.")
    return binding


def _admin(action: str, data: dict[str, Any], *, zip_b64: str = "", timeout: float = 60.0) -> dict[str, Any]:
    """Call a staff action on the Store's admin event (the existing staff gate)."""
    body: dict[str, Any] = {"binding": _staff_binding(), "action": action, "data": data}
    if zip_b64:
        body["zip_b64"] = zip_b64
    return _store("admin", body, timeout=timeout)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plugin_source_dir(plugin_id: str) -> Path | None:
    """The local source tree to publish: the AI-plugin draft, else a plain-source install."""
    pid = normalize_plugin_id(plugin_id)
    draft = appdata_ai_plugins_dir() / pid
    if (draft / PLUGIN_MANIFEST).is_file():
        return draft
    installed = plugin_dir(pid)
    # A plain-source install still carries its .py; a compiled one does not (nothing to republish).
    if (installed / PLUGIN_MANIFEST).is_file() and not _read_manifest(installed).get("compiled"):
        return installed
    return None


def _read_manifest(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / PLUGIN_MANIFEST).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def zip_source(src: Path) -> bytes:
    """Zip the plugin's private source (minus build junk). Keeps backend .py."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            parts = path.relative_to(src).parts
            if any(p in _SOURCE_SKIP_DIRS for p in parts) or any(p.startswith(".") for p in parts):
                continue
            if path.suffix.lower() in _SOURCE_SKIP_SUFFIX:
                continue
            zf.writestr("/".join(parts), path.read_bytes())
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def publish(
    plugin_id: str,
    target: str,
    team_id: str = "",
    notes: str = "",
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Build, zip the source, and upload both. ``target`` is 'team' or 'public'.

    Team → live for the team immediately (server checks Manage plugins). Public →
    pending review. ``team_id`` is the owning team; a new plugin needs one for either
    target (a new version keeps its owner). Returns {ok, status, version, signature?}
    or {ok: False, error}.
    """
    from backend.uefn_plugins import compile as engine

    pid = normalize_plugin_id(plugin_id)
    target = "team" if str(target).strip().lower() == "team" else "public"
    team_id = str(team_id or "").strip()
    if target == "team" and not team_id:
        return {"ok": False, "error": "team_id required to publish to a team"}

    src = plugin_source_dir(pid)
    if src is None:
        return {"ok": False, "error": f"no local source to publish for {pid}"}
    manifest = _read_manifest(src)
    version = store_version(manifest.get("version"))

    with tempfile.TemporaryDirectory(prefix="ducky-publish-") as td:
        build_zip = Path(td) / f"{pid}-build.zip"
        _progress(progress, 0.05, "Compiling plugin…")
        try:
            report = engine.build_plugin(
                src, build_zip, visibility=target, team_id=team_id, release=True,
                progress=lambda f, m: _progress(progress, 0.05 + 0.45 * f, m),
            )
        except engine.CompileError as exc:
            return {"ok": False, "error": f"Build failed: {exc}"}
        build_bytes = build_zip.read_bytes()
        _progress(progress, 0.52, "Packing source…")
        source_bytes = zip_source(src)
        _progress(progress, 0.6, "Uploading…")
        try:
            done = _store(
                "plugin-publish",
                {
                    "slug": pid,
                    "version": version,
                    "target": target,
                    "teamId": team_id,
                    "notes": str(notes or "")[:2000],
                    "compiled": True,
                    "buildSha256": report.get("sha256") or _sha256(build_bytes),
                    "sourceSha256": _sha256(source_bytes),
                    "buildB64": _b64(build_bytes),
                    "sourceB64": _b64(source_bytes),
                },
                timeout=180.0,
            )
        except Exception as exc:  # noqa: BLE001 — Store refusals come back as plain text
            return {"ok": False, "id": pid, "version": version, "error": _error_text(exc)}

    _progress(progress, 1.0, "Published" if done.get("ok") else "Done")
    status = str(done.get("status") or ("live" if target == "team" else "pending"))
    return {
        "ok": bool(done.get("ok", True)),
        "id": pid,
        "version": version,
        "target": target,
        "team_id": team_id,
        "status": status,
        "signature": done.get("signature"),
        "error": done.get("error") or "",
    }


def open_for_edit(plugin_id: str, team_id: str = "") -> dict[str, Any]:
    """Fetch a team item's private source (Manage plugins only) into a local draft and open it."""
    pid = normalize_plugin_id(plugin_id)
    team_id = str(team_id or "").strip()
    try:
        payload = _store("item-source", {"slug": pid, "teamId": team_id}, timeout=120.0)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "id": pid, "error": _error_text(exc)}
    zip_b64 = str(payload.get("zipB64") or "")
    if not zip_b64:
        return {"ok": False, "error": str(payload.get("error") or "No source returned (need Manage plugins?)")}
    try:
        raw = base64.b64decode(zip_b64)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Invalid source zip: {exc}"}
    draft = appdata_ai_plugins_dir() / pid
    files = _extract_source(raw, draft)
    opened = _open_in_editor(draft)
    return {"ok": True, "id": pid, "path": str(draft), "files": files, "opened": opened}


def _extract_source(raw: bytes, dest: Path) -> list[str]:
    import shutil

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = Path(info.filename).parts
            if not parts or ".." in parts or Path(info.filename).is_absolute():
                continue
            target = dest.joinpath(*parts)
            try:
                if not target.resolve().is_relative_to(dest.resolve()):
                    continue
            except (OSError, ValueError):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info.filename))
            out.append("/".join(parts))
    return out


def _open_in_editor(draft: Path) -> bool:
    """Best-effort: open the draft's manifest in Ducky's editor via the existing path."""
    try:
        from frontend.open_files import dispatch_open_files

        dispatch_open_files([str(draft / PLUGIN_MANIFEST)])
        return True
    except Exception:
        _log.debug("could not open draft in editor", exc_info=True)
        return False


def publish_status(plugin_id: str) -> dict[str, Any]:
    """{published_to, versions, protected} — protected means the latest version is compiled."""
    pid = normalize_plugin_id(plugin_id)
    try:
        payload = _store("publish-status", {"slug": pid})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "published_to": [], "versions": [], "protected": False}
    return {
        "ok": True,
        "id": pid,
        "published_to": list(payload.get("published_to") or []),
        "versions": list(payload.get("versions") or []),
        "protected": bool(payload.get("protected")),
    }


def review_queue() -> dict[str, Any]:
    """Public submissions awaiting review (Store staff, inside Ducky)."""
    try:
        payload = _admin("store-review-queue", {})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _error_text(exc), "pending": []}
    pending = []
    for row in payload.get("pending") or []:
        if not isinstance(row, dict):
            continue
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        version = row.get("version") if isinstance(row.get("version"), dict) else {}
        pending.append(
            {
                "slug": item.get("slug") or version.get("slug") or "",
                "name": item.get("name") or "",
                "version": version.get("version") or item.get("latest_version") or "",
                "team": row.get("teamName") or "",
                "submitted_by": row.get("submittedBy") or "",
                "changelog": version.get("changelog") or "",
                "compiled": bool(version.get("compiled")),
                "has_source": bool(version.get("source_storage_key")),
            }
        )
    return {"ok": True, "pending": pending, "can_manage": bool(payload.get("canManage"))}


def review_approve(slug: str, version: str = "", notes: str = "", progress: ProgressFn | None = None) -> dict[str, Any]:
    """Download the submission's private source, rebuild it here with the build engine,
    upload that build and publish it. What ships is the reviewer's own build, never the
    uploader's binary; the Store signs it as it goes live."""
    slug = normalize_plugin_id(slug)
    version = str(version or "").strip()
    _progress(progress, 0.05, "Fetching source…")
    try:
        payload = _admin("review-source", {"slug": slug, "version": version}, timeout=120.0)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "id": slug, "error": _error_text(exc)}
    version = str(payload.get("version") or version)
    zip_b64 = str(payload.get("zipB64") or "")
    if not zip_b64:
        return {"ok": False, "id": slug, "error": "No source to review"}
    try:
        raw = base64.b64decode(zip_b64)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "id": slug, "error": f"Invalid source zip: {exc}"}
    from backend.uefn_plugins import compile as engine

    with tempfile.TemporaryDirectory(prefix="ducky-review-") as td:
        work = Path(td)
        src = work / "src"
        _extract_source(raw, src)
        built_version = store_version(_read_manifest(src).get("version"))
        if version and built_version != version:
            return {"ok": False, "id": slug, "error": f"Source says version {built_version}, the submission is {version}"}
        build_zip = work / "build.zip"
        _progress(progress, 0.2, "Rebuilding…")
        try:
            # smoke_test=False: never import a submitted plugin's code on a reviewer's PC.
            report = engine.build_plugin(
                src, build_zip, visibility="public", team_id="", smoke_test=False, release=True,
                progress=lambda f, m: _progress(progress, 0.2 + 0.5 * f, m),
            )
        except engine.CompileError as exc:
            return {"ok": False, "id": slug, "error": f"Rebuild failed: {exc}"}
        build_bytes = build_zip.read_bytes()
        _progress(progress, 0.75, "Uploading approved build…")
        try:
            done = _admin(
                "review-approve",
                {
                    "slug": slug,
                    "version": version,
                    "notes": str(notes or "")[:2000],
                    "buildSha256": report.get("sha256") or _sha256(build_bytes),
                },
                zip_b64=_b64(build_bytes),
                timeout=180.0,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "id": slug, "version": version, "error": _error_text(exc)}
    _progress(progress, 1.0, "Approved")
    item = done.get("item") if isinstance(done.get("item"), dict) else {}
    return {"ok": True, "id": slug, "version": version, "status": item.get("status") or "published"}


def review_reject(slug: str, version: str, note: str) -> dict[str, Any]:
    """Reject a submission with a note the author sees (Store staff)."""
    slug = normalize_plugin_id(slug)
    note = str(note or "").strip()
    if not note:
        return {"ok": False, "error": "a rejection note is required"}
    try:
        payload = _admin("review-reject", {"slug": slug, "version": str(version or "").strip(), "note": note})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "id": slug, "error": _error_text(exc)}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    return {"ok": True, "id": slug, "status": item.get("status") or "rejected"}
