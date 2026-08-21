"""Verse discovery registry tools: parse/search Verse digest files."""

from __future__ import annotations

import os
import re
from typing import List

import unreal

from listener.dispatch import register


def _uefn_project_name() -> str:
    """Mount name of the open UEFN project, e.g. '/MyProject/MyProject' -> 'MyProject'."""
    try:
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        pkg = world.get_package().get_name()  # "/MyProject/MyProject"
        parts = [p for p in pkg.split("/") if p]
        return parts[0] if parts else ""
    except Exception:
        return ""


def _verse_project_dir() -> str:
    """UEFN's generated Verse workspace for the open project (where the digests live).

    Layout: %LOCALAPPDATA%/UnrealEditorFortnite/Saved/VerseProject/<Project>/
      Digests/BuiltIn/Verse/Verse.digest.verse            (also legacy: Verse/…)
      Digests/BuiltIn/Fortnite/Fortnite.digest.verse
      Digests/BuiltIn/UnrealEngine/UnrealEngine.digest.verse
      Digests/<Project>-Assets/<Project>-Assets.digest.verse
    """
    name = _uefn_project_name()
    if not name:
        return ""
    base = os.environ.get("LOCALAPPDATA") or ""
    path = os.path.join(base, "UnrealEditorFortnite", "Saved", "VerseProject", name)
    return path if os.path.isdir(path) else ""


def _discover_digest_files(include_engine: bool = False) -> List[str]:
    """Digest files for the open project. ``_verse_project_dir()`` already holds
    Fortnite/UnrealEngine/Verse.org digests copied for this project — the engine
    content tree is huge and only walked when *include_engine* is explicitly set.
    """
    candidates: List[str] = []
    roots = [
        _verse_project_dir(),
        unreal.Paths.project_content_dir(),
        unreal.Paths.project_dir(),
    ]
    if include_engine and hasattr(unreal.Paths, "engine_content_dir"):
        roots.append(unreal.Paths.engine_content_dir())
    names = ("Fortnite.digest.verse", "fortnite.digest.verse")
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            if dirpath[len(root):].count(os.sep) > 6:
                continue
            for fn in filenames:
                low = fn.lower()
                if low in names or (low.endswith(".digest.verse") or (low.endswith(".verse") and "digest" in low)):
                    candidates.append(os.path.join(dirpath, fn))
    return sorted(set(candidates))


def _parse_verse_devices(text: str) -> List[str]:
    devices: set[str] = set()
    patterns = [
        r"\b([A-Z][A-Za-z0-9_]*device[A-Za-z0-9_]*)\b",
        r"\b(fort_[a-z0-9_]+)\b",
        r"\b([A-Z][A-Za-z0-9_]*_device)\b",
        r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*<public>",
    ]
    for line in text.splitlines():
        for pat in patterns:
            for m in re.finditer(pat, line, re.IGNORECASE if "fort_" in pat else 0):
                name = m.group(1)
                if len(name) > 2 and name.lower() not in ("public", "private", "class"):
                    devices.add(name)
    return sorted(devices)


def list_verse_devices(digest_path: str = "", include_engine: bool = False) -> dict:
    """List device/type names parsed from Verse digest files."""
    paths = [digest_path] if digest_path else _discover_digest_files(include_engine=include_engine)
    if digest_path and not os.path.isfile(digest_path):
        raise ValueError(f"Digest file not found: {digest_path}")
    all_devices: set[str] = set()
    used_paths = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            raise ValueError(f"Cannot read digest: {path}: {e}") from e
        all_devices.update(_parse_verse_devices(text))
        used_paths.append(path)
    return {"devices": sorted(all_devices), "count": len(all_devices), "digest_paths": used_paths}


def search_verse_digest(
    query: str, digest_path: str = "", max_results: int = 50, include_engine: bool = False
) -> dict:
    """Search Verse digest text for lines matching a keyword (compact results for AI)."""
    if not query.strip():
        raise ValueError("query must not be empty")
    q = query.strip().lower()
    paths = [digest_path] if digest_path else _discover_digest_files(include_engine=include_engine)
    matches = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if q in line.lower():
                    matches.append({"path": path, "line": i, "text": line.strip()[:300]})
                    if len(matches) >= max_results:
                        return {"query": query, "matches": matches, "count": len(matches), "truncated": True}
    return {"query": query, "matches": matches, "count": len(matches), "truncated": False}


_DECL_RE = re.compile(
    r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>\s*)*:=\s*(class|module|interface|enum|struct)\b"
)
_EXT_METHOD_RE = re.compile(r"^\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*[^)]*\)\.([A-Za-z_][A-Za-z0-9_]*)\s*<")
_GET_API_MAX_CHARS = 24000


def list_verse_modules(digest_path: str = "", include_engine: bool = False) -> dict:
    """List module names (with nesting and line spans) across the Verse digest files."""
    paths = [digest_path] if digest_path else _discover_digest_files(include_engine=include_engine)
    modules = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        stack: List[tuple] = []  # (indent, name, start_line)
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                m = _DECL_RE.match(line)
                if not m or m.group(3) != "module":
                    continue
                indent = len(m.group(1))
                while stack and stack[-1][0] >= indent:
                    stack.pop()
                qual = ".".join([s[1] for s in stack] + [m.group(2)])
                stack.append((indent, m.group(2), i))
                modules.append({"module": qual, "line": i, "digest": os.path.basename(path)})
    return {"modules": modules, "count": len(modules)}


def get_verse_api(
    name: str,
    digest_path: str = "",
    max_chars: int = _GET_API_MAX_CHARS,
    include_engine: bool = False,
) -> dict:
    """Extract full digest definition block(s) for a Verse identifier (class/module/interface/enum/function).

    This is the ground truth for the running UEFN build — exact members,
    signatures, and doc comments. Use it before writing Verse against an
    unfamiliar API (entity, component, mesh_component, a device, a generated
    prefab class from Assets.digest, ...).
    """
    q = (name or "").strip()
    if not q:
        raise ValueError("name must not be empty")
    max_chars = max(1000, min(int(max_chars), _GET_API_MAX_CHARS))
    paths = [digest_path] if digest_path else _discover_digest_files(include_engine=include_engine)
    blocks = []
    total = 0
    for path in paths:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            m = _DECL_RE.match(line)
            is_decl = bool(m and m.group(2) == q)
            em = _EXT_METHOD_RE.match(line) if not is_decl else None
            is_ext = bool(em and em.group(1) == q)
            if not (is_decl or is_ext):
                i += 1
                continue
            indent = len(line) - len(line.lstrip())
            # Pull leading comment lines attached to the declaration.
            start = i
            while start > 0:
                prev = lines[start - 1]
                pstrip = prev.strip()
                if pstrip.startswith("#") or pstrip.startswith("@"):
                    start -= 1
                else:
                    break
            end = i + 1
            if is_decl:
                while end < len(lines):
                    nxt = lines[end]
                    if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                        break
                    end += 1
            text = "".join(lines[start:end]).rstrip()
            if total + len(text) > max_chars:
                text = text[: max(0, max_chars - total)] + "\n# ... (truncated)"
            blocks.append({
                "digest": os.path.basename(path),
                "path": path,
                "line": i + 1,
                "kind": m.group(3) if is_decl else "extension_function",
                "definition": text,
            })
            total += len(text)
            if total >= max_chars:
                return {"name": q, "matches": blocks, "count": len(blocks), "truncated": True}
            i = end
    if not blocks:
        return {
            "name": q,
            "matches": [],
            "count": 0,
            "hint": "No declaration found — try search_verse_digest for free-text matches, or list_verse_modules.",
        }
    return {"name": q, "matches": blocks, "count": len(blocks), "truncated": False}


register("list_verse_devices")(list_verse_devices)
register("search_verse_digest")(search_verse_digest)
register("list_verse_modules")(list_verse_modules)
register("get_verse_api")(get_verse_api)
