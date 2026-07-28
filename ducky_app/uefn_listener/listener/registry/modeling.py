"""Modeling registry tools: static mesh LODs, collision, Nanite."""

from __future__ import annotations

import unreal

from listener.dispatch import register


def _load_static_mesh(asset_path: str) -> unreal.StaticMesh:
    from listener.asset_resolve import load_asset_resolved

    mesh, _ = load_asset_resolved(asset_path)
    if mesh is None:
        raise ValueError(f"Static mesh not found: {asset_path}")
    if not isinstance(mesh, unreal.StaticMesh):
        raise ValueError(f"Not a StaticMesh (got {type(mesh).__name__}): {asset_path}")
    return mesh


def get_static_mesh_info(asset_path: str) -> dict:
    """Get LOD and collision summary for a static mesh asset."""
    mesh = _load_static_mesh(asset_path)
    info = {"asset_path": asset_path, "name": mesh.get_name(), "path": mesh.get_path_name()}
    try:
        sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        if sub is not None:
            info["lod_count"] = sub.get_lod_count(mesh)
            info["has_nanite"] = bool(mesh.get_editor_property("nanite_settings")) if hasattr(mesh, "get_editor_property") else None
    except Exception as e:
        info["subsystem_error"] = str(e)
    try:
        body_setup = mesh.get_editor_property("body_setup")
        if body_setup is not None:
            info["collision_trace_flag"] = str(body_setup.get_editor_property("collision_trace_flag"))
    except Exception:
        pass
    return info


def set_mesh_collision(asset_path: str, collision_preset: str = "BlockAll") -> dict:
    """Set simplified collision preset on a static mesh."""
    mesh = _load_static_mesh(asset_path)
    preset = getattr(unreal.CollisionTraceFlag, collision_preset, None)
    sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    if sub is None:
        raise RuntimeError("StaticMeshEditorSubsystem unavailable")
    try:
        sub.set_convex_decomposition_collisions(mesh, 1)
    except Exception:
        pass
    try:
        body_setup = mesh.get_editor_property("body_setup")
        if body_setup is not None and preset is not None:
            body_setup.set_editor_property("collision_trace_flag", preset)
    except Exception as e:
        raise RuntimeError(f"Failed to set collision: {e}") from e
    unreal.EditorAssetLibrary.save_loaded_asset(mesh)
    return {"asset_path": asset_path, "collision_preset": collision_preset}


register("get_static_mesh_info")(get_static_mesh_info)
register("set_mesh_collision")(set_mesh_collision)
