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
            f"Refused: {path} is not clearly inside this project's content — "
            "remove it in the Content Browser"
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
        return {"ok": True, "kind": "asset", "removed": "", "note": "asset no longer exists"}
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
            f"Refused: could not check what references {path} ({exc}) — "
            "remove it in the Content Browser"
        ) from exc
    if referencers:
        shown = ", ".join(referencers[:5]) + ("…" if len(referencers) > 5 else "")
        raise ValueError(
            f"Refused: {path} is still used by {len(referencers)} asset(s): {shown}. "
            "Removing it would break them — relink / fixup_redirectors first, or "
            "delete it in the Content Browser if you are sure."
        )
    unreal.EditorAssetLibrary.delete_asset(path)
    log_msg(f"deleted asset {path}", "info")
    return {"ok": True, "kind": "asset", "removed": path}
