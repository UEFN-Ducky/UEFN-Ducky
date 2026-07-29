"""Material registry tools — UEFN / Fortnite editor (2026).

Uses ``MaterialEditingLibrary`` + ``EditorAssetLibrary``. UEFN hard limits:
≤500 instructions/material, no Custom/HLSL node, prefer standard nodes.
"""

from __future__ import annotations

from typing import Any, List, Optional

import unreal

from listener import lookup
from listener.dispatch import register
from listener.serialize import serialize

# Curated short names that work in UEFN material graphs (no Custom/HLSL).
_UEFN_SAFE_EXPRESSION_SHORT = (
    "Constant",
    "Constant2Vector",
    "Constant3Vector",
    "Constant4Vector",
    "ScalarParameter",
    "VectorParameter",
    "TextureSample",
    "TextureSampleParameter2D",
    "TextureCoordinate",
    "Time",
    "Sine",
    "Cosine",
    "Add",
    "Subtract",
    "Multiply",
    "Divide",
    "Power",
    "Abs",
    "Ceil",
    "Floor",
    "Frac",
    "Fmod",
    "Clamp",
    "Min",
    "Max",
    "Lerp",
    "SmoothStep",
    "OneMinus",
    "AppendVector",
    "ComponentMask",
    "Desaturation",
    "DotProduct",
    "CrossProduct",
    "Normalize",
    "Transform",
    "TransformPosition",
    "WorldPosition",
    "ActorPositionWS",
    "CameraPositionWS",
    "PixelNormalWS",
    "VertexNormalWS",
    "ReflectionVectorWS",
    "Fresnel",
    "Panner",
    "Rotator",
    "Noise",
    "ConstantBiasScale",
    "If",
    "StaticSwitch",
    "StaticSwitchParameter",
    "StaticBool",
    "StaticBoolParameter",
    "FeatureLevelSwitch",
    "QualitySwitch",
    "ShadeModel",
    "TwoSidedSign",
    "VertexColor",
    "ObjectRadius",
    "ObjectBounds",
    "SphereMask",
    "Distance",
    "DistanceToNearestSurface",
    "DepthFade",
    "SceneTexture",
    "SceneColor",
    "SceneDepth",
    "LinearInterpolate",
    "HueShift",
    "Saturate",
)


def _load_material(asset_path: str) -> unreal.Material:
    mat = unreal.EditorAssetLibrary.load_asset(asset_path)
    if mat is None:
        raise ValueError(f"Material not found: {asset_path}")
    if not isinstance(mat, unreal.Material):
        raise ValueError(f"Not a Material (got {mat.get_class().get_name()}): {asset_path}")
    return mat


def _load_material_interface(asset_path: str):
    asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    if asset is None:
        raise ValueError(f"Material / material instance not found: {asset_path}")
    return asset


def _get_mesh_component(actor: unreal.Actor, component_name: str = ""):
    """Prefer StaticMesh, then SkeletalMesh, then any MeshComponent."""
    name = (component_name or "").strip()
    if name:
        for cls in (unreal.StaticMeshComponent, unreal.SkeletalMeshComponent, unreal.MeshComponent):
            for comp in actor.get_components_by_class(cls) or []:
                try:
                    if str(comp.get_name()) == name or str(comp.get_editor_property("component_name") or "") == name:
                        return comp
                except Exception:
                    if str(comp.get_name()) == name:
                        return comp
        raise ValueError(f"No mesh component named {name!r} on actor: {actor.get_actor_label()}")

    for cls in (unreal.StaticMeshComponent, unreal.SkeletalMeshComponent, unreal.MeshComponent):
        comps = actor.get_components_by_class(cls)
        if comps:
            return comps[0]
    raise ValueError(f"No mesh component on actor: {actor.get_actor_label()}")


def _material_expressions(mat: unreal.Material) -> list:
    return list(unreal.MaterialEditingLibrary.get_material_expressions(mat))


def _resolve_expression_class(expression_class: str):
    name = expression_class if expression_class.startswith("MaterialExpression") else f"MaterialExpression{expression_class}"
    # LinearInterpolate is often aliased as Lerp in docs.
    aliases = {
        "MaterialExpressionLerp": "MaterialExpressionLinearInterpolate",
        "MaterialExpressionSaturate": "MaterialExpressionSaturate",
    }
    name = aliases.get(name, name)
    cls = getattr(unreal, name, None)
    if cls is None and name == "MaterialExpressionSaturate":
        # Older builds expose Clamp; Saturate may be missing — callers get a clear error.
        pass
    if cls is None:
        raise ValueError(
            f"Unknown expression class: {expression_class!r} — "
            "use list_uefn_material_expression_classes or search_unreal_api('MaterialExpression')"
        )
    return name, cls


def _parse_blend_mode(raw: str):
    key = raw.strip().upper().replace(" ", "_")
    if not key.startswith("BLEND_"):
        key = f"BLEND_{key}"
    val = getattr(unreal.BlendMode, key, None)
    if val is None:
        available = sorted(n for n in dir(unreal.BlendMode) if n.startswith("BLEND_"))
        raise ValueError(f"Unknown blend_mode: {raw!r}. Available: {available}")
    return key, val


def _parse_shading_model(raw: str):
    key = raw.strip().upper().replace(" ", "_")
    if not key.startswith("MSM_"):
        key = f"MSM_{key}"
    val = getattr(unreal.MaterialShadingModel, key, None)
    if val is None:
        available = sorted(n for n in dir(unreal.MaterialShadingModel) if n.startswith("MSM_"))
        raise ValueError(f"Unknown shading_model: {raw!r}. Available: {available}")
    return key, val


def create_material(
    asset_name: str,
    folder: str = "/Game/Materials",
    base_color: Optional[List[float]] = None,
    two_sided: bool = False,
    blend_mode: str = "",
) -> dict:
    unreal.EditorAssetLibrary.make_directory(folder)
    full = f"{folder.rstrip('/')}/{asset_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        unreal.EditorAssetLibrary.delete_asset(full)
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    mat = asset_tools.create_asset(asset_name, folder, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    unreal.MaterialEditingLibrary.delete_all_material_expressions(mat)
    if two_sided:
        try:
            mat.set_editor_property("two_sided", True)
        except Exception:
            pass
    if blend_mode:
        _, bm = _parse_blend_mode(blend_mode)
        try:
            mat.set_editor_property("blend_mode", bm)
        except Exception:
            pass
    if base_color and len(base_color) >= 3:
        r, g, b = float(base_color[0]), float(base_color[1]), float(base_color[2])
        const = unreal.MaterialEditingLibrary.create_material_expression(
            mat, unreal.MaterialExpressionConstant3Vector, 0, 0
        )
        const.set_editor_property("constant", unreal.LinearColor(r, g, b, 1.0))
        unreal.MaterialEditingLibrary.connect_material_property(
            const, "", unreal.MaterialProperty.MP_BASE_COLOR
        )
    unreal.MaterialEditingLibrary.recompile_material(mat)
    mat.modify(True)
    unreal.EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=False)
    return {"material_path": str(mat.get_path_name()), "asset_name": asset_name, "folder": folder}


def connect_material_nodes(
    material_path: str,
    from_index: int,
    from_output: str,
    to_index: int,
    to_input: str,
) -> dict:
    mat = _load_material(material_path)
    exprs = _material_expressions(mat)
    if from_index < 0 or from_index >= len(exprs) or to_index < 0 or to_index >= len(exprs):
        raise ValueError(f"Expression index out of range (have {len(exprs)} expressions)")
    unreal.MaterialEditingLibrary.connect_material_expressions(
        exprs[from_index], from_output or "", exprs[to_index], to_input or ""
    )
    return {"material_path": material_path, "from_index": from_index, "to_index": to_index}


def disconnect_material_nodes(
    material_path: str,
    expression_index: int,
    input_name: str = "",
) -> dict:
    """Disconnect an expression input (empty input_name = all inputs on that node when supported)."""
    mat = _load_material(material_path)
    exprs = _material_expressions(mat)
    if expression_index < 0 or expression_index >= len(exprs):
        raise ValueError(f"Expression index out of range (have {len(exprs)} expressions)")
    expr = exprs[expression_index]
    fn = getattr(unreal.MaterialEditingLibrary, "disconnect_material_expression", None)
    if not callable(fn):
        raise ValueError("disconnect_material_expression is not exposed in this UEFN build")
    fn(expr, input_name or "")
    return {
        "material_path": material_path,
        "expression_index": int(expression_index),
        "input_name": input_name or "",
    }


def set_material_instance_scalar(material_instance_path: str, param_name: str, value: float) -> dict:
    mi = _load_material_interface(material_instance_path)
    unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
        mi, unreal.Name(param_name), float(value)
    )
    unreal.EditorAssetLibrary.save_loaded_asset(mi)
    return {"material_instance_path": material_instance_path, "param_name": param_name, "value": float(value)}


def set_material_instance_vector(material_instance_path: str, param_name: str, color: List[float]) -> dict:
    mi = _load_material_interface(material_instance_path)
    if len(color) < 3:
        raise ValueError("color must have at least [r,g,b]")
    a = float(color[3]) if len(color) > 3 else 1.0
    linear = unreal.LinearColor(float(color[0]), float(color[1]), float(color[2]), a)
    unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(
        mi, unreal.Name(param_name), linear
    )
    unreal.EditorAssetLibrary.save_loaded_asset(mi)
    return {"material_instance_path": material_instance_path, "param_name": param_name, "color": color}


def set_material_instance_texture(material_instance_path: str, param_name: str, texture_path: str) -> dict:
    mi = _load_material_interface(material_instance_path)
    tex = unreal.EditorAssetLibrary.load_asset(texture_path)
    if tex is None:
        raise ValueError(f"Texture not found: {texture_path}")
    unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(
        mi, unreal.Name(param_name), tex
    )
    unreal.EditorAssetLibrary.save_loaded_asset(mi)
    return {
        "material_instance_path": material_instance_path,
        "param_name": param_name,
        "texture_path": texture_path,
    }


def recompile_material(material_path: str) -> dict:
    mat = _load_material(material_path)
    unreal.MaterialEditingLibrary.recompile_material(mat)
    mat.modify(True)
    saved = unreal.EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=False)
    return {"material_path": material_path, "saved": bool(saved)}


def assign_material_to_mesh(
    actor_path: str,
    material_path: str,
    slot_index: int = 0,
    component_name: str = "",
) -> dict:
    actor = lookup.require_actor(actor_path)
    mesh = _get_mesh_component(actor, component_name)
    mat = unreal.EditorAssetLibrary.load_asset(material_path)
    if mat is None:
        raise ValueError(f"Material not found: {material_path}")
    mesh.set_material(int(slot_index), mat)
    return {
        "actor_path": actor.get_path_name(),
        "actor_label": actor.get_actor_label(),
        "component": str(mesh.get_name()),
        "component_class": mesh.get_class().get_name(),
        "material_path": material_path,
        "slot_index": int(slot_index),
    }


def list_material_expressions(material_path: str) -> dict:
    mat = _load_material(material_path)
    exprs = _material_expressions(mat)
    items = []
    for i, expr in enumerate(exprs):
        items.append(
            {
                "index": i,
                "class": expr.get_class().get_name() if hasattr(expr, "get_class") else type(expr).__name__,
                "path": serialize(expr),
            }
        )
    return {"material_path": material_path, "expressions": items, "count": len(items)}


def create_material_instance(asset_name: str, parent_material_path: str, folder: str = "/Game/Materials") -> dict:
    """Create a MaterialInstanceConstant with the given parent (replaces an existing asset of the same name)."""
    parent = _load_material_interface(parent_material_path)
    unreal.EditorAssetLibrary.make_directory(folder)
    full = f"{folder.rstrip('/')}/{asset_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        unreal.EditorAssetLibrary.delete_asset(full)
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    mi = asset_tools.create_asset(
        asset_name, folder, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew()
    )
    if mi is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    unreal.MaterialEditingLibrary.set_material_instance_parent(mi, parent)
    unreal.EditorAssetLibrary.save_loaded_asset(mi, only_if_is_dirty=False)
    return {"material_instance_path": str(mi.get_path_name()), "parent": parent_material_path, "folder": folder}


def add_material_expression(material_path: str, expression_class: str, pos_x: int = 0, pos_y: int = 0) -> dict:
    """Add one expression node (e.g. 'Multiply' or 'MaterialExpressionSine'); recompile_material when the graph is done."""
    mat = _load_material(material_path)
    name, cls = _resolve_expression_class(expression_class)
    expr = unreal.MaterialEditingLibrary.create_material_expression(mat, cls, int(pos_x), int(pos_y))
    if expr is None:
        raise RuntimeError(f"create_material_expression returned None for {name}")
    exprs = _material_expressions(mat)
    index = len(exprs) - 1
    for i, e in enumerate(exprs):
        if e == expr:
            index = i
            break
    return {"material_path": material_path, "class": name, "index": index}


def delete_material_expression(material_path: str, index: int) -> dict:
    """Delete one expression node by index — indices SHIFT after a delete, re-run list_material_expressions."""
    mat = _load_material(material_path)
    exprs = _material_expressions(mat)
    if index < 0 or index >= len(exprs):
        raise ValueError(f"Expression index out of range (have {len(exprs)} expressions)")
    unreal.MaterialEditingLibrary.delete_material_expression(mat, exprs[index])
    return {"material_path": material_path, "deleted_index": int(index), "remaining": len(exprs) - 1}


def clear_material_expressions(material_path: str) -> dict:
    """Delete every expression node on a material (blank graph)."""
    mat = _load_material(material_path)
    before = len(_material_expressions(mat))
    unreal.MaterialEditingLibrary.delete_all_material_expressions(mat)
    return {"material_path": material_path, "deleted": before, "remaining": 0}


def set_material_expression_property(material_path: str, index: int, property_name: str, value: Any) -> dict:
    """Set an editor property on an expression node; float lists auto-try LinearColor/Vector wrappers."""
    mat = _load_material(material_path)
    exprs = _material_expressions(mat)
    if index < 0 or index >= len(exprs):
        raise ValueError(f"Expression index out of range (have {len(exprs)} expressions)")
    expr = exprs[index]
    candidates: List[Any] = [value]
    if isinstance(value, (list, tuple)) and value and all(isinstance(v, (int, float)) for v in value):
        vals = [float(v) for v in value]
        if len(vals) >= 3:
            a = vals[3] if len(vals) > 3 else 1.0
            candidates.append(unreal.LinearColor(vals[0], vals[1], vals[2], a))
            candidates.append(unreal.Vector(vals[0], vals[1], vals[2]))
        elif len(vals) == 2:
            candidates.append(unreal.Vector2D(vals[0], vals[1]))
    if isinstance(value, str):
        candidates.append(unreal.Name(value))
    errors: List[str] = []
    for cand in candidates:
        try:
            expr.set_editor_property(property_name, cand)
            return {
                "material_path": material_path,
                "index": int(index),
                "property_name": property_name,
                "applied_as": type(cand).__name__,
            }
        except Exception as e:
            errors.append(str(e))
    raise ValueError(
        f"Could not set {property_name!r} on {expr.get_class().get_name()}: {errors[-1]} "
        "(get_material_expression_info shows the node's properties)"
    )


def get_material_expression_info(material_path: str, index: int) -> dict:
    """Dump one expression node's class and editor properties (capped)."""
    mat = _load_material(material_path)
    exprs = _material_expressions(mat)
    if index < 0 or index >= len(exprs):
        raise ValueError(f"Expression index out of range (have {len(exprs)} expressions)")
    expr = exprs[index]
    props: dict = {}
    for name in dir(expr):
        if name.startswith("_") or len(props) >= 40:
            continue
        try:
            if callable(getattr(type(expr), name, None)):
                continue
            props[name] = str(serialize(expr.get_editor_property(name)))[:200]
        except Exception:
            continue
    return {
        "material_path": material_path,
        "index": int(index),
        "class": expr.get_class().get_name(),
        "properties": props,
    }


def connect_material_output(material_path: str, from_index: int, from_output: str, material_property: str) -> dict:
    """Connect a node's output to a material output pin ('base_color', 'emissive_color', ...)."""
    mat = _load_material(material_path)
    exprs = _material_expressions(mat)
    if from_index < 0 or from_index >= len(exprs):
        raise ValueError(f"Expression index out of range (have {len(exprs)} expressions)")
    key = material_property.strip().upper()
    if not key.startswith("MP_"):
        key = f"MP_{key}"
    prop = getattr(unreal.MaterialProperty, key, None)
    if prop is None:
        available = sorted(n for n in dir(unreal.MaterialProperty) if n.startswith("MP_"))
        raise ValueError(f"Unknown material output: {material_property!r}. Available: {available}")
    unreal.MaterialEditingLibrary.connect_material_property(exprs[from_index], from_output or "", prop)
    return {"material_path": material_path, "from_index": int(from_index), "material_property": key}


def set_material_flags(
    material_path: str,
    two_sided: Optional[bool] = None,
    blend_mode: str = "",
    shading_model: str = "",
) -> dict:
    """Set common Material flags (two_sided, blend_mode, shading_model). Call recompile_material after."""
    mat = _load_material(material_path)
    applied: dict[str, Any] = {}
    if two_sided is not None:
        mat.set_editor_property("two_sided", bool(two_sided))
        applied["two_sided"] = bool(two_sided)
    if blend_mode:
        key, bm = _parse_blend_mode(blend_mode)
        mat.set_editor_property("blend_mode", bm)
        applied["blend_mode"] = key
    if shading_model:
        key, sm = _parse_shading_model(shading_model)
        mat.set_editor_property("shading_model", sm)
        applied["shading_model"] = key
    mat.modify(True)
    return {"material_path": material_path, "applied": applied}


def get_material_info(material_path: str) -> dict:
    """Read a material or material-instance summary: flags, parent, parameter names, expression count."""
    asset = _load_material_interface(material_path)
    info: dict = {
        "material_path": material_path,
        "class": asset.get_class().get_name(),
        "uefn_limits": {
            "max_instructions": 500,
            "custom_hlsl_node": False,
            "prefer_standard_nodes": True,
        },
    }
    for prop in (
        "blend_mode",
        "two_sided",
        "shading_model",
        "material_domain",
        "decal_blend_mode",
        "opacity_mask_clip_value",
        "used_with_skeletal_mesh",
        "used_with_static_lighting",
        "used_with_particle_sprites",
        "used_with_niagara_sprites",
        "used_with_niagara_mesh_particles",
        "used_with_instanced_static_meshes",
        "num_customized_u_vs",
    ):
        try:
            info[prop] = str(serialize(asset.get_editor_property(prop)))
        except Exception:
            continue
    try:
        info["expression_count"] = len(_material_expressions(asset))
    except Exception:
        pass
    try:
        parent = asset.get_editor_property("parent")
        info["parent"] = str(parent.get_path_name()) if parent else None
    except Exception:
        pass
    lib = unreal.MaterialEditingLibrary
    for kind, getter in (
        ("scalar", "get_scalar_parameter_names"),
        ("vector", "get_vector_parameter_names"),
        ("texture", "get_texture_parameter_names"),
        ("static_switch", "get_static_switch_parameter_names"),
    ):
        fn = getattr(lib, getter, None)
        if callable(fn):
            try:
                info[f"{kind}_parameters"] = [str(n) for n in fn(asset)][:60]
            except Exception:
                continue
    return info


def layout_material_expressions(material_path: str) -> dict:
    """Auto-arrange a material's expression nodes in the graph."""
    mat = _load_material(material_path)
    fn = getattr(unreal.MaterialEditingLibrary, "layout_material_expressions", None)
    if not callable(fn):
        raise ValueError("layout_material_expressions is not exposed in this UEFN build")
    fn(mat)
    return {"material_path": material_path, "expression_count": len(_material_expressions(mat))}


def list_uefn_material_expression_classes() -> dict:
    """UEFN-safe MaterialExpression short names present in this editor build."""
    available: list[str] = []
    missing: list[str] = []
    for short in _UEFN_SAFE_EXPRESSION_SHORT:
        name = f"MaterialExpression{short}"
        if getattr(unreal, name, None) is not None:
            available.append(short)
        else:
            missing.append(short)
    return {
        "available": sorted(set(available)),
        "missing_in_this_build": sorted(set(missing)),
        "notes": [
            "No Custom/HLSL node in UEFN — use these standard nodes only.",
            "≤500 instructions per material (Epic).",
            "Prefer MF_QualitySwitch_Material_Attributes for cross-platform look.",
        ],
    }


def duplicate_material(source_path: str, asset_name: str, folder: str = "/Game/Materials") -> dict:
    """Duplicate a material or material instance asset."""
    if not unreal.EditorAssetLibrary.does_asset_exist(source_path):
        raise ValueError(f"Source not found: {source_path}")
    unreal.EditorAssetLibrary.make_directory(folder)
    dest = f"{folder.rstrip('/')}/{asset_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(dest):
        unreal.EditorAssetLibrary.delete_asset(dest)
    ok = unreal.EditorAssetLibrary.duplicate_asset(source_path, dest)
    if not ok:
        raise RuntimeError(f"duplicate_asset failed: {source_path} -> {dest}")
    return {"source_path": source_path, "material_path": dest, "asset_name": asset_name, "folder": folder}


register("create_material")(create_material)
register("connect_material_nodes")(connect_material_nodes)
register("disconnect_material_nodes")(disconnect_material_nodes)
register("set_material_instance_scalar")(set_material_instance_scalar)
register("set_material_instance_vector")(set_material_instance_vector)
register("set_material_instance_texture")(set_material_instance_texture)
register("recompile_material")(recompile_material)
register("assign_material_to_mesh")(assign_material_to_mesh)
register("list_material_expressions")(list_material_expressions)
register("create_material_instance")(create_material_instance)
register("add_material_expression")(add_material_expression)
register("delete_material_expression")(delete_material_expression)
register("clear_material_expressions")(clear_material_expressions)
register("set_material_expression_property")(set_material_expression_property)
register("get_material_expression_info")(get_material_expression_info)
register("connect_material_output")(connect_material_output)
register("set_material_flags")(set_material_flags)
register("get_material_info")(get_material_info)
register("layout_material_expressions")(layout_material_expressions)
register("list_uefn_material_expression_classes")(list_uefn_material_expression_classes)
register("duplicate_material")(duplicate_material)
