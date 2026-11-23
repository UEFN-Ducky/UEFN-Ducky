"""Build engine: ship UEFN Ducky plugins compiled, with no readable Python.

``build_plugin`` compiles a plugin's backend package with Nuitka into one Windows
extension module (``uefn_plugin_<id>.v<rank>.cp313-win_amd64.pyd``) placed at the
plugin root — so the installed plugin carries no ``.py`` source — minifies web
assets with esbuild, and copies everything else as-is into a Store-ready zip.

Nuitka runs inside a one-time build kit under
``%LOCALAPPDATA%/UEFN-Ducky/build_kit`` (its own CPython plus Nuitka and the
ziglang C compiler, all from official sources). The kit is never bundled in the
installer and never imported by the frozen app — the engine only spawns the kit's
python as a subprocess, so the app needs no build packages.

Module layout (settled by a Nuitka experiment): the backend package is renamed to
``uefn_plugin_<id>`` and compiled with ``--module``. Nuitka reports each submodule's
``__file__`` as ``<pyd_dir>/uefn_plugin_<id>/<name>.py``, so placing the ``.pyd`` at
the plugin root makes ``Path(__file__).resolve().parents[1]`` resolve to the plugin
folder — exactly as the source package did. Non-Python files found inside the backend
are re-emitted under ``uefn_plugin_<id>/`` so ``__file__``-relative reads still land.

A module written across several files (:mod:`one_module`) is compiled as the one module
it is. And a build is only written once it has loaded: the finished zip is unpacked the
way an install lays it out and its backend imported in a process of its own.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from backend.uefn_plugins import one_module
from backend.uefn_plugins.plugin_host.loader import compiled_module_name
from backend.uefn_plugins.plugin_version import plugin_version_rank
from backend.uefn_plugins.signing import (
    SigningKeyError,
    pinned_public_key,
    release_public_key,
)
from backend.uefn_plugins.store import PLUGIN_MANIFEST, normalize_plugin_id

# ABI of the running interpreter — the compiled module must target the app's ABI.
PY_ABI = f"cp{sys.version_info.major}{sys.version_info.minor}-win_amd64"
MODULE_SUFFIX = ".pyd"
# Linux builds for the machine they run on (x86_64 or aarch64): Nuitka needs the target's own
# Python, so an aarch64 .so is built on an aarch64 machine (CI: an arm runner).
IS_LINUX = sys.platform.startswith("linux")
if IS_LINUX:
    from backend.uefn_plugins import manifest_platform as _mp

    LINUX_ARCH = _mp.host_arch()
    PY_ABI = _mp.python_abi(_mp.LINUX, LINUX_ARCH)
    MODULE_SUFFIX = ".so"

# Build kit — official, no-admin, pinned sources. Bump KIT_VERSION to force a rebuild.
KIT_VERSION = "1"
NUGET_PYTHON_VERSION = "3.13.16"
NUGET_PYTHON_URL = (
    "https://api.nuget.org/v3-flatcontainer/python/"
    f"{NUGET_PYTHON_VERSION}/python.{NUGET_PYTHON_VERSION}.nupkg"
)
NUGET_PYTHON_SHA256 = "95fad176338f1d6a799e45946488063f4aa61764e6cff2f132ddeffbf7a04c34"
NUITKA_SPEC = "nuitka==4.2.2"
ZIGLANG_SPEC = "ziglang==0.16.0"
ESBUILD_VERSION = "0.28.2"
ESBUILD_URL = (
    "https://registry.npmjs.org/@esbuild/win32-x64/-/"
    f"win32-x64-{ESBUILD_VERSION}.tgz"
)
ESBUILD_SHA256 = "7286c3611b6f1f4c4d9ec90adcbc478407ff0d28ead96567f361d53e67613e19"
# Linux kit: python-build-standalone (Astral's relocatable CPython, headers included) and the
# system C compiler (gcc / cc), per CPU.
PBS_TAG = "20261110"
PBS_PYTHON_VERSION = "3.13.18"
PBS_SHA256 = {
    "x86_64": "50c774f6674740739f791c05a9177ed03e2fe02abebda9bca3512eb7309b37cc",
    "aarch64": "d25a65036d5e149db902fa52ad65878d0c00e9301360ec197832f99063087e9e",
}
ESBUILD_LINUX = {  # npm package name, sha256
    "x86_64": ("linux-x64", "9573bb2233aab0f9ea7647d5cca9726113cc1768de61d66b17267f4db84488f6"),
    "aarch64": ("linux-arm64", "a96dbfa41d3ef5dbd1ef22b1c10d5187be9267e86093a870f06402a7ec931596"),
}
LINUX_BASELINE_CPU_FLAGS = {"x86_64": "-march=x86-64 -mtune=generic", "aarch64": "-march=armv8-a"}


def pbs_python_url(arch: str) -> str:
    return (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{PBS_TAG}/cpython-{PBS_PYTHON_VERSION}+{PBS_TAG}-{arch}-unknown-linux-gnu-install_only.tar.gz"
    )


def esbuild_linux_url(arch: str) -> str:
    pkg = ESBUILD_LINUX[arch][0]
    return f"https://registry.npmjs.org/@esbuild/{pkg}/-/{pkg}-{ESBUILD_VERSION}.tgz"
# zig builds for the CPU of the machine it runs on. Nuitka pins the compile step to
# baseline x86-64 but not the link, so zig's own C runtime (memcpy and friends) came
# out with AVX-512 and BMI2 from the build PC, and Ducky crashed with an illegal
# instruction (0xC000001D) on every CPU without them. Both steps get the baseline.
BASELINE_CPU_FLAGS = "-mcpu=x86_64"
# The Store refuses a bigger plugin zip (plugin-uefn-ducky-store MAX_ZIP_BYTES).
STORE_MAX_ZIP_BYTES = 32 * 1024 * 1024

# Never compiled, never shipped: repo internals and test code.
_SKIP_DIR_NAMES = frozenset(
    {".git", ".github", ".pytest_cache", "__pycache__", "scripts", "deploy", "tests", "node_modules"}
)
_SKIP_SUFFIXES = frozenset({".pyc", ".pyo", ".zip", ".bin"})
# Code run by *other* programs' Python (UEFN's listener, Blender add-ons). Compiling
# it to Ducky's ABI would make it unloadable there — leave it as source and report it.
_FOREIGN_RUNTIME_DIRS = frozenset({"listener"})
# Web assets minified with esbuild (vendored *.min.* left untouched).
_MINIFY_SUFFIXES = frozenset({".js", ".css", ".mjs"})
# The kit's python, Nuitka's compiler children and esbuild are console programs: from the
# app (which has no console) each would open its own window, and closing one stops the build.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ProgressFn = Callable[[float, str], None]


class CompileError(RuntimeError):
    """User-facing build failure."""


def _progress(fn: ProgressFn | None, frac: float, msg: str) -> None:
    if fn is not None:
        try:
            fn(max(0.0, min(1.0, frac)), msg)
        except Exception:
            pass


def store_version(raw: object) -> str:
    """The version string exactly as the Store keys and signs it: a number as written,
    a string trimmed (``-beta`` / ``+build`` kept). The baked license gate and the
    publish request must use this, or the signed record would not match the build."""
    if isinstance(raw, bool) or raw is None:
        return ""
    if isinstance(raw, int):
        return str(raw)
    return str(raw).strip()


# --------------------------------------------------------------------------- kit


def build_kit_dir() -> Path:
    from backend.skills.discovery import appdata_dir

    return appdata_dir() / "build_kit"


def _kit_python(kit: Path) -> Path:
    if IS_LINUX:
        return kit / "python" / "bin" / "python3"
    return kit / "python" / "python.exe"


def _kit_esbuild(kit: Path) -> Path:
    if IS_LINUX:
        return kit / "esbuild" / "esbuild"
    return kit / "esbuild" / "esbuild.exe"


def _kit_marker_value() -> str:
    if IS_LINUX:
        return "|".join([KIT_VERSION, "linux", LINUX_ARCH, PBS_TAG, PBS_PYTHON_VERSION, NUITKA_SPEC, ESBUILD_VERSION])
    return "|".join(
        [KIT_VERSION, NUGET_PYTHON_VERSION, NUITKA_SPEC, ZIGLANG_SPEC, ESBUILD_VERSION]
    )


def _sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download_verified(url: str, sha256: str, what: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "UEFN-Ducky"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise CompileError(f"{what} download failed (HTTP {exc.code}).") from exc
    except OSError as exc:
        raise CompileError(f"{what} download failed — check your internet connection.") from exc
    got = _sha256_of(data)
    if got != sha256:
        raise CompileError(f"{what} download was corrupted (checksum mismatch).")
    return data


def _extract_nuget_python(nupkg: bytes, dest: Path) -> None:
    """Extract the NuGet CPython ``tools/`` tree (python.exe + headers + libs) into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(nupkg)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not name.startswith("tools/"):
                continue
            rel = name[len("tools/"):]
            if not rel or ".." in Path(rel).parts:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
    if not (dest / "python.exe").is_file():
        raise CompileError("NuGet Python archive missing python.exe")


def _extract_esbuild(tgz: bytes, dest: Path, exe: str = "esbuild.exe") -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tf:
        member = None
        for m in tf.getmembers():
            if m.isfile() and m.name.replace("\\", "/").endswith("/" + exe if exe == "esbuild" else exe):
                member = m
                break
        if member is None:
            raise CompileError(f"esbuild archive missing {exe}")
        src = tf.extractfile(member)
        if src is None:
            raise CompileError("esbuild archive could not be read")
        (dest / exe).write_bytes(src.read())
    if exe == "esbuild":
        (dest / exe).chmod(0o755)


def _extract_pbs_python(tgz: bytes, dest_root: Path) -> None:
    """Unpack python-build-standalone's ``python/`` tree under ``dest_root`` (no paths outside it)."""
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tf:
        members = [m for m in tf.getmembers() if m.name.split("/", 1)[0] == "python"]
        tf.extractall(dest_root, members=members, filter="data")
    if not _kit_python(dest_root).is_file():
        raise CompileError("Python toolchain archive missing bin/python3")


def _linux_c_compiler() -> str:
    cc = os.environ.get("CC", "").strip() or shutil.which("gcc") or shutil.which("cc") or ""
    if not cc:
        raise CompileError("No C compiler found: install gcc (for example: sudo apt install gcc).")
    return cc


def _ensure_linux_kit(staging: Path, progress: ProgressFn | None) -> None:
    _progress(progress, 0.05, "Downloading Python build toolchain…")
    tgz = _download_verified(pbs_python_url(LINUX_ARCH), PBS_SHA256[LINUX_ARCH], "Python toolchain")
    _extract_pbs_python(tgz, staging)
    kpy = _kit_python(staging)
    _progress(progress, 0.45, "Installing Nuitka…")
    res = subprocess.run(
        [str(kpy), "-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check", NUITKA_SPEC],
        capture_output=True, text=True, errors="replace",
    )
    if res.returncode != 0:
        raise CompileError(f"Build kit pip install failed:\n{res.stdout}\n{res.stderr}")
    _progress(progress, 0.8, "Downloading esbuild…")
    tgz = _download_verified(esbuild_linux_url(LINUX_ARCH), ESBUILD_LINUX[LINUX_ARCH][1], "esbuild")
    _extract_esbuild(tgz, staging / "esbuild", "esbuild")


def _kit_ready(kit: Path) -> bool:
    marker = kit / ".kit-version"
    return (
        marker.is_file()
        and marker.read_text(encoding="utf-8").strip() == _kit_marker_value()
        and _kit_python(kit).is_file()
        and _kit_esbuild(kit).is_file()
    )


def ensure_build_kit(progress: ProgressFn | None = None) -> Path:
    """Return the build kit dir, downloading and assembling it once if needed.

    Idempotent: a version marker records the exact pinned sources, so an up-to-date
    kit is reused. Never bundled in the installer; lives under AppData.
    """
    kit = build_kit_dir()
    if _kit_ready(kit):
        return kit
    staging = kit.with_name(kit.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        if IS_LINUX:
            _ensure_linux_kit(staging, progress)
            (staging / ".kit-version").write_text(_kit_marker_value(), encoding="utf-8")
            shutil.rmtree(kit, ignore_errors=True)
            staging.replace(kit)
            return _linux_kit_done(kit, progress)
        _progress(progress, 0.05, "Downloading Python build toolchain…")
        nupkg = _download_verified(NUGET_PYTHON_URL, NUGET_PYTHON_SHA256, "Python toolchain")
        _extract_nuget_python(nupkg, staging / "python")
        kpy = _kit_python(staging)

        _progress(progress, 0.35, "Preparing pip…")
        subprocess.run(
            [str(kpy), "-m", "ensurepip", "--upgrade"],
            check=False, capture_output=True, text=True, errors="replace", creationflags=_NO_WINDOW,
        )
        _progress(progress, 0.45, "Installing Nuitka and the C compiler…")
        res = subprocess.run(
            [
                str(kpy), "-m", "pip", "install", "--no-warn-script-location",
                "--disable-pip-version-check", NUITKA_SPEC, ZIGLANG_SPEC,
            ],
            capture_output=True, text=True, errors="replace", creationflags=_NO_WINDOW,
        )
        if res.returncode != 0:
            raise CompileError(f"Build kit pip install failed:\n{res.stdout}\n{res.stderr}")

        _progress(progress, 0.8, "Downloading esbuild…")
        tgz = _download_verified(ESBUILD_URL, ESBUILD_SHA256, "esbuild")
        _extract_esbuild(tgz, staging / "esbuild")

        (staging / ".kit-version").write_text(_kit_marker_value(), encoding="utf-8")
        shutil.rmtree(kit, ignore_errors=True)
        staging.replace(kit)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _progress(progress, 1.0, "Build kit ready")
    if not _kit_ready(kit):
        raise CompileError("Build kit did not complete.")
    return kit


def _linux_kit_done(kit: Path, progress: ProgressFn | None) -> Path:
    _progress(progress, 1.0, "Build kit ready")
    if not _kit_ready(kit):
        raise CompileError("Build kit did not complete.")
    return kit


def _check_kit_abi(kpy: Path) -> None:
    """Fail early if the kit interpreter's major.minor differs from the app's."""
    res = subprocess.run(
        [str(kpy), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
        capture_output=True, text=True, errors="replace", creationflags=_NO_WINDOW,
    )
    kit_mm = (res.stdout or "").strip()
    app_mm = f"{sys.version_info.major}.{sys.version_info.minor}"
    if kit_mm != app_mm:
        raise CompileError(
            f"Build kit Python is {kit_mm}, this Ducky runs {app_mm} — rebuild the kit."
        )


# ------------------------------------------------------------------------- build


def _is_test_py(name: str) -> bool:
    return name == "conftest.py" or (name.startswith("test_") and name.endswith(".py"))


def shipped_files(src: Path) -> list[Path]:
    """The files a build ships (tests, scripts, dot folders, caches and archives stay behind)."""
    out = []
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(src).parts
        if any(p in _SKIP_DIR_NAMES for p in parts) or any(p.startswith(".") for p in parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES or _is_test_py(path.name):
            continue
        out.append(path)
    return out


def shipped_personal_paths(src: Path) -> list[dict[str, object]]:
    from backend.uefn_plugins.personal_paths import find_personal_paths

    return find_personal_paths(src, shipped_files(src))


def _stage_backend_package(pkg_src: Path, staged: Path) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Copy the backend package into ``staged`` for Nuitka, minus tests and foreign code.

    Returns ``(data_files, foreign_files)``:
    - ``data_files``: (source, rel-inside-package) non-Python files to re-emit under the
      compiled module's directory so ``__file__``-relative reads still resolve.
    - ``foreign_files``: (source, rel-inside-package) code left as source (listener/…).
    """
    data_files: list[tuple[Path, Path]] = []
    foreign_files: list[tuple[Path, Path]] = []
    for path in sorted(pkg_src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(pkg_src)
        parts = rel.parts
        if any(p in _SKIP_DIR_NAMES for p in parts) or any(p.startswith(".") for p in parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        if any(p in _FOREIGN_RUNTIME_DIRS for p in parts):
            foreign_files.append((path, rel))
            continue
        if path.suffix == ".py":
            if _is_test_py(path.name):
                continue
            target = staged / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        else:
            data_files.append((path, rel))
    if not (staged / "__init__.py").is_file():
        raise CompileError(f"backend entry {pkg_src} is not a package (no __init__.py)")
    return data_files, foreign_files


# First app that loads a compiled build under its own per-version module name
# (``backend.module``); an older app would look for ``PyInit_uefn_plugin_<id>`` and fail.
COMPILED_MIN_APP = "1.2.501"


def _at_least(version: str, floor: str) -> str:
    """``version`` unless it is older than ``floor`` (or missing / unreadable)."""

    def key(v: str) -> tuple[int, ...] | None:
        parts = v.strip().split("-", 1)[0].split("+", 1)[0].split(".")
        try:
            return tuple(int(p) for p in parts if p != "")
        except ValueError:
            return None

    have, need = key(version), key(floor)
    if have is None or need is None or have < need:
        return floor
    return version.strip()


_LICENSE_MODULE = "_ducky_license"


def _inject_license(
    staged_pkg: Path, pid: str, version: str, visibility: str, audience: str, pubkey: str
) -> None:
    """Bake a license gate into the staged package so it compiles into the binary.

    The gate's identity (id, version, visibility, audience) and the Store's public key are
    literals, so they live in the .pyd and cannot be edited after the build. It runs at
    import: a missing/tampered signed record, a mismatched id/version, or (private items)
    lost access to the audience makes the import fail, so the plugin refuses to register. A dev
    build with no key bakes an empty one, which skips the gate.
    """
    gate = (
        "# Generated by the Ducky build engine — baked license gate (compiled, not editable).\n"
        "from __future__ import annotations\n\n"
        f"_PUBKEY = {pubkey!r}\n"
        f"_ID = {pid!r}\n"
        f"_VERSION = {version!r}\n"
        f"_VISIBILITY = {visibility!r}\n"
        f"_AUDIENCE = {audience!r}\n\n\n"
        "def enforce() -> None:\n"
        "    import os\n\n"
        "    if os.environ.get('DUCKY_SKIP_LICENSE_GATE') == '1':\n"
        "        return\n"
        "    if not _PUBKEY:\n"
        "        return\n"
        "    from backend.uefn_plugins.signing import verify_installed_record\n\n"
        "    verify_installed_record(_ID, _VERSION, _VISIBILITY, _AUDIENCE, _PUBKEY)\n"
    )
    (staged_pkg / f"{_LICENSE_MODULE}.py").write_text(gate, encoding="utf-8")

    init_py = staged_pkg / "__init__.py"
    text = init_py.read_text(encoding="utf-8") if init_py.is_file() else ""
    lines = text.splitlines()
    # Insert the gate call after any module docstring and __future__ imports (which
    # must stay first), before the plugin's own imports.
    insert_at = 0
    i = 0
    n = len(lines)
    # Skip a leading module docstring.
    while i < n and not lines[i].strip():
        i += 1
    if i < n and lines[i].lstrip().startswith(('"""', "'''")):
        quote = lines[i].lstrip()[:3]
        rest = lines[i].lstrip()[3:]
        if rest.rstrip().endswith(quote) and len(rest.strip()) >= 3:
            i += 1  # single-line docstring
        else:
            i += 1
            while i < n and quote not in lines[i]:
                i += 1
            i += 1
    insert_at = i
    # Then skip over __future__ imports and blank lines.
    while insert_at < n and (
        not lines[insert_at].strip()
        or lines[insert_at].lstrip().startswith("from __future__")
    ):
        insert_at += 1
    inject = [
        f"from .{_LICENSE_MODULE} import enforce as _ducky_license_enforce  # build-injected",
        "_ducky_license_enforce()",
        "",
    ]
    new_lines = lines[:insert_at] + inject + lines[insert_at:]
    init_py.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _run_nuitka(kpy: Path, work: Path, mod_name: str) -> tuple[Path, list[str]]:
    """Compile ``work/<mod_name>`` to one extension module. Returns (pyd, warnings)."""
    out_dir = work / "out"
    if IS_LINUX:
        return _run_nuitka_linux(kpy, work, mod_name, out_dir)
    cmd = [
        str(kpy), "-m", "nuitka",
        "--module", mod_name,
        f"--include-package={mod_name}",
        # zig, always: on a PC with Visual Studio, Nuitka picked MSVC, which ignores
        # BASELINE_CPU_FLAGS ("D9002: unknown option -mcpu=x86_64") and links its CPU
        # probe (XSAVE xgetbv), so _assert_runs_on_every_cpu refused the build.
        "--zig",
        f"--output-dir={out_dir}",
        "--remove-output",
        "--assume-yes-for-downloads",
        "--quiet",
        "--no-progressbar",
    ]
    # A busy PC (many agents, a capped terminal) runs out of memory with one C compiler per core:
    # DUCKY_NUITKA_JOBS=2 builds with fewer at a time, in Nuitka's low-memory mode.
    jobs = os.environ.get("DUCKY_NUITKA_JOBS", "").strip()
    if jobs.isdigit() and int(jobs) > 0:
        cmd += [f"--jobs={int(jobs)}", "--low-memory"]
    env = dict(os.environ, CFLAGS=BASELINE_CPU_FLAGS, LDFLAGS=BASELINE_CPU_FLAGS)
    # --zig finds zig on PATH; without the kit's own (ZIGLANG_SPEC) there, Nuitka downloaded
    # a newer one of its own.
    zig_dir = kpy.parent / "Lib" / "site-packages" / "ziglang"
    env["PATH"] = str(zig_dir) + os.pathsep + env.get("PATH", "")
    # zig's shared cache (%LOCALAPPDATA%\zig) kept entries whose files were gone, and every
    # zig cc that hit one failed with "failed to link with LLD: FileNotFound" (Forge 1.2.10).
    # Each build gets its own cache, deleted with the work dir.
    zig_cache = work / "zig-cache"
    env["ZIG_GLOBAL_CACHE_DIR"] = str(zig_cache / "global")
    env["ZIG_LOCAL_CACHE_DIR"] = str(zig_cache / "local")
    res = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True, errors="replace", env=env, creationflags=_NO_WINDOW)
    warnings = [
        line.strip()
        for line in (res.stderr or "").splitlines()
        if line.strip() and ("WARNING" in line.upper() or "ERROR" in line.upper())
    ]
    if res.returncode != 0:
        tail = "\n".join((res.stdout or "").splitlines()[-20:] + (res.stderr or "").splitlines()[-20:])
        raise CompileError(f"Nuitka failed to compile {mod_name}:\n{tail}")
    pyds = sorted(out_dir.glob(f"{mod_name}*.pyd"))
    if not pyds:
        raise CompileError(f"Nuitka produced no .pyd for {mod_name}")
    return pyds[0], warnings


def _run_nuitka_linux(kpy: Path, work: Path, mod_name: str, out_dir: Path) -> tuple[Path, list[str]]:
    cmd = [
        str(kpy), "-m", "nuitka", "--module", mod_name, f"--include-package={mod_name}",
        f"--output-dir={out_dir}", "--remove-output", "--assume-yes-for-downloads", "--quiet", "--no-progressbar",
    ]
    jobs = os.environ.get("DUCKY_NUITKA_JOBS", "").strip()
    if jobs.isdigit() and int(jobs) > 0:
        cmd += [f"--jobs={int(jobs)}", "--low-memory"]
    flags = LINUX_BASELINE_CPU_FLAGS.get(LINUX_ARCH, "")
    env = dict(os.environ, CC=_linux_c_compiler(), CFLAGS=flags, LDFLAGS=flags)
    res = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True, errors="replace", env=env)
    warnings = [
        line.strip()
        for line in (res.stderr or "").splitlines()
        if line.strip() and ("WARNING" in line.upper() or "ERROR" in line.upper())
    ]
    if res.returncode != 0:
        tail = "\n".join((res.stdout or "").splitlines()[-20:] + (res.stderr or "").splitlines()[-20:])
        raise CompileError(f"Nuitka failed to compile {mod_name}:\n{tail}")
    built = sorted(out_dir.glob(f"{mod_name}*.so"))
    if not built:
        raise CompileError(f"Nuitka produced no .so for {mod_name}")
    return built[0], warnings


def _assert_runs_on_every_cpu(pyd: Path) -> None:
    """Refuse a build with instructions some PCs lack: loading it would close Ducky there."""
    if IS_LINUX:
        # ponytail: cpu_check reads PE images only; Linux relies on the -march baseline flags.
        # Add an ELF reader to cpu_check when a Linux build is caught with newer instructions.
        return
    from backend.uefn_plugins.cpu_check import problems

    found = problems(pyd.read_bytes())
    if found:
        raise CompileError(
            f"{pyd.name} uses instructions that some PCs don't have, so Ducky would close on them "
            "(illegal instruction). Build it only with this build engine. Found:\n" + "\n".join(found)
        )


def _esbuild_minify(esbuild: Path, infile: Path) -> tuple[bytes, str]:
    """Minify one web asset. Returns (bytes, warning). Falls back to the original on error."""
    try:
        res = subprocess.run(
            [str(esbuild), str(infile), "--minify", "--log-level=silent"],
            capture_output=True, creationflags=_NO_WINDOW,
        )
        if res.returncode == 0 and res.stdout:
            return res.stdout, ""
        return infile.read_bytes(), f"esbuild could not minify {infile.name}; shipped as-is"
    except OSError as exc:
        return infile.read_bytes(), f"esbuild failed on {infile.name} ({exc}); shipped as-is"


def _should_minify(rel: Path) -> bool:
    if rel.suffix.lower() not in _MINIFY_SUFFIXES:
        return False
    if ".min." in rel.name.lower():
        return False  # leave vendored / pre-minified assets untouched
    # Served web assets only: the ui/ tree and root-level files. Build tooling
    # (tools/, scripts/…) ships as-is so it still runs for the plugin author.
    parts = rel.parts
    return parts[0] == "ui" or len(parts) == 1


# Run in a process of its own (the same way in the installed app and from source): import the
# built backend from an unpacked copy of the zip, and say in a file how that went.
_LOAD_CHECK = """import importlib.util, json, os, sys
from importlib.machinery import ExtensionFileLoader


def say(what):
    with open(ARGS["report"], "w", encoding="utf-8") as fh:
        json.dump(what, fh)


try:  # (what the plugin prints while loading is not the build's output)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.dup2(null, 2)
except OSError:
    pass
sys.stdout = sys.stderr = open(os.devnull, "w")
os.environ["DUCKY_SKIP_LICENSE_GATE"] = "1"  # the load is checked here, not the licence
say({"started": True})
try:
    loader = ExtensionFileLoader(ARGS["name"], ARGS["pyd"])
    spec = importlib.util.spec_from_file_location(ARGS["name"], ARGS["pyd"], loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[ARGS["name"]] = module
    spec.loader.exec_module(module)
    if callable(getattr(module, ARGS["register"], None)):
        say({"ok": True})
    else:
        say({"error": "the backend has no %s()" % ARGS["register"]})
except BaseException as exc:
    say({"error": "%s: %s" % (type(exc).__name__, exc)})
os._exit(0)
"""
_LOAD_CHECK_S = 180.0


def _check_build_loads(raw_zip: bytes, work: Path, mod_name: str, pyd_name: str, register_name: str) -> tuple[str, str]:
    """Does the build load? ``(why it does not, why that could not be checked)``, both '' when it does.

    The zip is unpacked under ``work`` the way an install lays it out (the files the backend
    reads beside itself are there) and the backend imported in a process of its own, which
    has this process's import path: Ducky's own modules are there as they are in the app.
    Only what the backend itself did counts as not loading; a check that could not run says so.
    """
    home = work / "installed"
    report = work / "load-check.json"
    try:
        import multiprocessing

        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            zf.extractall(home)
        args = {"name": mod_name, "pyd": str(home / pyd_name), "register": register_name, "report": str(report)}
        proc = multiprocessing.get_context("spawn").Process(
            target=exec, args=(_LOAD_CHECK, {"ARGS": args}), name="ducky-build-load-check", daemon=True,
        )
        proc.start()
        proc.join(_LOAD_CHECK_S)
        if proc.is_alive():
            proc.terminate()
            proc.join(10)
            return "", f"loading it took over {int(_LOAD_CHECK_S)} seconds"
        code = proc.exitcode
        said = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    except Exception as exc:  # noqa: BLE001 - the check itself failed, which says nothing about the build
        return "", f"{type(exc).__name__}: {exc}"
    if not isinstance(said, dict):
        return "", f"the process that loads it did not start (exit code {code})"
    if said.get("ok"):
        return "", ""
    if said.get("error"):
        return str(said["error"]), ""
    return f"it closed the process that loaded it (exit code {code})", ""


def build_plugin(
    src_dir: str | Path,
    out_zip: str | Path,
    *,
    visibility: str = "public",
    audience: str = "",
    smoke_test: bool = True,
    release: bool = False,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Compile a plugin into a Store-ready zip with no readable Python. Returns a report.

    ``visibility`` (public, or the Store's private value) and ``audience`` are baked into
    the license gate so a private build only loads where its audience is reachable. They
    do not change what ships.
    ``smoke_test=False`` skips loading the built module — a reviewer rebuilding
    someone else's submission never runs its code. Otherwise a build that does not load
    is refused (:func:`_check_build_loads`). ``release=True`` (Store publish and
    review builds) bakes in the Store's public key — the one this app carries, else
    fetched from the Store now — and fails if neither is available: a build made
    without the key would never be license-checked. Dev builds bake whatever key this
    app carries (none in a dev run) and never fetch.
    """
    if release:
        try:
            pubkey = release_public_key()
        except SigningKeyError as exc:
            raise CompileError(str(exc)) from exc
    else:
        pubkey = pinned_public_key()
    src = Path(src_dir).resolve()
    out_zip = Path(out_zip)
    visibility = str(visibility or "").strip().lower() or "public"
    audience = str(audience or "").strip() if visibility != "public" else ""
    manifest_path = src / PLUGIN_MANIFEST
    if not manifest_path.is_file():
        raise CompileError(f"no {PLUGIN_MANIFEST} in {src}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise CompileError("plugin.json must be an object")
    pid = normalize_plugin_id(str(manifest.get("id") or ""))
    version = manifest.get("version")
    version_str = store_version(version)
    verrank = plugin_version_rank(version)
    # One module name per version: CPython never runs an extension's init twice under one
    # name, so a Store update loads its new name next to the running copy, no restart.
    mod_name = compiled_module_name(pid, verrank)
    backend_cfg = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    entry = str(backend_cfg.get("entry") or "backend").strip() or "backend"
    register_name = str(backend_cfg.get("register") or "register").strip() or "register"
    pkg_src = src / entry
    if not (pkg_src / "__init__.py").is_file():
        raise CompileError(f"backend entry {entry!r} is not a package under {src}")
    from backend.uefn_plugins.personal_paths import personal_path_message

    personal = shipped_personal_paths(src)
    if personal and release:
        # A published plugin would hand every user its author's account name and folders.
        raise CompileError(personal_path_message(personal))
    from backend.uefn_plugins.plugin_lint import check_store_ready

    store_errors, store_warnings = check_store_ready(src, manifest, pid)
    if store_errors and release:
        # Every Store build (public, a team's private one, a review): nothing the Store would reject.
        raise CompileError(" ".join(store_errors))

    _progress(progress, 0.02, "Preparing build kit…")
    kit = ensure_build_kit(progress)
    kpy = _kit_python(kit)
    _check_kit_abi(kpy)
    esbuild = _kit_esbuild(kit)

    final_pyd_name = f"{mod_name}.{PY_ABI}{MODULE_SUFFIX}"
    warnings: list[str] = ([personal_path_message(personal)] if personal else []) + store_errors + store_warnings
    try:
        from backend.uefn_plugins.plugin_lint import check_findability

        find_errors, find_warnings = check_findability(src)
        warnings.extend(find_errors + find_warnings)
    except Exception:  # noqa: BLE001 — a findability note never blocks a build
        pass
    minified: list[str] = []
    uncompiled: list[str] = []

    with tempfile.TemporaryDirectory(prefix="ducky-compile-") as td:
        work = Path(td)
        staged = work / mod_name
        _progress(progress, 0.55, "Staging backend…")
        data_files, foreign = _stage_backend_package(pkg_src, staged)
        try:
            joined = one_module.join(staged)
        except one_module.OneModuleError as exc:
            raise CompileError(f"{entry}/{exc}") from exc
        _inject_license(staged, pid, version_str, visibility, audience, pubkey)
        _progress(progress, 0.6, "Compiling backend with Nuitka…")
        pyd, nuitka_warn = _run_nuitka(kpy, work, mod_name)
        warnings.extend(nuitka_warn)
        _progress(progress, 0.75, "Checking it runs on every CPU…")
        _assert_runs_on_every_cpu(pyd)

        # Rewrite the manifest for a compiled install.
        out_manifest = dict(manifest)
        out_manifest["compiled"] = True
        out_manifest["python_abi"] = PY_ABI
        # The Store keeps a version away from apps older than this, and older apps can't
        # load compiled plugins safely, so a compiled build never asks for less.
        out_manifest["min_app_version"] = _at_least(str(manifest.get("min_app_version") or ""), COMPILED_MIN_APP)
        out_manifest["visibility"] = visibility
        if visibility != "public" and audience:
            out_manifest["audience"] = audience
        out_backend = dict(backend_cfg)
        out_backend["register"] = register_name
        out_backend["compiled"] = final_pyd_name
        out_backend["module"] = mod_name
        # entry no longer points at .py source; keep it for reference/debuggability.
        out_backend["entry"] = entry
        out_manifest["backend"] = out_backend

        _progress(progress, 0.85, "Assembling zip…")
        files: list[dict[str, Any]] = []

        def _add(zf: zipfile.ZipFile, arc: str, payload: bytes) -> None:
            zf.writestr(arc, payload)
            files.append({"path": arc, "size": len(payload)})

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            _add(zf, PLUGIN_MANIFEST, (json.dumps(out_manifest, indent=2) + "\n").encode("utf-8"))
            _add(zf, final_pyd_name, pyd.read_bytes())
            # Backend data files -> under the compiled module dir (keeps __file__ reads valid).
            for srcf, rel in data_files:
                _add(zf, "/".join((mod_name, *rel.parts)), srcf.read_bytes())
            # Foreign-runtime code -> kept as source at its original path; a later change ships it.
            for srcf, rel in foreign:
                arc = "/".join((entry, *rel.parts))
                _add(zf, arc, srcf.read_bytes())
                uncompiled.append(arc)
            # Everything outside the backend package.
            for path in sorted(src.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(src)
                parts = rel.parts
                if not parts or parts[0] == entry:
                    continue  # backend handled above
                if rel.name == PLUGIN_MANIFEST and len(parts) == 1:
                    continue  # rewritten above
                if any(p in _SKIP_DIR_NAMES for p in parts) or any(p.startswith(".") for p in parts):
                    continue
                if path.suffix.lower() in _SKIP_SUFFIXES or _is_test_py(path.name):
                    continue
                arc = "/".join(parts)
                if any(p in _FOREIGN_RUNTIME_DIRS for p in parts):
                    _add(zf, arc, path.read_bytes())
                    uncompiled.append(arc)
                elif _should_minify(rel):
                    payload, warn = _esbuild_minify(esbuild, path)
                    if warn:
                        warnings.append(warn)
                    else:
                        minified.append(arc)
                    _add(zf, arc, payload)
                else:
                    _add(zf, arc, path.read_bytes())

        raw = buf.getvalue()
        with zipfile.ZipFile(io.BytesIO(raw)) as check:
            bad = check.testzip()
            if bad:
                raise CompileError(f"built zip failed CRC for {bad}")
        if release and len(raw) > STORE_MAX_ZIP_BYTES:
            raise CompileError(
                f"The build is {len(raw) / 1048576:.1f} MB and the Store takes up to "
                f"{STORE_MAX_ZIP_BYTES // 1048576} MB: move big media out of the plugin (users fetch or make it)."
            )
        if smoke_test:
            _progress(progress, 0.93, "Checking the build loads…")
            broken, unchecked = _check_build_loads(raw, work, mod_name, final_pyd_name, register_name)
            if broken:
                raise CompileError(
                    f"The build does not load, so it was not written: {broken}. A compiled plugin has no .py "
                    "files of its own to read or run; run the plugin from source to find anything else."
                )
            if unchecked:
                warnings.append(f"the build was not checked for loading: {unchecked}")
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_zip.with_suffix(out_zip.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(out_zip)

    _progress(progress, 1.0, "Done")
    return {
        "ok": True,
        "id": pid,
        "module_name": mod_name,
        "python_abi": PY_ABI,
        "pyd": final_pyd_name,
        "version": version_str,
        "visibility": visibility,
        "audience": audience,
        "sha256": _sha256_of(out_zip.read_bytes()),
        "out": str(out_zip),
        "bytes": out_zip.stat().st_size,
        "files": files,
        "warnings": warnings,
        "minified": minified,
        "uncompiled": uncompiled,
        # Files compiled as part of another module (one module written across files).
        "joined": [f"{entry}/{rel}" for rel in joined],
        # The licence gate is live only when the Store's key was baked in.
        "license_gate": bool(pubkey),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.uefn_plugins.compile",
        description="Compile a UEFN Ducky plugin into a Store-ready zip with no .py source.",
    )
    parser.add_argument("plugin_dir", help="plugin source folder (holds plugin.json)")
    parser.add_argument("out_zip", help="output .zip path")
    parser.add_argument("--visibility", default="public",
                        help="bake the license gate for a public release or the Store's private value")
    parser.add_argument("--audience", default="", help="who a private release is for")
    parser.add_argument("--release", action="store_true",
                        help="a build that will ship: bake in the Store's signing key (fetched from "
                             "DUCKYOS_BASE_URL, default uefnducky.org) and fail if it can't be had")
    parser.add_argument("--quiet", action="store_true", help="only print the JSON report")
    args = parser.parse_args(argv)

    def _prog(frac: float, msg: str) -> None:
        if not args.quiet:
            print(f"[{frac * 100:5.1f}%] {msg}", file=sys.stderr)

    try:
        report = build_plugin(
            args.plugin_dir, args.out_zip,
            visibility=args.visibility, audience=args.audience,
            release=args.release, progress=_prog,
        )
    except CompileError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stdout)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
