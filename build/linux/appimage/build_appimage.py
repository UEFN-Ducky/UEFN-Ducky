#!/usr/bin/env python3
"""Pack ``dist/uefn-ducky/`` (build_exes.py on Linux) into ``dist/UEFN-Ducky-<ver>-<arch>.AppImage``.

AppDir layout::

    AppRun                      start, --install-integration, --uninstall [--purge]
    uefn-ducky.desktop          packaging/linux/uefn-ducky.desktop (S12)
    uefn-ducky.png, .DirIcon    256 px icon
    usr/bin/                    the PyInstaller one-dir build (uefn-ducky, -bridge, Ducky-Launcher)
    usr/lib/libportaudio.so.2   -> ../bin/_internal/libportaudio.so.2 (microphone)
    usr/share/applications/, usr/share/icons/hicolor/

The AppImage carries zsync update info ``zsync|<base>/UEFN-Ducky-latest-<arch>.AppImage.zsync``
and the ``.zsync`` is written beside it. The release lane uploads both to ``<base>/``, with the
.zsync renamed to the ``latest`` name. The .zsync points at the versioned AppImage by its relative
file name, so both live in one folder.

Usage (Linux, from the repo root):  python3 build/linux/appimage/build_appimage.py [--arch aarch64]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
APP_ID = "uefn-ducky"
APPIMAGETOOL_VERSION = "1.9.1"
APPIMAGETOOL_URL = "https://github.com/AppImage/appimagetool/releases/download/{v}/appimagetool-{arch}.AppImage"
APPIMAGETOOL_SHA256 = {
    "x86_64": "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0",
    "aarch64": "f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158",
}
# The static type-2 runtime (libfuse3 linked in, needs only fusermount/fusermount3 on PATH): stock
# Ubuntu 22.04/24.04 ship no libfuse2, which the classic runtime needs. Pinned so appimagetool never
# embeds its own unpinned "continuous" download.
RUNTIME_VERSION = "20251108"
RUNTIME_URL = "https://github.com/AppImage/type2-runtime/releases/download/{v}/runtime-{arch}"
RUNTIME_SHA256 = {
    "x86_64": "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d",
    "aarch64": "00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444",
}
ARCHES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
# Where the release lane publishes the Linux downloads (the Store site). Override per run.
UPDATE_BASE = os.environ.get("UEFN_DUCKY_APPIMAGE_UPDATE_BASE", "https://uefnducky.org/downloads/linux")
PORTAUDIO = "libportaudio.so.2"


def normalize_arch(machine: str | None = None) -> str:
    raw = (machine or platform.machine()).lower()
    if raw not in ARCHES:
        raise SystemExit(f"ERROR: no AppImage for CPU {raw!r} (x86_64 and aarch64 only)")
    return ARCHES[raw]


def appimage_name(version: str, arch: str) -> str:
    return f"UEFN-Ducky-{version}-{arch}.AppImage"


def update_info(arch: str, base: str = UPDATE_BASE) -> str:
    return f"zsync|{base.rstrip('/')}/UEFN-Ducky-latest-{arch}.AppImage.zsync"


def read_version(root: Path = ROOT) -> str:
    text = (root / "ducky_app" / "frontend" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'(?m)^__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise SystemExit("ERROR: no __version__ in ducky_app/frontend/__init__.py")
    return m.group(1).partition("+")[0]


def assemble_appdir(app_dir: Path, appdir: Path, *, root: Path = ROOT) -> Path:
    """Lay out ``appdir`` from the one-dir build ``app_dir``. Raises when a part is missing."""
    if not (app_dir / APP_ID).is_file():
        raise SystemExit(f"ERROR: {app_dir / APP_ID} missing: run build/build_exes.py on Linux first")
    if not (app_dir / "_internal" / PORTAUDIO).is_file():
        raise SystemExit(f"ERROR: {app_dir}/_internal/{PORTAUDIO} missing (unified.spec Linux branch)")
    packaging = root / "packaging" / "linux"
    desktop = packaging / f"{APP_ID}.desktop"
    icons = packaging / "icons" / "hicolor"
    if appdir.exists():
        shutil.rmtree(appdir)
    usr = appdir / "usr"
    shutil.copytree(app_dir, usr / "bin", symlinks=True)
    apprun = appdir / "AppRun"
    # LF only: a Windows checkout may hand us CRLF, which bash refuses (#!/bin/bash\r).
    apprun.write_bytes((HERE / "AppRun").read_bytes().replace(b"\r\n", b"\n"))
    apprun.chmod(0o755)
    (usr / "share" / "applications").mkdir(parents=True)
    for dest in (appdir / desktop.name, usr / "share" / "applications" / desktop.name):
        shutil.copy2(desktop, dest)
    shutil.copytree(icons, usr / "share" / "icons" / "hicolor")
    shutil.copy2(icons / "256x256" / "apps" / f"{APP_ID}.png", appdir / f"{APP_ID}.png")
    (appdir / ".DirIcon").symlink_to(f"{APP_ID}.png")
    (usr / "lib").mkdir()
    (usr / "lib" / PORTAUDIO).symlink_to(f"../bin/_internal/{PORTAUDIO}")
    return appdir


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_pinned(what: str, url: str, want: str, dest: Path) -> Path:
    """``url`` cached at ``dest``, SHA-256 checked (a mismatch stops the build)."""
    if not (dest.is_file() and sha256_of(dest) == want):
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part")
        print(f"Downloading {url}")
        with urllib.request.urlopen(url, timeout=120) as resp, part.open("wb") as out:
            shutil.copyfileobj(resp, out)
        got = sha256_of(part)
        if got != want:
            part.unlink(missing_ok=True)
            raise SystemExit(f"ERROR: {what} SHA-256 {got} != pinned {want}")
        os.replace(part, dest)
    dest.chmod(0o755)
    return dest


def fetch_appimagetool(arch: str, cache: Path) -> Path:
    """The pinned appimagetool for ``arch``."""
    return _fetch_pinned("appimagetool", APPIMAGETOOL_URL.format(v=APPIMAGETOOL_VERSION, arch=arch),
                         APPIMAGETOOL_SHA256[arch], cache / f"appimagetool-{APPIMAGETOOL_VERSION}-{arch}.AppImage")


def fetch_runtime(arch: str, cache: Path) -> Path:
    """The pinned static type-2 runtime for ``arch`` (no libfuse2 needed where the AppImage runs)."""
    return _fetch_pinned("runtime", RUNTIME_URL.format(v=RUNTIME_VERSION, arch=arch),
                         RUNTIME_SHA256[arch], cache / f"runtime-{RUNTIME_VERSION}-{arch}")


def appimagetool_cmd(tool: Path, appdir: Path, out: Path, arch: str, runtime: Path) -> list[str]:
    return [str(tool), "--no-appstream", "--runtime-file", str(runtime), "-u", update_info(arch),
            str(appdir), str(out)]


def build(app_dir: Path, out_dir: Path, *, arch: str, version: str, cache: Path, run=subprocess.run) -> Path:
    appdir = assemble_appdir(app_dir, out_dir / f"UEFN-Ducky-{arch}.AppDir")
    out = out_dir / appimage_name(version, arch)
    out.unlink(missing_ok=True)
    tool = fetch_appimagetool(arch, cache)
    runtime = fetch_runtime(arch, cache)
    # No FUSE in CI or containers: the tool unpacks itself instead of mounting.
    env = {**os.environ, "ARCH": arch, "APPIMAGE_EXTRACT_AND_RUN": "1"}
    cmd = appimagetool_cmd(tool, appdir, out, arch, runtime)
    print(">", " ".join(cmd))
    run(cmd, check=True, env=env)
    zsync = out.with_name(out.name + ".zsync")
    if not zsync.is_file() and shutil.which("zsyncmake"):
        run(["zsyncmake", "-u", out.name, "-o", str(zsync), str(out)], check=True, cwd=str(out_dir))
    if not out.is_file() or not zsync.is_file():
        raise SystemExit(f"ERROR: appimagetool wrote no {out.name} / {zsync.name}")
    shutil.rmtree(appdir, ignore_errors=True)
    print(f"Wrote {out} ({out.stat().st_size // (1024 * 1024)} MB) + {zsync.name}\n  update info: {update_info(arch)}")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--arch", default=None, help="x86_64 or aarch64 (default: this machine)")
    p.add_argument("--app-dir", type=Path, default=ROOT / "dist" / APP_ID)
    p.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    args = p.parse_args(argv)
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "UEFN-Ducky" / "build_cache" / "appimagetool"
    build(args.app_dir, args.out_dir, arch=normalize_arch(args.arch), version=read_version(), cache=cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
