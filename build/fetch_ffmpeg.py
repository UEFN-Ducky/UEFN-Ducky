#!/usr/bin/env python3
"""Fetch the pinned ffmpeg build into ``build/ffmpeg-bundle/`` for PyInstaller.

Reuses the runtime installer's download/verify/extract code, so the SHA-256 and the
member filter are defined once. ``DUCKY_FFMPEG_ZIP`` may point at an already-downloaded
archive (its SHA-256 is still checked) to skip the network.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "ducky_app"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backend.agent.video import ffmpeg_install as fi  # noqa: E402

DEFAULT_DEST = Path(__file__).resolve().parent / "ffmpeg-bundle"
DOWNLOAD_TRIES = 5
DOWNLOAD_WAIT_S = 20

# macOS: static single-file builds (no dylibs), one per arch, pinned by SHA-256.
# ponytail: eugeneware/ffmpeg-static b6.1.1 (FFmpeg 6.1.1, GPL build); swap for an LGPL
# macOS build when one is published with the same per-arch pinning.
MACOS_FFMPEG_VERSION = "6.1.1-static"
_MACOS_BASE = "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"
MACOS_PINS = {
    "arm64": {
        "ffmpeg": (f"{_MACOS_BASE}/ffmpeg-darwin-arm64", "a90e3db6a3fd35f6074b013f948b1aa45b31c6375489d39e572bea3f18336584"),
        "ffprobe": (f"{_MACOS_BASE}/ffprobe-darwin-arm64", "bb2db6f5d8cef919da12fbf592119a987202a8c060a886f3cab091f9cab90b64"),
        "LICENSE.txt": (f"{_MACOS_BASE}/darwin-arm64.LICENSE", "cb48bf09a11f5fb576cddb0431c8f5ed0a60157a9ec942adffc13907cbe083f2"),
    },
    "x64": {
        "ffmpeg": (f"{_MACOS_BASE}/ffmpeg-darwin-x64", "ebdddc936f61e14049a2d4b549a412b8a40deeff6540e58a9f2a2da9e6b18894"),
        "ffprobe": (f"{_MACOS_BASE}/ffprobe-darwin-x64", "fa3add0ce901f7241abe0dfc0155d958fc834aca3f8ce61f87cc712ae669c1e0"),
        "LICENSE.txt": (f"{_MACOS_BASE}/darwin-arm64.LICENSE", "cb48bf09a11f5fb576cddb0431c8f5ed0a60157a9ec942adffc13907cbe083f2"),
    },
}


def mac_arch(machine: str | None = None) -> str:
    m = (machine or platform.machine()).lower()
    return "arm64" if m in ("arm64", "aarch64") else "x64"


def _download_with_retries(part: Path, url: str = "", sha256: str = "") -> None:
    """One GitHub 504 must not end a release build. Every try checks the SHA-256 again."""
    url, sha256 = url or fi.FFMPEG_URL, sha256 or fi.FFMPEG_SHA256
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        try:
            fi.download_verified(url, sha256, part)
            return
        except (fi.FfmpegInstallError, OSError) as exc:
            if attempt == DOWNLOAD_TRIES:
                raise fi.FfmpegInstallError(f"ffmpeg download failed {attempt} times: {exc}") from exc
            wait = DOWNLOAD_WAIT_S * attempt
            print(f"ffmpeg download failed ({exc}), try {attempt + 1} of {DOWNLOAD_TRIES} in {wait}s")
            time.sleep(wait)


def _up_to_date(dest: Path) -> bool:
    marker = dest / fi._MARKER
    return (
        marker.is_file()
        and marker.read_text(encoding="utf-8").strip() == fi.FFMPEG_VERSION
        and (dest / "ffmpeg.exe").is_file()
        and (dest / "ffprobe.exe").is_file()
    )


def ensure_bundle(dest: Path = DEFAULT_DEST) -> Path:
    """Return ``dest`` holding the verified ffmpeg files; raises on any mismatch."""
    dest = Path(dest)
    if _up_to_date(dest):
        return dest
    staging = dest.with_name(dest.name + ".tmp")
    part = dest.with_name(dest.name + ".zip.part")
    shutil.rmtree(staging, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        local = os.environ.get("DUCKY_FFMPEG_ZIP", "").strip()
        if local:
            zip_path = Path(local)
            if not zip_path.is_file():
                raise fi.FfmpegInstallError(f"DUCKY_FFMPEG_ZIP not found: {zip_path}")
            if fi.sha256_of(zip_path) != fi.FFMPEG_SHA256:
                raise fi.FfmpegInstallError("DUCKY_FFMPEG_ZIP checksum mismatch.")
        else:
            print(f"Downloading {fi.FFMPEG_URL}")
            _download_with_retries(part)
            zip_path = part
        fi.extract_members(zip_path, staging)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(staging, dest)
    finally:
        part.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
    return dest


def ensure_macos_bundle(dest: Path = DEFAULT_DEST, arch: str | None = None) -> Path:
    """``dest`` with the pinned static ``ffmpeg`` + ``ffprobe`` for ``arch`` (arm64 / x64)."""
    dest, arch = Path(dest), arch or mac_arch()
    stamp = f"{MACOS_FFMPEG_VERSION}-{arch}"
    marker = dest / fi._MARKER
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == stamp and (
        all((dest / n).is_file() for n in MACOS_PINS[arch])
    ):
        return dest
    staging = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        for name, (url, sha) in MACOS_PINS[arch].items():
            print(f"Downloading {url}")
            _download_with_retries(staging / name, url, sha)
            if name != "LICENSE.txt":
                (staging / name).chmod(0o755)
        (staging / fi._MARKER).write_text(stamp, encoding="utf-8")
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(staging, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return dest


def main() -> int:
    try:
        dest = ensure_macos_bundle() if sys.platform == "darwin" else ensure_bundle()
    except fi.FfmpegInstallError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"ffmpeg bundle ready: {dest} ({fi.FFMPEG_VERSION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
