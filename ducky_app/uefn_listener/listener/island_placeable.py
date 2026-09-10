"""Content Drawer placement helpers.

Cook errors on Asteria olive trees came from *how* they were placed
(``spawn_actor_from_object(StaticMesh)`` → FortStaticMeshActor), not from using
Fortnite assets. Content Drawer drag-drop places the Actor Blueprint (``_C``).
"""

from __future__ import annotations

from typing import List

# Default foliage_list_sources roots = Content Drawer Fortnite nature, not BR bake meshes.
FOLIAGE_SOURCE_FOLDERS = (
    "/Game/Creative/Environments/",
    "/Game/Creative/Sets/",
)


def _norm(path: str) -> str:
    p = (path or "").strip().replace("\\", "/").lower()
    if not p:
        return ""
    p = p.split(".")[0]
    if not p.startswith("/"):
        p = "/" + p
    return p


def is_foliage_bake_mesh(path: str) -> bool:
    """Internal BR foliage bake/HLOD meshes — not what Content Drawer drops."""
    p = _norm(path)
    return "/bakedata/" in (p + "/") or "bakedata" in p.split("/") or "/hlod/" in (p + "/")


def blueprint_candidates(path: str) -> List[str]:
    """Package / ``_C`` variants Content Drawer would spawn."""
    p = (path or "").strip()
    if not p:
        return []
    pkg = p.split(".")[0]
    name = pkg.rsplit("/", 1)[-1]
    out: List[str] = []
    if p.lower().endswith("_c"):
        out.append(p)
    out.append(f"{pkg}.{name}_C")
    out.append(f"{pkg}.{name}")
    uniq: List[str] = []
    for c in out:
        if c not in uniq:
            uniq.append(c)
    return uniq


def resolve_content_drawer_placeable(path: str):
    """Load the Actor Blueprint Content Drawer would drop, not a naked BakeData mesh."""
    import unreal

    path = (path or "").strip()
    if not path:
        raise ValueError("source path required")
    if is_foliage_bake_mesh(path):
        raise ValueError(
            f"{path} is a BR foliage BakeData mesh. Content Drawer places the "
            f"Actor Blueprint (`_C`) under /Game/Creative/Environments — spawn that, "
            f"not FortStaticMeshActor wrapping this mesh."
        )
    for cand in blueprint_candidates(path):
        try:
            cls = unreal.EditorAssetLibrary.load_blueprint_class(cand)
        except Exception:
            cls = None
        if cls is not None:
            return cls
        try:
            obj = unreal.load_object(None, cand)
        except Exception:
            obj = None
        if obj is not None and not isinstance(obj, unreal.StaticMesh):
            return obj
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is None and "." not in path:
        asset = unreal.EditorAssetLibrary.load_asset(f"{path}.{path.rsplit('/', 1)[-1]}")
    if asset is None:
        try:
            asset = unreal.load_object(None, path)
        except Exception:
            asset = None
    if asset is None:
        raise ValueError(f"Asset not found: {path}")
    return asset
