"""Lore changelist drop — no CheckIn, no lstat, never edit .lore packs.

``drop_paths`` is fileUnstage + fileReset(purge). Only pending adds.
Reset of a committed path / pending delete restores files onto disk.
Tests inject ``backend``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class LoreBackend(Protocol):
    def status(self, project_root: str) -> dict[str, Any]: ...
    def drop(self, project_root: str, paths: list[str]) -> dict[str, Any]: ...
    def revert_local(self, project_root: str) -> dict[str, Any]: ...


_backend: LoreBackend | None = None


def set_backend(backend: LoreBackend | None) -> None:
    global _backend
    _backend = backend


def has_lore(project_root: str) -> bool:
    root = Path(project_root or "")
    return bool(str(root)) and (root / ".lore").is_dir()


def content_rel_from_asset_path(asset_path: str) -> str:
    """``/Project/Characters/Foo`` → ``Content/Characters/Foo.uasset``."""
    raw = (asset_path or "").strip().replace("\\", "/")
    if raw.startswith("/"):
        parts = [p for p in raw.split("/") if p]
        if len(parts) >= 2:
            raw = "/".join(parts[1:])
    if raw.lower().endswith(".uasset"):
        return f"Content/{raw}" if not raw.lower().startswith("content/") else raw
    if raw.lower().startswith("content/"):
        return raw if raw.lower().endswith(".uasset") else f"{raw}.uasset"
    return f"Content/{raw}.uasset"


def _norm(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("/")


def _under(rel: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    return any(rel == p or rel.startswith(p + "/") for p in prefixes)


def drop_paths(project_root: str, paths: list[str]) -> dict[str, Any]:
    """Unstage + reset(purge) each path. No-op when the island has no ``.lore``."""
    if not has_lore(project_root):
        return {"ok": True, "skipped": "no_lore", "dropped": []}
    cleaned = [_norm(p) for p in paths if (p or "").strip()]
    if not cleaned:
        return {"ok": True, "dropped": []}
    backend = _backend or _default_backend()
    return backend.drop(project_root, cleaned)


def lore_status(project_root: str) -> dict[str, Any]:
    if not has_lore(project_root):
        return {"ok": True, "skipped": "no_lore", "paths": [], "staged": [], "untracked": []}
    backend = _backend or _default_backend()
    return backend.status(project_root)


def pending_adds(project_root: str, prefixes: list[str] | None = None) -> list[str]:
    """Paths Lore lists as ADD / untracked. Never pending deletes."""
    st = lore_status(project_root)
    prefs = [_norm(p).rstrip("/") for p in (prefixes or [])]
    seen: set[str] = set()
    out: list[str] = []
    for raw in st.get("untracked") or []:
        rel = _norm(str(raw))
        if not rel or rel in seen or not _under(rel, prefs):
            continue
        seen.add(rel)
        out.append(rel)
    return out


def drop_pending_adds(project_root: str, prefixes: list[str] | None = None) -> dict[str, Any]:
    """Lore-drop pending adds under optional prefixes. Does not reset committed trees."""
    if not has_lore(project_root):
        return {"ok": True, "skipped": "no_lore", "dropped": []}
    adds = pending_adds(project_root, prefixes)
    if not adds:
        return {"ok": True, "dropped": []}
    return drop_paths(project_root, adds)


def drop_missing_orphans(project_root: str, prefixes: list[str] | None = None) -> dict[str, Any]:
    """Drop pending adds whose files are gone. Pending deletes stay (editor delete)."""
    if not has_lore(project_root):
        return {"ok": True, "skipped": "no_lore", "dropped": []}
    root = Path(project_root)
    prefs = [_norm(p).rstrip("/") for p in (prefixes or [])]
    missing = [rel for rel in pending_adds(project_root, prefs) if not (root / rel).exists()]
    if not missing:
        return {"ok": True, "dropped": []}
    return drop_paths(project_root, missing)


def revert_local_commit(project_root: str) -> dict[str, Any]:
    """Drop an unpushed local commit (``revisionRevert`` + noCommit). Never CheckIn."""
    if not has_lore(project_root):
        return {"ok": True, "skipped": "no_lore"}
    backend = _backend or _default_backend()
    revert = getattr(backend, "revert_local", None)
    if revert is None:
        return {"ok": False, "error": "backend_no_revert"}
    return revert(project_root)


def after_asset_gone(project_root: str, asset_path: str) -> dict[str, Any]:
    """Already-gone editor asset: drop a Lore pending add only — never a pending delete."""
    rel = content_rel_from_asset_path(asset_path)
    folder = rel.rsplit("/", 1)[0] if "/" in rel else "Content"
    adds = [p for p in pending_adds(project_root, [folder]) if p == rel or p.startswith(rel + "/")]
    if not adds:
        return {"ok": True, "dropped": [], "note": "no_pending_add"}
    return drop_paths(project_root, adds)


def _default_backend() -> LoreBackend:
    from frontend.ui_web.verse_editor.urc.lore_ffi import LoreFfiBackend

    return LoreFfiBackend()
