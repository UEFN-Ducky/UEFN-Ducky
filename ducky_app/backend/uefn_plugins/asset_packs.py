"""Asset packs: big files (a toolchain, an SDK) a plugin version carries next to its zip.

plugin.json names them, and that signed manifest is the trust root:

    "asset_packs": [{"id": "toolchain-win64", "sha256": "…", "bytes": 412000000, "platform": "win64"}]

At a Store install each pack for this PC downloads in the background (HTTP Range resume
from a ``.part`` file), is checked against that size and sha256, and unpacks (zip, tar,
tar.gz/.xz/.zst) into its own folder the plugin asks for with ``api.asset_pack_path(id)``.
Progress is kept per pack for the plugin's panel (``api.asset_packs()``).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

_log = logging.getLogger(__name__)

PACKS_DIR = "plugin_asset_packs"
MARKER = ".asset_pack.json"
MAX_PACK_BYTES = 8 * 1024 * 1024 * 1024
CHUNK = 1 << 20
ATTEMPTS = 4
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_PLATFORM_RE = re.compile(r"^[a-z0-9-]{0,16}$")

# (plugin_id, pack_id) -> {id, state, done, total, error}
_PROGRESS: dict[tuple[str, str], dict[str, Any]] = {}
_PROGRESS_LOCK = threading.Lock()
_PLUGIN_LOCKS: dict[str, threading.Lock] = {}
_RUNNING: set[str] = set()


def this_platform() -> str:
    if os.name == "nt":
        return "win64"
    return "mac" if sys.platform == "darwin" else "linux64"


def manifest_packs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """The manifest's ``asset_packs``, checked. A bad entry raises ValueError (install refuses)."""
    raw = manifest.get("asset_packs")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('"asset_packs" must be a list')
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError('"asset_packs" entries must be objects')
        pid = str(entry.get("id") or "")
        sha = str(entry.get("sha256") or "").lower()
        size = entry.get("bytes")
        platform = str(entry.get("platform") or "").lower()
        if not _ID_RE.match(pid) or ".." in pid or pid in seen:
            raise ValueError(f'"asset_packs" id {pid!r} must be unique, 1-64 of a-z 0-9 - _ .')
        if not _SHA_RE.match(sha):
            raise ValueError(f'"asset_packs" {pid}: sha256 must be 64 hex characters')
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_PACK_BYTES:
            raise ValueError(f'"asset_packs" {pid}: bytes must be 1..{MAX_PACK_BYTES}')
        if not _PLATFORM_RE.match(platform):
            raise ValueError(f'"asset_packs" {pid}: platform must be up to 16 of a-z 0-9 -')
        seen.add(pid)
        out.append({"id": pid, "sha256": sha, "bytes": size, "platform": platform})
    return out


def packs_for_this_pc(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    here = this_platform()
    return [p for p in manifest_packs(manifest) if p["platform"] in ("", "any", here)]


def packs_root() -> Path:
    from backend.skills.discovery import appdata_dir

    return appdata_dir() / PACKS_DIR


def _plugin_root(plugin_id: str) -> Path:
    from backend.uefn_plugins.store import normalize_plugin_id

    return packs_root() / normalize_plugin_id(plugin_id)


def _installed_sha(folder: Path) -> str:
    try:
        return str(json.loads((folder / MARKER).read_text(encoding="utf-8")).get("sha256") or "")
    except (OSError, ValueError):
        return ""


def asset_pack_path(plugin_id: str, pack_id: str) -> Path | None:
    """The unpacked folder of an installed pack, or None until it has fully landed."""
    if not _ID_RE.match(str(pack_id or "")):
        return None
    folder = _plugin_root(plugin_id) / pack_id
    return folder if _installed_sha(folder) else None


def _set(plugin_id: str, pack_id: str, **fields: Any) -> None:
    with _PROGRESS_LOCK:
        row = _PROGRESS.setdefault((plugin_id, pack_id), {"id": pack_id})
        row.update(fields)


def status(plugin_id: str, manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One row per pack this PC needs: {id, bytes, state, done, total, error, path}.

    state: ``missing`` | ``downloading`` | ``verifying`` | ``unpacking`` | ``ready`` | ``failed``.
    """
    if manifest is None:
        from backend.uefn_plugins.store import load_plugin_manifest

        manifest = load_plugin_manifest(plugin_id) or {}
    rows = []
    for pack in packs_for_this_pc(manifest):
        folder = _plugin_root(plugin_id) / pack["id"]
        ready = _installed_sha(folder) == pack["sha256"]
        with _PROGRESS_LOCK:
            live = dict(_PROGRESS.get((plugin_id, pack["id"]), {}))
        row = {"id": pack["id"], "bytes": pack["bytes"], "state": "missing", "done": 0, "total": pack["bytes"], "error": ""}
        row.update({k: v for k, v in live.items() if k != "id"})
        if ready:
            row.update(state="ready", done=pack["bytes"], error="", path=str(folder))
        rows.append(row)
    return rows


def download(
    url_for: Callable[[], str],
    part: Path,
    size: int,
    sha256: str,
    on_progress: Callable[[int], None] | None = None,
) -> Path:
    """Fetch into ``part`` (resuming what is there with a Range request), check size and
    sha256, and rename it to drop the ``.part``. A wrong hash deletes the file and raises
    ValueError; a dropped connection is retried, and what arrived stays for the next try."""
    part.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    have = part.stat().st_size if part.is_file() else 0
    if have > size:
        part.unlink()
        have = 0
    if have:
        with part.open("rb") as f:
            while chunk := f.read(CHUNK):
                h.update(chunk)
    last_exc: Exception | None = None
    for attempt in range(ATTEMPTS):
        if have >= size:
            break
        try:
            headers = {"Range": f"bytes={have}-"} if have else {}
            req = urllib.request.Request(url_for(), headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                if have and getattr(resp, "status", 200) != 206:
                    h, have = hashlib.sha256(), 0  # the server sent it whole: start over
                with part.open("ab" if have else "wb") as f:
                    while chunk := resp.read(CHUNK):
                        if have + len(chunk) > size:
                            raise ValueError("asset pack is bigger than its manifest says")
                        f.write(chunk)
                        h.update(chunk)
                        have += len(chunk)
                        if on_progress:
                            on_progress(have)
        except (OSError, http.client.HTTPException) as exc:  # URLError is an OSError; a cut body is IncompleteRead
            last_exc = exc
            _log.info("asset pack download interrupted at %d/%d: %s", have, size, exc)
            time.sleep(min(2.0 * attempt, 5.0))
    if have < size:
        raise ConnectionError(f"asset pack download stopped at {have} of {size} bytes: {last_exc}")
    if h.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise ValueError("asset pack failed its sha256 check (deleted; it downloads again next time)")
    done = part.with_suffix("")
    os.replace(part, done)
    return done


def _open_tar(archive: Path) -> tarfile.TarFile:
    with archive.open("rb") as f:
        magic = f.read(4)
    if magic == b"\x28\xb5\x2f\xfd":  # zstd
        try:
            return tarfile.open(archive, mode="r:zst")  # Python 3.14+
        except (tarfile.CompressionError, ValueError, AttributeError):
            pass
        try:
            import zstandard  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("this Ducky can't read .zst asset packs") from exc
        stream = zstandard.ZstdDecompressor().stream_reader(archive.open("rb"))
        return tarfile.open(fileobj=stream, mode="r|")
    return tarfile.open(archive, mode="r:*")


def unpack(archive: Path, target: Path) -> None:
    """Unpack a zip or tar(.gz/.xz/.bz2/.zst) into ``target``; nothing lands outside it."""
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            root = target.resolve()
            for info in zf.infolist():
                dest = (target / info.filename).resolve()
                if not dest.is_relative_to(root):
                    raise ValueError(f"asset pack entry escapes its folder: {info.filename}")
            zf.extractall(target)
        return
    with _open_tar(archive) as tf:
        tf.extractall(target, filter="data")


def _lock(plugin_id: str) -> threading.Lock:
    with _PROGRESS_LOCK:
        return _PLUGIN_LOCKS.setdefault(plugin_id, threading.Lock())


def ensure_packs(
    plugin_id: str,
    manifest: dict[str, Any],
    url_for: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    """Download, verify and unpack every pack this PC needs that is not there yet.
    ``url_for(pack)`` returns a (fresh) download URL. Returns {ok, packs, error}."""
    root = _plugin_root(plugin_id)
    errors: list[str] = []
    with _lock(plugin_id):
        for pack in packs_for_this_pc(manifest):
            pid, sha, size = pack["id"], pack["sha256"], pack["bytes"]
            folder = root / pid
            if _installed_sha(folder) == sha:
                _set(plugin_id, pid, state="ready", done=size, total=size, error="")
                continue
            part = root / ".downloads" / f"{pid}-{sha[:16]}.pack.part"
            try:
                _set(plugin_id, pid, state="downloading", done=0, total=size, error="")
                archive = download(
                    lambda pack=pack: url_for(pack),
                    part,
                    size,
                    sha,
                    lambda n, pid=pid: _set(plugin_id, pid, done=n),
                )
                _set(plugin_id, pid, state="unpacking", done=size)
                staging = root / f".staging-{pid}"
                shutil.rmtree(staging, ignore_errors=True)
                unpack(archive, staging)
                (staging / MARKER).write_text(json.dumps({"id": pid, "sha256": sha, "bytes": size}), encoding="utf-8")
                old = root / f".old-{pid}"
                shutil.rmtree(old, ignore_errors=True)
                if folder.exists():
                    os.replace(folder, old)
                os.replace(staging, folder)
                shutil.rmtree(old, ignore_errors=True)
                archive.unlink(missing_ok=True)
                _set(plugin_id, pid, state="ready", error="")
            except Exception as exc:  # noqa: BLE001 — the row says why; the next try resumes
                _log.warning("asset pack %s/%s failed: %s", plugin_id, pid, exc)
                _set(plugin_id, pid, state="failed", error=str(exc))
                errors.append(f"{pid}: {exc}")
    return {"ok": not errors, "packs": status(plugin_id, manifest), "error": "; ".join(errors)}


def remove_packs(plugin_id: str) -> None:
    """Delete every pack of a plugin (its uninstall cleanup)."""
    shutil.rmtree(_plugin_root(plugin_id), ignore_errors=True)
    with _PROGRESS_LOCK:
        for key in [k for k in _PROGRESS if k[0] == plugin_id]:
            del _PROGRESS[key]


def store_url_for(slug: str, version: str) -> Callable[[dict[str, Any]], str]:
    """A ``url_for`` that asks the Store (``asset-packs``) for a fresh link each time.
    The Store's sha256 must match the manifest's, which is what the bytes are checked against."""

    def url_for(pack: dict[str, Any]) -> str:
        from frontend.duckyos_account import _store_collect

        payload = _store_collect(
            "asset-packs", {"slug": slug, "version": version}, allow_anonymous=True, timeout=60.0
        )
        for row in payload.get("packs") or []:
            if isinstance(row, dict) and row.get("id") == pack["id"]:
                if str(row.get("sha256") or "").lower() != pack["sha256"]:
                    raise ValueError(f"the Store's {pack['id']} is not the one this version names")
                url = str(row.get("url") or "")
                if url:
                    return url
        raise ValueError(f"the Store has no asset pack {pack['id']} for {slug} {version}")

    return url_for


def start_install(plugin_id: str, manifest: dict[str, Any], slug: str = "") -> bool:
    """Fetch this plugin's missing packs from the Store in the background. False when it
    needs none or a fetch is already running."""
    if not packs_for_this_pc(manifest):
        return False
    with _PROGRESS_LOCK:
        if plugin_id in _RUNNING:
            return False
        _RUNNING.add(plugin_id)
    version = str(manifest.get("version") or "")
    url_for = store_url_for(slug or plugin_id, version)

    def run() -> None:
        try:
            ensure_packs(plugin_id, manifest, url_for)
        finally:
            with _PROGRESS_LOCK:
                _RUNNING.discard(plugin_id)

    threading.Thread(target=run, daemon=True, name=f"asset-packs-{plugin_id}").start()
    return True
