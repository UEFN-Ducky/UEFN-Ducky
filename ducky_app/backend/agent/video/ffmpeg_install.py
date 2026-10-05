"""Locate the pinned LGPL ffmpeg build: bundled with the app, else downloaded into AppData.

The archive is checked against a pinned SHA-256 before anything is extracted;
a mismatching or partial download is deleted and never executed. BtbN keeps
month-end autobuilds for ~2 years — bump the four constants together to update.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from frontend.app_paths import resolve_app_data_dir
from frontend.bundle_root import packaged_data_root

FFMPEG_RELEASE_TAG = "autobuild-2026-08-31-13-27"
FFMPEG_VERSION = "n9.0.1-11-ge47273f4d9"
FFMPEG_ZIP_ROOT = "ffmpeg-n9.0.1-11-ge47273f4d9-win64-lgpl-shared-9.0"
FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    f"{FFMPEG_RELEASE_TAG}/{FFMPEG_ZIP_ROOT}.zip"
)
FFMPEG_SHA256 = "83a824f0729a69d143c9865125bb86988a11dd388325f0033711045522068aa0"

_MARKER = ".installed"
_urlopen = urllib.request.urlopen

_lock = threading.Lock()  # guards _state / _thread
_install_lock = threading.Lock()  # one download at a time
_state: dict[str, Any] = {"state": "missing", "progress": 0.0, "error": ""}
_thread: threading.Thread | None = None


class FfmpegInstallError(RuntimeError):
    """User-facing install failure."""


def tools_root() -> Path:
    return resolve_app_data_dir() / "tools" / "ffmpeg"


def install_dir() -> Path:
    return tools_root() / FFMPEG_VERSION


def bundled_dir() -> Path | None:
    """``<data root>/tools/ffmpeg`` when running packaged and both exes are present."""
    root = packaged_data_root()
    if root is None:
        return None
    d = root / "tools" / "ffmpeg"
    if (d / "ffmpeg.exe").is_file() and (d / "ffprobe.exe").is_file():
        return d
    return None


def binaries() -> tuple[Path, Path] | None:
    b = bundled_dir()
    if b is not None:
        return b / "ffmpeg.exe", b / "ffprobe.exe"
    d = install_dir()
    ffmpeg, ffprobe = d / "ffmpeg.exe", d / "ffprobe.exe"
    if (d / _MARKER).is_file() and ffmpeg.is_file() and ffprobe.is_file():
        return ffmpeg, ffprobe
    return None


def _set(**fields: Any) -> None:
    with _lock:
        _state.update(fields)


def status() -> dict[str, Any]:
    if binaries() is not None:
        return {
            "state": "ready", "progress": 1.0, "error": "", "version": FFMPEG_VERSION,
            "bundled": bundled_dir() is not None,
        }
    with _lock:
        snap = dict(_state)
    if snap["state"] == "ready":  # removed from disk underneath us
        snap = {"state": "missing", "progress": 0.0, "error": ""}
    return {**snap, "version": FFMPEG_VERSION, "bundled": False}


def _wanted_member(name: str) -> str | None:
    if name == f"{FFMPEG_ZIP_ROOT}/LICENSE.txt":
        return "LICENSE.txt"
    prefix = f"{FFMPEG_ZIP_ROOT}/bin/"
    if not name.startswith(prefix):
        return None
    leaf = name[len(prefix):]
    if not leaf or "/" in leaf:
        return None
    low = leaf.lower()
    if low in ("ffmpeg.exe", "ffprobe.exe") or low.endswith(".dll"):
        return leaf
    return None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_verified(url: str, sha256: str, dest: Path, progress: Any = None) -> None:
    """Stream ``url`` to ``dest`` and check SHA-256; a mismatch deletes ``dest`` and raises."""
    req = urllib.request.Request(url, headers={"User-Agent": "UEFN-Ducky"})
    try:
        resp = _urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FfmpegInstallError(
                "The ffmpeg download is no longer available — update UEFN-Ducky."
            ) from exc
        raise FfmpegInstallError(f"ffmpeg download failed (HTTP {exc.code}).") from exc
    except OSError as exc:
        raise FfmpegInstallError("ffmpeg download failed — check your internet connection.") from exc
    sha = hashlib.sha256()
    with resp, dest.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            if total and progress is not None:
                progress(min(0.99, done / total))
    if sha.hexdigest() != sha256:
        dest.unlink(missing_ok=True)
        raise FfmpegInstallError("The ffmpeg download was corrupted (checksum mismatch).")


def extract_members(zip_path: Path, dest: Path) -> None:
    """Extract only the wanted members into ``dest`` (created) and write the version marker."""
    dest.mkdir(parents=True, exist_ok=True)
    found: set[str] = set()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                leaf = _wanted_member(name)
                if leaf:
                    (dest / leaf).write_bytes(zf.read(name))
                    found.add(leaf.lower())
    except zipfile.BadZipFile as exc:
        raise FfmpegInstallError("The ffmpeg download was corrupted.") from exc
    if not {"ffmpeg.exe", "ffprobe.exe"} <= found:
        raise FfmpegInstallError("The ffmpeg archive is missing ffmpeg.exe or ffprobe.exe.")
    (dest / _MARKER).write_text(FFMPEG_VERSION, encoding="utf-8")


def _install_now() -> None:
    root = tools_root()
    root.mkdir(parents=True, exist_ok=True)
    part = root / f"{FFMPEG_VERSION}.zip.part"
    staging = root / f"{FFMPEG_VERSION}.tmp"
    try:
        download_verified(FFMPEG_URL, FFMPEG_SHA256, part, lambda p: _set(progress=p))
        shutil.rmtree(staging, ignore_errors=True)
        extract_members(part, staging)
        final = install_dir()
        shutil.rmtree(final, ignore_errors=True)
        os.replace(staging, final)
    finally:
        part.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def ensure_installed() -> tuple[Path, Path]:
    """Return (ffmpeg, ffprobe), downloading first if needed. Blocks."""
    found = binaries()
    if found:
        return found
    with _install_lock:
        found = binaries()
        if found:
            return found
        _set(state="installing", progress=0.0, error="")
        try:
            _install_now()
        except FfmpegInstallError as exc:
            _set(state="error", error=str(exc))
            raise
        except Exception as exc:
            msg = f"ffmpeg install failed: {exc}"
            _set(state="error", error=msg)
            raise FfmpegInstallError(msg) from exc
        _set(state="ready", progress=1.0, error="")
    found = binaries()
    if not found:
        raise FfmpegInstallError("ffmpeg install did not complete.")
    return found


def _background() -> None:
    try:
        ensure_installed()
    except FfmpegInstallError:
        pass  # surfaced through status()


def start_install() -> dict[str, Any]:
    """Kick off a background install if needed; return the current status."""
    global _thread
    if binaries() is not None:
        return status()
    with _lock:
        if _thread is None or not _thread.is_alive():
            _state.update(state="installing", progress=0.0, error="")
            _thread = threading.Thread(target=_background, name="ffmpeg-install", daemon=True)
            _thread.start()
    return status()


def remove() -> dict[str, Any]:
    """Clear the AppData install only; the bundled copy is never touched."""
    with _install_lock:
        shutil.rmtree(tools_root(), ignore_errors=True)
        _set(state="missing", progress=0.0, error="")
    return status()
