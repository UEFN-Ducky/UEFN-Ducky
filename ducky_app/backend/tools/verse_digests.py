"""Offline-first Verse digest reader — disk cache + decl index for AI tools.

Digests live under %LOCALAPPDATA%/UnrealEditorFortnite/Saved/VerseProject/<Project>/
and do not need the UEFN listener. Listener copies of the same tools remain as fallback.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

_DECL_RE = re.compile(
    r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>\s*)*:=\s*(class|module|interface|enum|struct)\b"
)
_CLASS_PARENT_RE = re.compile(
    r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>\s*)*:=\s*class\s*\(([^)]*)\)"
)
_EXT_METHOD_RE = re.compile(
    r"^\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*[^)]*\)\.([A-Za-z_][A-Za-z0-9_]*)\s*<"
)
_GET_API_MAX_CHARS = 24000

DIGEST_PURPOSE: dict[str, str] = {
    "Fortnite.digest.verse": (
        "Epic Fortnite / Creative gameplay API — devices, agents, playspace, items, "
        "characters, and related modules under /Fortnite.com."
    ),
    "Verse.digest.verse": (
        "Core Verse language + SceneGraph / Simulation / SpatialMath under /Verse.org."
    ),
    "UnrealEngine.digest.verse": (
        "Unreal Engine APIs exposed to Verse (math, meshes, materials helpers) under "
        "/UnrealEngine.com."
    ),
    "Assets.digest.verse": (
        "This project's custom assets as Verse identifiers — materials, meshes, "
        "prefabs (P_*), and asset-generated component classes. Regenerated after a "
        "Verse build / import."
    ),
}


@dataclass
class DeclEntry:
    name: str
    kind: str
    line: int  # 1-based
    module: str
    parents: str = ""  # raw class(...) parents, if any


@dataclass
class DigestFile:
    path: str
    mtime: float
    lines: list[str]
    decls: list[DeclEntry] = field(default_factory=list)
    # name_lower -> indices into decls
    by_name: dict[str, list[int]] = field(default_factory=dict)


# path -> DigestFile
_CACHE: dict[str, DigestFile] = {}


def clear_cache() -> None:
    """Test helper — drop the mtime cache."""
    _CACHE.clear()


def _project_name() -> str:
    for key in ("UEFN_DUCKY_PROJECT_ROOT",):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return os.path.basename(os.path.realpath(raw).rstrip("\\/"))
    try:
        from frontend.settings import PanelSettings

        root = PanelSettings.load().uefn_project_root.strip()
        if root:
            return os.path.basename(os.path.realpath(root).rstrip("\\/"))
    except Exception:
        pass
    return ""


def _project_root() -> str:
    for key in ("UEFN_DUCKY_PROJECT_ROOT",):
        raw = (os.environ.get(key) or "").strip()
        if raw and os.path.isdir(raw):
            return os.path.realpath(raw)
    try:
        from frontend.settings import PanelSettings

        root = PanelSettings.load().uefn_project_root.strip()
        if root and os.path.isdir(root):
            return os.path.realpath(root)
    except Exception:
        pass
    return ""


def _verse_project_dir(project_name: str = "") -> str:
    name = project_name or _project_name()
    if not name:
        return ""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("USERPROFILE") or ""
    if not base:
        return ""
    path = os.path.join(base, "UnrealEditorFortnite", "Saved", "VerseProject", name)
    return path if os.path.isdir(path) else ""


def discover_digest_files(
    *,
    digest_path: str = "",
    project_name: str = "",
    project_root: str = "",
    extra_roots: list[str] | None = None,
) -> list[str]:
    """Find *.digest.verse files for the configured project (or an explicit path)."""
    if digest_path:
        return [digest_path] if os.path.isfile(digest_path) else []

    roots: list[str] = []
    saved = _verse_project_dir(project_name)
    if saved:
        roots.append(saved)
    root = project_root or _project_root()
    if root:
        content = os.path.join(root, "Content")
        roots.append(content if os.path.isdir(content) else root)
    for r in extra_roots or []:
        if r:
            roots.append(r)

    candidates: list[str] = []
    for base in roots:
        if not base or not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            # Keep walk shallow — VerseProject layout is shallow; Content can be deep.
            if dirpath[len(base) :].count(os.sep) > 6:
                continue
            for fn in filenames:
                low = fn.lower()
                if low.endswith(".digest.verse") or (low.endswith(".verse") and "digest" in low):
                    candidates.append(os.path.join(dirpath, fn))
    return sorted(set(candidates))


def _purpose_for(basename: str) -> str:
    for key, blurb in DIGEST_PURPOSE.items():
        if basename.lower() == key.lower():
            return blurb
    if basename.lower().endswith("assets.digest.verse") or "assets" in basename.lower():
        return DIGEST_PURPOSE["Assets.digest.verse"]
    return "Verse digest file for this UEFN project."


def _build_index(path: str, mtime: float, lines: list[str]) -> DigestFile:
    decls: list[DeclEntry] = []
    by_name: dict[str, list[int]] = {}
    stack: list[tuple[int, str]] = []  # (indent, module_name)

    for i, line in enumerate(lines):
        m = _DECL_RE.match(line)
        if not m:
            em = _EXT_METHOD_RE.match(line)
            if em:
                indent = len(line) - len(line.lstrip())
                while stack and stack[-1][0] >= indent:
                    stack.pop()
                name = em.group(1)
                module = ".".join(s[1] for s in stack)
                idx = len(decls)
                decls.append(
                    DeclEntry(
                        name=name,
                        kind="extension_function",
                        line=i + 1,
                        module=module,
                    )
                )
                by_name.setdefault(name.lower(), []).append(idx)
            continue

        indent = len(m.group(1))
        name = m.group(2)
        kind = m.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        module = ".".join(s[1] for s in stack)
        parents = ""
        if kind == "class":
            pm = _CLASS_PARENT_RE.match(line)
            if pm:
                parents = pm.group(3).strip()
        if kind == "module":
            stack.append((indent, name))
        idx = len(decls)
        decls.append(
            DeclEntry(name=name, kind=kind, line=i + 1, module=module, parents=parents)
        )
        by_name.setdefault(name.lower(), []).append(idx)

    return DigestFile(path=path, mtime=mtime, lines=lines, decls=decls, by_name=by_name)


def load_digest(path: str) -> DigestFile | None:
    """Load (or return cached) digest; rebuilds when mtime changes."""
    if not path or not os.path.isfile(path):
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _CACHE.get(path)
    if cached is not None and cached.mtime == mtime:
        return cached
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    built = _build_index(path, mtime, lines)
    _CACHE[path] = built
    return built


def load_digests(
    *,
    digest_path: str = "",
    project_name: str = "",
    project_root: str = "",
    extra_roots: list[str] | None = None,
) -> list[DigestFile]:
    out: list[DigestFile] = []
    for path in discover_digest_files(
        digest_path=digest_path,
        project_name=project_name,
        project_root=project_root,
        extra_roots=extra_roots,
    ):
        d = load_digest(path)
        if d is not None:
            out.append(d)
    return out


def digests_available(
    *,
    digest_path: str = "",
    project_name: str = "",
    project_root: str = "",
    extra_roots: list[str] | None = None,
) -> bool:
    return bool(
        discover_digest_files(
            digest_path=digest_path,
            project_name=project_name,
            project_root=project_root,
            extra_roots=extra_roots,
        )
    )


def list_verse_digests(
    *,
    digest_path: str = "",
    project_name: str = "",
    project_root: str = "",
    extra_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Catalog every digest found with purpose blurbs and decl counts by kind."""
    digests = load_digests(
        digest_path=digest_path,
        project_name=project_name,
        project_root=project_root,
        extra_roots=extra_roots,
    )
    items = []
    for d in digests:
        counts: dict[str, int] = {}
        for decl in d.decls:
            counts[decl.kind] = counts.get(decl.kind, 0) + 1
        base = os.path.basename(d.path)
        items.append(
            {
                "path": d.path,
                "name": base,
                "mtime": d.mtime,
                "mtime_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(d.mtime)),
                "purpose": _purpose_for(base),
                "decl_counts": counts,
                "decl_total": len(d.decls),
            }
        )
    return {
        "digests": items,
        "count": len(items),
        "hint": (
            "Fortnite = Epic devices/gameplay; Verse = language + SceneGraph; "
            "UnrealEngine = engine APIs; Assets = this project's custom Verse-visible "
            "assets. Weapon/item *definitions* in the Content Browser use search_assets; "
            "digests are the Verse API + Verse-visible asset identifiers."
        ),
    }


def search_verse_digest(
    query: str,
    *,
    digest_path: str = "",
    max_results: int = 50,
    project_name: str = "",
    project_root: str = "",
    extra_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Ranked digest search: decl-name hits, then # doc hits, then substring."""
    if not query.strip():
        raise ValueError("query must not be empty")
    q = query.strip().lower()
    max_results = max(1, min(int(max_results), 500))
    digests = load_digests(
        digest_path=digest_path,
        project_name=project_name,
        project_root=project_root,
        extra_roots=extra_roots,
    )

    # rank 0 = exact decl name, 1 = decl name contains, 2 = # doc, 3 = plain line
    ranked: list[tuple[int, dict[str, Any]]] = []
    for d in digests:
        # Running enclosing module as we scan (decls are in file order).
        module_cursor = ""
        decl_i = 0
        for i, line in enumerate(d.lines):
            lineno = i + 1
            while decl_i < len(d.decls) and d.decls[decl_i].line <= lineno:
                decl = d.decls[decl_i]
                if decl.kind == "module":
                    module_cursor = f"{decl.module}.{decl.name}" if decl.module else decl.name
                elif decl.module:
                    module_cursor = decl.module
                decl_i += 1
            low = line.lower()
            if q not in low:
                continue
            strip = line.lstrip()
            m = _DECL_RE.match(line)
            if m and m.group(2).lower() == q:
                rank = 0
            elif m and q in m.group(2).lower():
                rank = 1
            elif strip.startswith("#"):
                rank = 2
            else:
                rank = 3
            ranked.append(
                (
                    rank,
                    {
                        "path": d.path,
                        "digest": os.path.basename(d.path),
                        "line": lineno,
                        "text": line.strip()[:300],
                        "module": module_cursor,
                        "rank": rank,
                    },
                )
            )

    ranked.sort(key=lambda x: (x[0], x[1]["digest"], x[1]["line"]))
    matches = [item for _, item in ranked[:max_results]]
    return {
        "query": query,
        "matches": matches,
        "count": len(matches),
        "truncated": len(ranked) > max_results,
    }


def get_verse_api(
    name: str,
    *,
    digest_path: str = "",
    max_chars: int = _GET_API_MAX_CHARS,
    project_name: str = "",
    project_root: str = "",
    extra_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Extract full digest definition block(s) for a Verse identifier."""
    q = (name or "").strip()
    if not q:
        raise ValueError("name must not be empty")
    max_chars = max(1000, min(int(max_chars), _GET_API_MAX_CHARS))
    digests = load_digests(
        digest_path=digest_path,
        project_name=project_name,
        project_root=project_root,
        extra_roots=extra_roots,
    )
    blocks = []
    total = 0
    q_lower = q.lower()
    for d in digests:
        indices = d.by_name.get(q_lower, [])
        # Also match exact case-sensitive name preference when multiple casings exist
        for idx in indices:
            decl = d.decls[idx]
            if decl.name != q and decl.name.lower() != q_lower:
                continue
            i = decl.line - 1
            line = d.lines[i]
            indent = len(line) - len(line.lstrip())
            start = i
            while start > 0:
                prev = d.lines[start - 1]
                pstrip = prev.strip()
                if pstrip.startswith("#") or pstrip.startswith("@"):
                    start -= 1
                else:
                    break
            end = i + 1
            if decl.kind != "extension_function":
                while end < len(d.lines):
                    nxt = d.lines[end]
                    if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                        break
                    end += 1
            text = "".join(d.lines[start:end]).rstrip()
            if total + len(text) > max_chars:
                text = text[: max(0, max_chars - total)] + "\n# ... (truncated)"
            blocks.append(
                {
                    "digest": os.path.basename(d.path),
                    "path": d.path,
                    "line": decl.line,
                    "kind": decl.kind,
                    "module": decl.module,
                    "definition": text,
                }
            )
            total += len(text)
            if total >= max_chars:
                return {"name": q, "matches": blocks, "count": len(blocks), "truncated": True}

    if not blocks:
        return {
            "name": q,
            "matches": [],
            "count": 0,
            "hint": (
                "No declaration found — try search_verse_digest for free-text matches, "
                "list_verse_types, or list_verse_modules."
            ),
        }
    return {"name": q, "matches": blocks, "count": len(blocks), "truncated": False}


def list_verse_modules(
    *,
    digest_path: str = "",
    project_name: str = "",
    project_root: str = "",
    extra_roots: list[str] | None = None,
) -> dict[str, Any]:
    digests = load_digests(
        digest_path=digest_path,
        project_name=project_name,
        project_root=project_root,
        extra_roots=extra_roots,
    )
    modules = []
    for d in digests:
        for decl in d.decls:
            if decl.kind != "module":
                continue
            qual = f"{decl.module}.{decl.name}" if decl.module else decl.name
            modules.append(
                {
                    "module": qual,
                    "line": decl.line,
                    "digest": os.path.basename(d.path),
                }
            )
    return {"modules": modules, "count": len(modules)}


def list_verse_types(
    *,
    kind: str = "",
    digest: str = "",
    name_filter: str = "",
    offset: int = 0,
    limit: int = 200,
    digest_path: str = "",
    project_name: str = "",
    project_root: str = "",
    extra_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Enumerate declarations (class/enum/struct/interface/function/module).

    kind: optional filter (class, enum, struct, interface, module, extension_function)
    digest: basename substring filter (e.g. 'fortnite', 'assets')
    name_filter: substring match on declaration name (e.g. '_device')
    """
    digests = load_digests(
        digest_path=digest_path,
        project_name=project_name,
        project_root=project_root,
        extra_roots=extra_roots,
    )
    kind_l = (kind or "").strip().lower()
    digest_l = (digest or "").strip().lower()
    filt = (name_filter or "").strip().lower()
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 1000))

    rows: list[dict[str, Any]] = []
    for d in digests:
        base = os.path.basename(d.path)
        if digest_l and digest_l not in base.lower():
            continue
        for decl in d.decls:
            if kind_l and decl.kind.lower() != kind_l:
                continue
            if filt and filt not in decl.name.lower():
                continue
            rows.append(
                {
                    "name": decl.name,
                    "kind": decl.kind,
                    "module": decl.module,
                    "parents": decl.parents,
                    "line": decl.line,
                    "digest": base,
                    "path": d.path,
                }
            )

    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "types": page,
        "count": len(page),
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(page) < total,
        "hint": (
            "Use get_verse_api(name) for full members/signatures. "
            "Weapon/item Content Browser assets → search_assets; digests cover Verse API "
            "+ Verse-visible custom assets (Assets.digest)."
        ),
    }


def list_verse_devices(
    *,
    digest_path: str = "",
    project_name: str = "",
    project_root: str = "",
    extra_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Device classes from the decl index (_device suffix or creative_device parent)."""
    digests = load_digests(
        digest_path=digest_path,
        project_name=project_name,
        project_root=project_root,
        extra_roots=extra_roots,
    )
    devices: list[dict[str, Any]] = []
    used_paths: list[str] = []
    for d in digests:
        used_paths.append(d.path)
        for decl in d.decls:
            if decl.kind != "class":
                continue
            name_l = decl.name.lower()
            parents_l = decl.parents.lower()
            if name_l.endswith("_device") or "creative_device" in parents_l or "device" in parents_l:
                devices.append(
                    {
                        "name": decl.name,
                        "module": decl.module,
                        "parents": decl.parents,
                        "line": decl.line,
                        "digest": os.path.basename(d.path),
                    }
                )
    devices.sort(key=lambda x: x["name"].lower())
    return {
        "devices": [d["name"] for d in devices],
        "details": devices,
        "count": len(devices),
        "digest_paths": used_paths,
    }
