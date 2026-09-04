#!/usr/bin/env python3
"""
Build frozen ``UEFN-Ducky-<version>.exe`` into ``dist/`` (repo root).

``build/`` keeps only **scripts and the spec**; intermediates (``pyinstaller-dist/``,
``unified-work/``) are gitignored. Each run: validate ``ducky_app/uefn_listener/`` → build React panel → bump app
version → PyInstaller freeze (which ships the plaintext ``uefn_listener/`` tree as ``bundle/uefn_listener``).

- Double-click → setup GUI (Apply, Deploy, Test).
- IDE spawns the same file with ``bridge --port …`` for MCP stdio (any filename — Apply uses
  ``sys.executable``).

PyInstaller writes into ``build/pyinstaller-dist/`` then copies to ``dist/UEFN-Ducky-<version>.exe``.
If that exact file is locked, writes ``UEFN-Ducky-<version>.pending.exe`` instead.

Usage (from repo root):
  py build/build_exes.py

Version: EVERY build bumps patch in ``ducky_app/frontend/__init__.py``. Output filename includes the
version (e.g. ``UEFN-Ducky-1.0.39.exe``). Rename the file freely — Settings → IDEs → Apply writes
whatever executable you are running into Cursor/Claude.

Requires: any Python 3.10+ (``py`` or ``python`` on PATH). No Python 3.11 needed — the listener
ships as plaintext and runs on UEFN's embedded 3.11 at load time.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _read_package_version(init_py: Path) -> str:
    text = init_py.read_text(encoding="utf-8")
    m = re.search(r'(?m)^(__version__\s*=\s*["\'])([^"\']+)(["\'])', text)
    if not m:
        print(f"ERROR: No __version__ assignment in {init_py}", file=sys.stderr)
        raise SystemExit(1)
    return m.group(2).strip()


def _bump_package_version(init_py: Path) -> str:
    """
    Increment patch (third segment) by 1. ``0.0.11`` → ``0.0.12``.
    Strips a legacy ``+N`` suffix before parsing (``0.0.11+10`` → next ``0.0.12``).
    """
    text = init_py.read_text(encoding="utf-8")
    m = re.search(r'(?m)^(__version__\s*=\s*["\'])([^"\']+)(["\'])', text)
    if not m:
        print(f"ERROR: No __version__ assignment in {init_py}", file=sys.stderr)
        raise SystemExit(1)
    v = m.group(2).strip()
    base = v.partition("+")[0].strip()
    parts = base.split(".")
    if len(parts) != 3:
        print(
            f"ERROR: __version__ must be MAJOR.MINOR.PATCH (optional legacy +suffix), got {v!r}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
    c += 1
    new_v = f"{a}.{b}.{c}"
    new_text = text[: m.start(2)] + new_v + text[m.end(2) :]
    init_py.write_text(new_text, encoding="utf-8")
    print(f"Bumped package version -> {new_v} ({init_py})")
    return new_v


def _validate_listener_source(listener_root: Path) -> None:
    """Refuse to freeze if ``uefn_listener/listener`` is missing or corrupt (folders named like ``*.py``).

    The exe ships the plaintext ``uefn_listener/`` tree (unified.spec → ``bundle/uefn_listener``); UEFN-Ducky.exe
    copies it to AppData on launch. No bytecode compile, no zip — plaintext runs on UEFN's 3.11.
    """
    listener = listener_root / "listener"
    if not (listener / "bootstrap.py").is_file():
        print(f"ERROR: Missing {listener / 'bootstrap.py'}", file=sys.stderr)
        raise SystemExit(1)
    for p in listener.rglob("*"):
        if p.is_dir() and p.name.endswith((".py", ".pyc")):
            print(f"ERROR: Invalid listener tree — directory named like a Python file: {p}", file=sys.stderr)
            raise SystemExit(1)


def _build_react_panel(root: Path) -> None:
    """Build ducky_app/frontend/ui_web/web/dist for PyInstaller embed."""
    web_dir = root / "ducky_app" / "frontend" / "ui_web" / "web"
    if not (web_dir / "package.json").is_file():
        print(f"ERROR: Missing {web_dir / 'package.json'}", file=sys.stderr)
        raise SystemExit(1)
    npm = shutil.which("npm")
    if not npm:
        print("ERROR: npm not found on PATH — install Node.js to build the React panel.", file=sys.stderr)
        raise SystemExit(1)
    dist = web_dir / "dist"
    if dist.is_dir():
        shutil.rmtree(dist)
        print(f"Cleaned React panel dist: {dist}")
    for cmd in ([npm, "install"], [npm, "run", "build"]):
        print(">", " ".join(cmd), f"(cwd={web_dir})")
        subprocess.run(cmd, check=True, cwd=str(web_dir))
    if not (dist / "index.html").is_file():
        print(f"ERROR: React build did not produce {dist / 'index.html'}", file=sys.stderr)
        raise SystemExit(1)
    ducky_app = root / "ducky_app"
    for p in (root, ducky_app):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    from frontend.ui_web.panel_httpd import verify_panel_dist

    try:
        verify_panel_dist(dist)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Verified React panel dist: {dist}")


def _version_info_text(app_version: str, exe_stem: str) -> str:
    """PyInstaller VSVersionInfo file (EXE(version=...) input)."""
    parts = [int(p) for p in app_version.split(".")]
    while len(parts) < 4:
        parts.append(0)
    vers = tuple(parts[:4])
    ver4 = ".".join(str(n) for n in vers)
    year = time.strftime("%Y")
    return f"""\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers!r},
    prodvers={vers!r},
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'UEFN Ducky'),
        StringStruct('FileDescription', 'UEFN Ducky - AI toolkit for Unreal Editor for Fortnite'),
        StringStruct('FileVersion', '{ver4}'),
        StringStruct('InternalName', '{exe_stem}'),
        StringStruct('LegalCopyright', '(c) {year} UEFN Ducky. All rights reserved.'),
        StringStruct('OriginalFilename', '{exe_stem}.exe'),
        StringStruct('ProductName', 'UEFN Ducky'),
        StringStruct('ProductVersion', '{ver4}'),
      ]),
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build frozen UEFN-Ducky.exe into dist/")
    p.add_argument(
        "--dev",
        action="store_true",
        help="Build UEFN-Ducky-Dev-<version>.exe (inspector on; uses Vite when localhost:5173 is up)",
    )
    p.add_argument(
        "--no-bump",
        action="store_true",
        help="Use current __version__ without incrementing (pair with release+dev in one build_all run)",
    )
    return p.parse_args()


def _stale_artifact_patterns(*, dev_build: bool) -> tuple[str, ...]:
    if dev_build:
        return ("UEFN-Ducky-Dev-*.exe", "UEFN-Ducky-Dev-*.pending.exe")
    return ("UEFN-Ducky-*.exe", "*.pending.exe")


def _is_stale_artifact(path: Path, *, dev_build: bool, keep: Path) -> bool:
    if not path.is_file() or path.resolve() == keep.resolve():
        return False
    name = path.name
    if dev_build:
        return name.startswith("UEFN-Ducky-Dev-")
    if name.startswith("UEFN-Ducky-Dev-"):
        return False
    return name.startswith("UEFN-Ducky-") or name.endswith(".pending.exe")


def main() -> int:
    args = _parse_args()
    dev_build = bool(args.dev)
    exe_stem = "UEFN-Ducky-Dev" if dev_build else "UEFN-Ducky"
    here = Path(__file__).resolve().parent
    root = here.parent
    req = root / "requirements.txt"
    spec = here / "unified.spec"
    # Prefer AppData/temp workpath — Documents/GitHub builds can lose
    # build/unified-work mid-Analysis (OneDrive/AV) → FileNotFoundError on base_library.zip.
    local_app = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    work = local_app / "UEFN-Ducky" / "pyinstaller-work" / "unified-work"
    legacy_work = here / "unified-work"
    for stale in (work, legacy_work):
        if stale.is_dir():
            try:
                shutil.rmtree(stale)
                print(f"Cleaned PyInstaller work dir: {stale}")
            except OSError as exc:
                print(f"Note: could not clean {stale} ({exc})", file=sys.stderr)

    for p in (req, spec):
        if not p.is_file():
            print(f"Missing {p}", file=sys.stderr)
            return 1

    exe_product = root / "dist" / "UEFN-Ducky-<version>.exe"
    print(f"Full build: PyInstaller will write → {exe_product}\n")

    init_py = root / "ducky_app" / "frontend" / "__init__.py"
    if not init_py.is_file():
        print(f"Missing {init_py}", file=sys.stderr)
        return 1

    _validate_listener_source(root / "ducky_app" / "uefn_listener")  # spec bundles plaintext uefn_listener/ → bundle/uefn_listener

    _build_react_panel(root)

    # Single output: dist/UEFN-Ducky.exe (version is inside the binary, not the filename).
    if args.no_bump:
        app_version = _read_package_version(init_py)
        print(f"Using package version (no bump) -> {app_version} ({init_py})")
    else:
        app_version = _bump_package_version(init_py)

    # Deps first: write_ico below needs Pillow, so this must run before it.
    pip = [sys.executable, "-m", "pip", "install", "pyinstaller", "-r", str(req)]
    print(">", " ".join(pip))
    subprocess.run(pip, check=True, cwd=str(root))

    # Branded .ico for the frozen EXE + Windows taskbar
    ducky_app = root / "ducky_app"
    for p in (root, ducky_app):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    ico_path = here / "app_icon.ico"
    try:
        from frontend.tray_icon import write_ico

        write_ico(ico_path)
        print(f"Wrote {ico_path}")
    except Exception as exc:
        print(f"ERROR: could not write app_icon.ico ({exc})", file=sys.stderr)
        return 1

    # PyInstaller icon embedding can fail silently when the path contains spaces — stage a copy.
    stage_ico = Path(tempfile.gettempdir()) / "uefn_ducky_pyinstaller.ico"
    shutil.copy2(ico_path, stage_ico)
    os.environ["UEFN_DUCKY_BUILD_ICON"] = str(stage_ico)
    os.environ["UEFN_DUCKY_EXE_BASENAME"] = exe_stem
    print(f"Staged PyInstaller icon -> {stage_ico}")
    print(f"PyInstaller EXE basename -> {exe_stem}")

    # Windows VERSIONINFO resource: EXEs with no company/product/version metadata
    # are a top trigger for Defender ML false positives (Wacatac.B!ml).
    version_file = Path(tempfile.gettempdir()) / "uefn_ducky_version_info.txt"
    version_file.write_text(_version_info_text(app_version, exe_stem), encoding="utf-8")
    os.environ["UEFN_DUCKY_BUILD_VERSION_FILE"] = str(version_file)
    print(f"Staged VERSIONINFO -> {version_file}")

    # Staging under build/ (gitignored); final EXE goes to dist/ (avoids locking a running copy).
    dist_stage = here / "pyinstaller-dist"
    dist_stage.mkdir(parents=True, exist_ok=True)
    # PyInstaller writes base_library.zip under work/<specstem>/ — ensure that
    # dir exists after our rmtree (some PyInstaller versions don't recreate it).
    (work / "unified").mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--distpath",
        str(dist_stage),
        "--workpath",
        str(work),
        str(spec),
    ]
    print(">", " ".join(cmd))
    print(f"(PyInstaller --workpath: {work}, --distpath: {dist_stage})")
    env = os.environ.copy()
    # Spec collect_submodules("backend") needs ducky_app on path in the child.
    sep = os.pathsep
    env["PYTHONPATH"] = sep.join(
        p for p in (str(ducky_app), str(root), env.get("PYTHONPATH", "")) if p
    )
    subprocess.run(cmd, check=True, cwd=str(root), env=env)

    if work.is_dir():
        try:
            shutil.rmtree(work)
            print(f"Removed PyInstaller work dir: {work}")
        except OSError as exc:
            print(f"Note: could not remove {work} ({exc}). Delete it manually if you want it gone.", file=sys.stderr)

    staged = dist_stage / f"{exe_stem}.exe"
    out_dir = root / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not staged.is_file():
        print(f"Missing PyInstaller output: {staged}", file=sys.stderr)
        return 1

    out = out_dir / f"{exe_stem}-{app_version}.exe"
    pending = out_dir / f"{exe_stem}-{app_version}.pending.exe"
    try:
        if out.is_file():
            out.unlink()
        shutil.copy2(staged, out)
        if pending.is_file():
            try:
                pending.unlink()
            except OSError:
                pass
        print(f"Wrote {out}")
    except OSError as exc:
        try:
            shutil.copy2(staged, pending)
            print(
                f"NOTE: {out.name} is in use — wrote {pending.name} instead.\n"
                f"  Close the running panel, then rename pending → {out.name}",
                file=sys.stderr,
            )
        except OSError as exc2:
            print(f"ERROR: could not write {out} ({exc}) or {pending} ({exc2}).", file=sys.stderr)
            return 1

    # Guarantee "always NEW": dist/ must contain exactly the EXE we just built. Sweep older
    # versioned builds and stale .pending files so the user can never double-click an old copy.
    stale_exes: list[Path] = []
    for pattern in _stale_artifact_patterns(dev_build=dev_build):
        stale_exes.extend(out_dir.glob(pattern))
    extra = [
        out_dir / f"{exe_stem}.exe",
        here / f"{exe_stem}.exe",
        root / "build" / "UEFN-Ducky-Windows.zip",
    ]
    if not dev_build:
        extra.append(out_dir / "UEFN-Ducky.exe")
    stale_exes.extend(p for p in extra if p.is_file())

    locked: list[Path] = []
    for stale in dict.fromkeys(stale_exes):
        if not _is_stale_artifact(stale, dev_build=dev_build, keep=out):
            continue
        try:
            stale.unlink()
            print(f"Removed old artifact {stale.name}")
        except OSError:
            locked.append(stale)

    if locked:
        names = ", ".join(p.name for p in locked)
        print(
            f"\nWARNING: could not delete {names} — it is almost certainly RUNNING.\n"
            f"  Close that app (tray → Exit all, or Task Manager) so you don't keep using the\n"
            f"  old frozen build, then delete it. The new build is {out.name}.",
            file=sys.stderr,
        )

    try:
        shutil.rmtree(dist_stage)
        print(f"Removed staging dir: {dist_stage}")
    except OSError as exc:
        print(f"Note: could not remove {dist_stage} ({exc}). Delete manually if you want it gone.", file=sys.stderr)

    remaining = sorted(p.name for p in out_dir.glob("*.exe"))
    print()
    print(f"Done. Fresh build: v{app_version}  ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    if dev_build:
        print("  Dev build: WebView inspector ON; runs Vite at http://127.0.0.1:5173 when available.")
    print(f"  {out}")
    print(f"  dist/ now contains: {remaining}")
    if remaining != [out.name]:
        print(
            "  ^ More than one .exe in dist/ — launch the one named above; the others are stale.",
            file=sys.stderr,
        )
    print()
    print(
        "Distribution: copy ONLY this .exe — no editor folder, zip, or DLLs beside it.\n"
        "  (PyInstaller unpacks embedded data under %TEMP% in _MEI… folders at runtime; that is normal.)"
    )
    print(
        "  Bytecode is packed, not encrypted; PyInstaller 6 removed optional “encryption” "
        "(it did not provide real secrecy)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
