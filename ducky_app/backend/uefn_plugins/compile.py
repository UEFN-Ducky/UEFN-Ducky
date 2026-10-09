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
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
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

from backend.uefn_plugins.host import backend_module_name
from backend.uefn_plugins.plugin_version import plugin_version_rank
from backend.uefn_plugins.signing import (
    SigningKeyError,
    pinned_public_key,
    release_key_problem,
    release_public_key,
)
from backend.uefn_plugins.store import PLUGIN_MANIFEST, normalize_plugin_id

# ABI of the running interpreter — the compiled module must target the app's ABI.
PY_ABI = f"cp{sys.version_info.major}{sys.version_info.minor}-win_amd64"

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
    from backend.skills.store import appdata_dir

    return appdata_dir() / "build_kit"


def _kit_python(kit: Path) -> Path:
    return kit / "python" / "python.exe"


def _kit_esbuild(kit: Path) -> Path:
    return kit / "esbuild" / "esbuild.exe"


def _kit_marker_value() -> str:
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


def _extract_esbuild(tgz: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tf:
        member = None
        for m in tf.getmembers():
            if m.isfile() and m.name.replace("\\", "/").endswith("esbuild.exe"):
                member = m
                break
        if member is None:
            raise CompileError("esbuild archive missing esbuild.exe")
        src = tf.extractfile(member)
        if src is None:
            raise CompileError("esbuild archive could not be read")
        (dest / "esbuild.exe").write_bytes(src.read())


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
        _progress(progress, 0.05, "Downloading Python build toolchain…")
        nupkg = _download_verified(NUGET_PYTHON_URL, NUGET_PYTHON_SHA256, "Python toolchain")
        _extract_nuget_python(nupkg, staging / "python")
        kpy = _kit_python(staging)

        _progress(progress, 0.35, "Preparing pip…")
        subprocess.run(
            [str(kpy), "-m", "ensurepip", "--upgrade"],
            check=False, capture_output=True, text=True,
        )
        _progress(progress, 0.45, "Installing Nuitka and the C compiler…")
        res = subprocess.run(
            [
                str(kpy), "-m", "pip", "install", "--no-warn-script-location",
                "--disable-pip-version-check", NUITKA_SPEC, ZIGLANG_SPEC,
            ],
            capture_output=True, text=True,
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


def _check_kit_abi(kpy: Path) -> None:
    """Fail early if the kit interpreter's major.minor differs from the app's."""
    res = subprocess.run(
        [str(kpy), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
        capture_output=True, text=True,
    )
    kit_mm = (res.stdout or "").strip()
    app_mm = f"{sys.version_info.major}.{sys.version_info.minor}"
    if kit_mm != app_mm:
        raise CompileError(
            f"Build kit Python is {kit_mm}, this Ducky runs {app_mm} — rebuild the kit."
        )


# ------------------------------------------------------------------------- build


def _is_test_py(name: str) -> bool:
    return name.startswith("test_") and name.endswith(".py")


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


_LICENSE_MODULE = "_ducky_license"


def _inject_license(
    staged_pkg: Path, pid: str, version: str, visibility: str, team_id: str, pubkey: str
) -> None:
    """Bake a license gate into the staged package so it compiles into the binary.

    The gate's identity (id, version, visibility, team) and the Store's public key are
    literals, so they live in the .pyd and cannot be edited after the build. It runs at
    import: a missing/tampered signed record, a mismatched id/version, or (team items)
    lost team access makes the import fail, so the plugin refuses to register. A dev
    build with no key bakes an empty one, which skips the gate.
    """
    gate = (
        "# Generated by the Ducky build engine — baked license gate (compiled, not editable).\n"
        "from __future__ import annotations\n\n"
        f"_PUBKEY = {pubkey!r}\n"
        f"_ID = {pid!r}\n"
        f"_VERSION = {version!r}\n"
        f"_VISIBILITY = {visibility!r}\n"
        f"_TEAM_ID = {team_id!r}\n\n\n"
        "def enforce() -> None:\n"
        "    import os\n\n"
        "    if os.environ.get('DUCKY_SKIP_LICENSE_GATE') == '1':\n"
        "        return\n"
        "    if not _PUBKEY:\n"
        "        return\n"
        "    from backend.uefn_plugins.signing import verify_installed_record\n\n"
        "    verify_installed_record(_ID, _VERSION, _VISIBILITY, _TEAM_ID, _PUBKEY)\n"
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
    cmd = [
        str(kpy), "-m", "nuitka",
        "--module", mod_name,
        f"--include-package={mod_name}",
        f"--output-dir={out_dir}",
        "--remove-output",
        "--assume-yes-for-downloads",
        "--quiet",
        "--no-progressbar",
    ]
    res = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True)
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


def _esbuild_minify(esbuild: Path, infile: Path) -> tuple[bytes, str]:
    """Minify one web asset. Returns (bytes, warning). Falls back to the original on error."""
    try:
        res = subprocess.run(
            [str(esbuild), str(infile), "--minify", "--log-level=silent"],
            capture_output=True,
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


def _smoke_import(pyd: Path, mod_name: str, register_name: str) -> str:
    """Load the compiled module in a fresh app-ABI process; return '' on success else error.

    Skipped under a frozen interpreter (no ``-c``); the final build/test covers that.
    """
    if getattr(sys, "frozen", False):
        return ""
    code = (
        "import sys,importlib.util\n"
        "from importlib.machinery import ExtensionFileLoader\n"
        "name=sys.argv[1]; pyd=sys.argv[2]; reg=sys.argv[3]\n"
        "ld=ExtensionFileLoader(name,pyd)\n"
        "sp=importlib.util.spec_from_file_location(name,pyd,loader=ld)\n"
        "m=importlib.util.module_from_spec(sp); sys.modules[name]=m; sp.loader.exec_module(m)\n"
        "assert hasattr(m,reg), 'missing register(): '+reg\n"
        "print('ok')\n"
    )
    import os

    env = dict(os.environ)
    env["DUCKY_SKIP_LICENSE_GATE"] = "1"  # smoke test checks load only, not licensing
    res = subprocess.run(
        [sys.executable, "-I", "-c", code, mod_name, str(pyd), register_name],
        capture_output=True, text=True, env=env,
    )
    if res.returncode == 0 and "ok" in res.stdout:
        return ""
    return (res.stderr or res.stdout or "import failed").strip().splitlines()[-1]


def build_plugin(
    src_dir: str | Path,
    out_zip: str | Path,
    *,
    visibility: str = "public",
    team_id: str = "",
    smoke_test: bool = True,
    release: bool = False,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Compile a plugin into a Store-ready zip with no readable Python. Returns a report.

    ``visibility`` (public|team) and ``team_id`` are baked into the license gate so a
    team build only loads where that team's key is held. They do not change what ships.
    ``smoke_test=False`` skips importing the built module — a reviewer rebuilding
    someone else's submission never runs its code. ``release=True`` (Store publish and
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
    visibility = "team" if str(visibility).strip().lower() == "team" else "public"
    team_id = str(team_id or "").strip() if visibility == "team" else ""
    manifest_path = src / PLUGIN_MANIFEST
    if not manifest_path.is_file():
        raise CompileError(f"no {PLUGIN_MANIFEST} in {src}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise CompileError("plugin.json must be an object")
    pid = normalize_plugin_id(str(manifest.get("id") or ""))
    mod_name = backend_module_name(pid)
    version = manifest.get("version")
    version_str = store_version(version)
    verrank = plugin_version_rank(version)
    backend_cfg = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    entry = str(backend_cfg.get("entry") or "backend").strip() or "backend"
    register_name = str(backend_cfg.get("register") or "register").strip() or "register"
    pkg_src = src / entry
    if not (pkg_src / "__init__.py").is_file():
        raise CompileError(f"backend entry {entry!r} is not a package under {src}")

    _progress(progress, 0.02, "Preparing build kit…")
    kit = ensure_build_kit(progress)
    kpy = _kit_python(kit)
    _check_kit_abi(kpy)
    esbuild = _kit_esbuild(kit)

    final_pyd_name = f"{mod_name}.v{verrank}.{PY_ABI}.pyd"
    warnings: list[str] = []
    minified: list[str] = []
    uncompiled: list[str] = []

    with tempfile.TemporaryDirectory(prefix="ducky-compile-") as td:
        work = Path(td)
        staged = work / mod_name
        _progress(progress, 0.55, "Staging backend…")
        data_files, foreign = _stage_backend_package(pkg_src, staged)
        _inject_license(staged, pid, version_str, visibility, team_id, pubkey)
        _progress(progress, 0.6, "Compiling backend with Nuitka…")
        pyd, nuitka_warn = _run_nuitka(kpy, work, mod_name)
        warnings.extend(nuitka_warn)

        smoke = _smoke_import(pyd, mod_name, register_name) if smoke_test else ""
        if smoke:
            warnings.append(f"compiled module did not import in a test process: {smoke}")

        # Rewrite the manifest for a compiled install.
        out_manifest = dict(manifest)
        out_manifest["compiled"] = True
        out_manifest["python_abi"] = PY_ABI
        out_manifest["visibility"] = visibility
        if visibility == "team" and team_id:
            out_manifest["team_id"] = team_id
        out_backend = dict(backend_cfg)
        out_backend["register"] = register_name
        out_backend["compiled"] = final_pyd_name
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
        "team_id": team_id,
        "sha256": _sha256_of(out_zip.read_bytes()),
        "out": str(out_zip),
        "bytes": out_zip.stat().st_size,
        "files": files,
        "warnings": warnings,
        "minified": minified,
        "uncompiled": uncompiled,
        # The licence gate is live only when the Store's key was baked in.
        "license_gate": bool(pubkey),
    }


def abi_matches(manifest: dict[str, Any]) -> bool:
    """True unless the manifest pins a python_abi that differs from this interpreter."""
    abi = str(manifest.get("python_abi") or "").strip()
    return (not abi) or abi == PY_ABI


def assert_release_abi(zip_path: str | Path) -> None:
    """Refuse to package a compiled plugin into a release (installer guard).

    Raises CompileError when this build doesn't carry the Store's signing key (the
    release step that fetches it was skipped, so Store installs would go unverified),
    and on a python_abi mismatch, so a bundled compiled plugin can never ship against
    the wrong interpreter.
    """
    problem = release_key_problem()
    if problem:
        raise CompileError(problem)
    zp = Path(zip_path)
    try:
        with zipfile.ZipFile(zp) as zf:
            names = [n for n in zf.namelist() if Path(n).name == PLUGIN_MANIFEST and len(Path(n).parts) <= 2]
            if not names:
                raise CompileError(f"{zp.name}: no {PLUGIN_MANIFEST}")
            manifest = json.loads(zf.read(sorted(names, key=len)[0]).decode("utf-8"))
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise CompileError(f"{zp.name}: cannot read plugin.json ({exc})") from exc
    if not abi_matches(manifest):
        raise CompileError(
            f"{zp.name}: built for {manifest.get('python_abi')}, this app is {PY_ABI} — refusing to bundle."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.uefn_plugins.compile",
        description="Compile a UEFN Ducky plugin into a Store-ready zip with no .py source.",
    )
    parser.add_argument("plugin_dir", help="plugin source folder (holds plugin.json)")
    parser.add_argument("out_zip", help="output .zip path")
    parser.add_argument("--visibility", default="public", choices=["public", "team"],
                        help="bake the license gate for a public or team release")
    parser.add_argument("--team-id", default="", help="team id when --visibility team")
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
            visibility=args.visibility, team_id=args.team_id,
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
