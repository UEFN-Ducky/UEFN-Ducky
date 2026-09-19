"""Delete one unreferenced asset inside this project's content.

``delete_asset`` (agents) and ``ducky_revert_creation`` (user Revert) share this
gate. Engine / Fortnite / catalog paths and anything still referenced are refused.
"""

from __future__ import annotations

from typing import List

import unreal

from listener.logutil import log_msg
from listener.project_paths import content_root


def require_project_asset_path(asset_path: str) -> str:
    """Only this project's own content. Never Engine, Fortnite or stock assets."""
    path = (asset_path or "").strip()
    if not path.startswith("/"):
        raise ValueError(f"Refused: not an asset path: {asset_path!r}")
    try:
        root = str(content_root()).strip("/")
    except Exception:
        root = ""
    head = path.strip("/").split("/", 1)[0]
    if root and head.lower() != root.lower():
        raise ValueError(
            f"Refused: {path} is outside this project's content ({root}) — Ducky only "
            "removes what it created in your island"
        )
    if not root and head.lower() in ("engine", "game"):
        raise ValueError(
            f"Refused: {path} is not clearly inside this project's content"
        )
    return path


def _is_self_ref(ref: str, path: str) -> bool:
    a = (ref or "").split(".")[0].rstrip("/").lower()
    b = (path or "").split(".")[0].rstrip("/").lower()
    return bool(a) and a == b


def delete_unreferenced_project_asset(asset_path: str) -> dict:
    """Remove one project asset that nothing else still uses.

    Already-gone is success (nothing to do). Referencer lookup failure refuses
    rather than guessing. Self-refs from the asset registry are ignored.
    """
    path = require_project_asset_path(asset_path)
    if not unreal.EditorAssetLibrary.does_asset_exist(path):
        return {
            "ok": True,
            "kind": "asset",
            "removed": "",
            "gone": True,
            "path": path,
            "note": "asset no longer exists",
        }
    referencers: List[str] = []
    try:
        from listener.registry.assets_pipeline import get_referencers

        referencers = [
            r
            for r in list(get_referencers(path).get("referencers") or [])
            if not _is_self_ref(str(r), path)
        ]
    except Exception as exc:
        raise ValueError(
            f"Refused: could not check what references {path} ({exc})"
        ) from exc
    if referencers:
        shown = ", ".join(referencers[:5]) + ("…" if len(referencers) > 5 else "")
        raise ValueError(
            f"Refused: {path} is still used by {len(referencers)} asset(s): {shown}. "
            "Removing it would break them — relink / fixup_redirectors first. "
            "To remove a whole character folder, call delete_project_folder."
        )
    unreal.EditorAssetLibrary.delete_asset(path)
    log_msg(f"deleted asset {path}", "info")
    return {"ok": True, "kind": "asset", "removed": path}


def _inside_folder(ref: str, folder: str) -> bool:
    r = (ref or "").split(".")[0].rstrip("/").lower()
    f = folder.rstrip("/").lower()
    return bool(r) and (r == f or r.startswith(f + "/"))


def delete_project_folder(directory: str) -> dict:
    """Remove one project folder when nothing outside it still references it."""
    path = require_project_asset_path((directory or "").strip().rstrip("/"))
    listed: List[str] = []
    try:
        listed = [str(a) for a in (unreal.EditorAssetLibrary.list_assets(path, True, False) or [])]
    except Exception:
        listed = []
    external: List[str] = []
    try:
        from listener.registry.assets_pipeline import get_referencers

        for asset in listed:
            pkg = str(asset).split(".")[0]
            for ref in list(get_referencers(pkg).get("referencers") or []):
                if _is_self_ref(str(ref), pkg) or _inside_folder(str(ref), path):
                    continue
                external.append(str(ref))
    except Exception as exc:
        raise ValueError(f"Refused: could not check folder referencers ({exc})") from exc
    if external:
        shown = ", ".join(external[:5]) + ("…" if len(external) > 5 else "")
        raise ValueError(
            f"Refused: {path} is still used outside the folder by {len(external)} asset(s): {shown}"
        )
    ok = bool(unreal.EditorAssetLibrary.delete_directory(path))
    if not ok and not listed:
        return {"ok": True, "kind": "folder", "removed": path, "note": "folder already empty"}
    if not ok:
        raise ValueError(f"Refused: editor could not delete folder {path}")
    log_msg(f"deleted folder {path}", "info")
    return {"ok": True, "kind": "folder", "removed": path}
