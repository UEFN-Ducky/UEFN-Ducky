"""Locate Epic verse-lsp binary from built-in bin, cache, or Fortnite VSIX."""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

from frontend.settings import default_app_data_dir

_CACHE_DIR = default_app_data_dir() / "verse-lsp"
_BUILTIN_BIN = Path(__file__).resolve().parent / "bin" / "win64"
_LSP_NAMES = ("verse-lsp-latest.exe", "verse-lsp.exe", "uLangServer.exe")


def _vsix_candidates() -> list[Path]:
    """Local Verse.vsix locations: env override first, then standard UEFN install dirs."""
    dirs: list[Path] = []
    env = os.environ.get("UEFN_DUCKY_FORTNITE_VSCODE", "").strip()
    if env:
        dirs.append(Path(env))
    for base in (
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ):
        if base:
            dirs.append(Path(base) / "Epic Games" / "Fortnite" / "VSCode")
    return [d / name for d in dirs for name in ("Verse.vsix", "Verse-language.vsix")]


_VSIX_CANDIDATES = _vsix_candidates()


def _cache_lsp(exe: Path) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = _CACHE_DIR / exe.name
    if not dest.exists() or dest.stat().st_mtime < exe.stat().st_mtime:
        shutil.copy2(exe, dest)
    parent = exe.parent
    for dll in parent.glob("*.dll"):
        dll_dest = _CACHE_DIR / dll.name
        if not dll_dest.exists() or dll_dest.stat().st_mtime < dll.stat().st_mtime:
            shutil.copy2(dll, dll_dest)
    return dest


def _find_in_dir(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    for name in _LSP_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    for candidate in directory.rglob("verse-lsp*.exe"):
        if candidate.is_file():
            return candidate
    return None


def _extract_from_vsix(vsix_path: Path) -> Path | None:
    if not vsix_path.is_file():
        return None
    extract_dir = _CACHE_DIR / "vsix_extract"
    try:
        with zipfile.ZipFile(vsix_path, "r") as zf:
            zf.extractall(extract_dir)
    except (OSError, zipfile.BadZipFile):
        return None
    for sub in ("extension/bin/Win64", "extension/bin", "bin/Win64", "bin", "extension"):
        found = _find_in_dir(extract_dir / sub.replace("/", os.sep))
        if found:
            return _cache_lsp(found)
    return _find_in_dir(extract_dir)


def _vscode_extension_dirs() -> list[Path]:
    """Installed VS Code / Cursor extension dirs that may hold a Verse extension."""
    dirs: list[Path] = []
    for root in (
        Path.home() / ".vscode" / "extensions",
        Path.home() / ".vscode-insiders" / "extensions",
        Path.home() / ".cursor" / "extensions",
    ):
        if root.is_dir():
            dirs.extend(d for d in root.iterdir() if d.is_dir() and "verse" in d.name.lower())
    return dirs


def detect_verse_lsp() -> dict[str, object]:
    """Return {available, path, source, error}."""
    cached = _find_in_dir(_CACHE_DIR)
    if cached:
        return {"available": True, "path": str(cached), "source": "appdata_cache", "error": ""}

    builtin = _find_in_dir(_BUILTIN_BIN)
    if builtin:
        cached_path = _cache_lsp(builtin)
        return {"available": True, "path": str(cached_path), "source": "builtin_bin", "error": ""}

    for ext_dir in _vscode_extension_dirs():
        found = (
            _find_in_dir(ext_dir / "bin" / "Win64")
            or _find_in_dir(ext_dir / "bin")
            or _find_in_dir(ext_dir)
        )
        if found:
            cached_path = _cache_lsp(found)
            return {"available": True, "path": str(cached_path), "source": "vscode_ext", "error": ""}

    for vsix in _VSIX_CANDIDATES:
        found = _extract_from_vsix(vsix)
        if found:
            return {"available": True, "path": str(found), "source": "vsix", "error": ""}

    return {
        "available": False,
        "path": "",
        "source": "",
        "error": "verse-lsp not found. Install UEFN, or run: py scripts/extract_vscode_ext.py",
    }
