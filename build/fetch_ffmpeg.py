#!/usr/bin/env python3
"""Fetch the pinned ffmpeg build into ``build/ffmpeg-bundle/`` for PyInstaller.

Reuses the runtime installer's download/verify/extract code, so the SHA-256 and the
member filter are defined once. ``DUCKY_FFMPEG_ZIP`` may point at an already-downloaded
archive (its SHA-256 is still checked) to skip the network.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "ducky_app"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backend.agent.video import ffmpeg_install as fi  # noqa: E402

DEFAULT_DEST = Path(__file__).resolve().parent / "ffmpeg-bundle"


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
            fi.download_verified(fi.FFMPEG_URL, fi.FFMPEG_SHA256, part)
            zip_path = part
        fi.extract_members(zip_path, staging)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(staging, dest)
    finally:
        part.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
    return dest


def main() -> int:
    try:
        dest = ensure_bundle()
    except fi.FfmpegInstallError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"ffmpeg bundle ready: {dest} ({fi.FFMPEG_VERSION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
