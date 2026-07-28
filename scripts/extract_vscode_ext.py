#!/usr/bin/env python3
"""Extract VS Code extension assets (grammar/snippets/theme/language server) from a
locally installed extension into UEFN-Ducky's built-in paths.

UEFN-Ducky ships **no** Epic Games proprietary assets. This script reads them from the
user's own UEFN install (or any extension they point at) and caches them into git-ignored
paths so the Verse editor and language server work locally. See docs/verse_extension.md.

The extractor is generic: it reads each extension's own ``package.json`` ``contributes``
(languages / grammars / snippets / themes) rather than hard-coded filenames, so any VS
Code language extension can be wired in via the PROFILES registry below. The one thing VS
Code doesn't standardize is how an extension launches its language server, so that path is
discovered heuristically (``bin/<platform>/*lsp*.exe`` and known names).

Usage:
    py scripts/extract_vscode_ext.py                     # all profiles, auto-detect UEFN
    py scripts/extract_vscode_ext.py --ext verse         # one profile
    py scripts/extract_vscode_ext.py --lang-only         # skip the language-server binary
    py scripts/extract_vscode_ext.py --lsp-only          # only the language-server binary
    py scripts/extract_vscode_ext.py --extension <path>  # a .vsix file or an extracted dir
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_WEB = _REPO / "ducky_app" / "frontend" / "ui_web" / "web"


@dataclass(frozen=True)
class Profile:
    """A consumer profile: which extension to source and where its assets belong."""

    name: str                       # profile key (--ext value)
    match_names: tuple[str, ...]     # package.json "name" values that satisfy this profile
    vsix_names: tuple[str, ...]      # .vsix filenames to look for in the UEFN VSCode dir
    grammar_dst_name: str            # web imports a fixed grammar filename (rename target)
    lang_dst: Path                   # dir for grammar/config/snippets/theme
    icons_dst: Path | None = None    # dir for language icons (optional)
    lsp_dst: Path | None = None      # dir for the language-server binary (optional)
    lsp_names: tuple[str, ...] = ()  # known server exe names (heuristic fallback: *lsp*.exe)


# --- Registry ---------------------------------------------------------------------------
# Add a Profile here to support another extension (e.g. URC) the same clean way.
PROFILES: dict[str, Profile] = {
    "verse": Profile(
        name="verse",
        match_names=("verse",),
        vsix_names=("Verse.vsix", "Verse-language.vsix"),
        grammar_dst_name="verse.tmGrammar.json",
        lang_dst=_WEB / "src" / "verse-editor" / "lang",
        icons_dst=_WEB / "public" / "verse-workflow",
        lsp_dst=_REPO / "ducky_app" / "frontend" / "ui_web" / "verse_editor" / "lsp" / "bin" / "win64",
        lsp_names=("verse-lsp.exe", "verse-lsp-latest.exe", "uLangServer.exe"),
    ),
}


# --- Source abstraction: a .vsix (zip, content under extension/) or an extracted dir -----
class Source:
    """Read files from a VS Code extension, whether a .vsix zip or an unpacked directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        self._base = ""
        if path.is_file() and path.suffix.lower() == ".vsix":
            self._zip = zipfile.ZipFile(path, "r")
            names = self._zip.namelist()
            self._base = "extension/" if any(n.startswith("extension/") for n in names) else ""
        elif not path.is_dir():
            raise FileNotFoundError(f"extension source not found: {path}")

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def __enter__(self) -> "Source":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def read_text(self, rel: str) -> str:
        if self._zip is not None:
            return self._zip.read(self._base + rel).decode("utf-8")
        return (self.path / rel).read_text(encoding="utf-8")

    def exists(self, rel: str) -> bool:
        if self._zip is not None:
            try:
                self._zip.getinfo(self._base + rel)
                return True
            except KeyError:
                return False
        return (self.path / rel).exists()

    def copy_to(self, rel: str, dst: Path) -> bool:
        if rel.startswith("./"):
            rel = rel[2:]
        if not self.exists(rel):
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():  # committed copies may be read-only — clear before rewriting
            try:
                dst.chmod(0o666)
                dst.unlink()
            except OSError:
                pass
        if self._zip is not None:
            with self._zip.open(self._base + rel) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
        else:
            shutil.copy2(self.path / rel, dst)
        try:
            dst.chmod(0o644)
        except OSError:
            pass
        return True

    def list_under(self, rel_dir: str) -> list[str]:
        rel_dir = rel_dir.strip("/")
        if self._zip is not None:
            pfx = self._base + rel_dir + "/"
            return [n[len(self._base):] for n in self._zip.namelist()
                    if n.startswith(pfx) and not n.endswith("/")]
        base = self.path / rel_dir
        if not base.is_dir():
            return []
        return [str(p.relative_to(self.path)).replace("\\", "/")
                for p in base.rglob("*") if p.is_file()]


# --- Locating the extension source ------------------------------------------------------
def _fortnite_vscode_dirs() -> list[Path]:
    """Candidate directories that hold Epic's bundled .vsix files."""
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
    return [d for d in dirs if d.is_dir()]


def _find_source(profile: Profile, explicit: Path | None) -> Source | None:
    if explicit is not None:
        return Source(explicit)
    # 1) A .vsix in the local UEFN install.
    for vdir in _fortnite_vscode_dirs():
        for vname in profile.vsix_names:
            vsix = vdir / vname
            if vsix.is_file():
                return Source(vsix)
    # 2) An installed/unpacked extension dir under the user's VS Code extensions.
    ext_root = Path.home() / ".vscode" / "extensions"
    if ext_root.is_dir():
        for d in ext_root.iterdir():
            pkg = d / "package.json"
            if pkg.is_file():
                try:
                    name = json.loads(pkg.read_text(encoding="utf-8")).get("name", "")
                except (OSError, json.JSONDecodeError):
                    continue
                if name in profile.match_names:
                    return Source(d)
    return None


# --- Extraction -------------------------------------------------------------------------
def _extract_lang(profile: Profile, src: Source) -> int:
    """Copy grammar/config/snippets/theme/icons using the extension's own contributes."""
    contributes = json.loads(src.read_text("package.json")).get("contributes", {})
    copied = 0

    grammars = contributes.get("grammars") or []
    if grammars and src.copy_to(grammars[0]["path"], profile.lang_dst / profile.grammar_dst_name):
        print(f"  grammar   -> {profile.lang_dst / profile.grammar_dst_name}")
        copied += 1

    languages = contributes.get("languages") or []
    cfg = next((lang.get("configuration") for lang in languages if lang.get("configuration")), None)
    if cfg:
        dst = profile.lang_dst / Path(cfg).name
        if src.copy_to(cfg, dst):
            print(f"  langcfg   -> {dst}")
            copied += 1

    snippets = contributes.get("snippets") or []
    if snippets:
        dst = profile.lang_dst / Path(snippets[0]["path"]).name
        if src.copy_to(snippets[0]["path"], dst):
            print(f"  snippets  -> {dst}")
            copied += 1

    themes = contributes.get("themes") or []
    if themes:
        dst = profile.lang_dst / Path(themes[0]["path"]).name
        if src.copy_to(themes[0]["path"], dst):
            print(f"  theme     -> {dst}")
            copied += 1

    if profile.icons_dst is not None:
        for rel in src.list_under("icons"):
            if rel.lower().endswith(".svg"):
                src.copy_to(rel, profile.icons_dst / Path(rel).name)
                copied += 1
        if copied:
            print(f"  icons     -> {profile.icons_dst}")
    return copied


def _extract_lsp(profile: Profile, src: Source) -> bool:
    if profile.lsp_dst is None:
        return True
    # Language servers aren't declared in package.json; scan bin/<platform>/.
    candidates: list[str] = []
    for plat in ("bin/Win64", "bin/win64", "bin"):
        candidates.extend(src.list_under(plat))
    exe = next((c for c in candidates if Path(c).name in profile.lsp_names), None)
    if exe is None:
        exe = next((c for c in candidates
                    if c.lower().endswith(".exe") and "lsp" in Path(c).name.lower()), None)
    if exe is None:
        print("  language server not found in bin/Win64/ — check the extension version")
        return False
    bin_dir = str(Path(exe).parent).replace("\\", "/")
    ok = False
    for rel in src.list_under(bin_dir):
        if rel.lower().endswith((".exe", ".dll")):
            if src.copy_to(rel, profile.lsp_dst / Path(rel).name):
                ok = True
    if ok:
        print(f"  lsp       -> {profile.lsp_dst}")
    return ok


def run_profile(profile: Profile, explicit: Path | None, lang: bool, lsp: bool) -> bool:
    src = _find_source(profile, explicit)
    if src is None:
        print(
            f"[{profile.name}] extension not found. Install UEFN, or pass "
            f"--extension <path-to-.vsix-or-dir>, or set UEFN_DUCKY_FORTNITE_VSCODE.",
            file=sys.stderr,
        )
        return False
    with src:
        print(f"[{profile.name}] source: {src.path}")
        ok = True
        if lang:
            ok = _extract_lang(profile, src) > 0 and ok
        if lsp:
            ok = _extract_lsp(profile, src) and ok
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ext", default="all", help="profile to extract (default: all)")
    parser.add_argument("--extension", type=Path, default=None,
                        help="explicit .vsix file or extracted extension directory")
    parser.add_argument("--lang-only", action="store_true", help="skip the language-server binary")
    parser.add_argument("--lsp-only", action="store_true", help="only the language-server binary")
    args = parser.parse_args()

    names = list(PROFILES) if args.ext == "all" else [args.ext]
    unknown = [n for n in names if n not in PROFILES]
    if unknown:
        parser.error(f"unknown profile(s): {', '.join(unknown)}. Known: {', '.join(PROFILES)}")

    lang = not args.lsp_only
    lsp = not args.lang_only
    ok = True
    for n in names:
        ok = run_profile(PROFILES[n], args.extension, lang, lsp) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
