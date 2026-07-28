"""JSON-safe values for HTTP responses."""

from typing import Any, List, Optional

import unreal


def is_live(obj: Any) -> bool:
    """True only if ``obj`` is a UObject safe to call native methods on.

    Calling ANY native method (``get_name``, ``get_class``, ``destroy_actor`` …) on a
    None, destroyed, or pending-kill/GC'd UObject triggers a native
    ``EXCEPTION_ACCESS_VIOLATION reading 0xffffffffffffffff`` that Python ``try/except``
    CANNOT catch — it takes down the whole editor. Guard every dereference that could
    touch a mutated/destroyed actor (loops, cascade deletes, cross-frame refs) with this.
    """
    if obj is None:
        return False
    try:
        return bool(unreal.SystemLibrary.is_valid(obj))
    except Exception:
        # is_valid itself only throws for non-UObject inputs, which are not live.
        return False


def serialize(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): serialize(v) for k, v in obj.items()}
    if isinstance(obj, unreal.Vector):
        return {"x": obj.x, "y": obj.y, "z": obj.z}
    if isinstance(obj, unreal.Rotator):
        return {"pitch": obj.pitch, "yaw": obj.yaw, "roll": obj.roll}
    if isinstance(obj, unreal.Vector2D):
        return {"x": obj.x, "y": obj.y}
    if isinstance(obj, unreal.LinearColor):
        return {"r": obj.r, "g": obj.g, "b": obj.b, "a": obj.a}
    if isinstance(obj, unreal.Color):
        return {"r": obj.r, "g": obj.g, "b": obj.b, "a": obj.a}
    if isinstance(obj, unreal.Transform):
        return {
            "location": serialize(obj.translation),
            "rotation": serialize(obj.rotation.rotator()),
            "scale": serialize(obj.scale3d),
        }
    if isinstance(obj, unreal.AssetData):
        return {
            "asset_name": str(obj.asset_name),
            "asset_class": str(obj.asset_class_path.asset_name) if hasattr(obj, "asset_class_path") else str(getattr(obj, "asset_class", "")),
            "package_name": str(obj.package_name),
            "package_path": str(obj.package_path),
            "object_path": str(obj.get_export_text_name()) if hasattr(obj, "get_export_text_name") else str(obj.object_path) if hasattr(obj, "object_path") else "",
        }
    if hasattr(obj, "get_path_name"):
        return str(obj.get_path_name())
    if hasattr(obj, "get_name"):
        return str(obj.get_name())
    if hasattr(obj, "__class__") and hasattr(obj.__class__, "__qualname__"):
        cls_name = obj.__class__.__qualname__
        if "." in cls_name or cls_name[0].isupper():
            return str(obj)
    try:
        return str(obj)
    except Exception:
        return repr(obj)


def rotator_pyr(pitch: float, yaw: float, roll: float) -> unreal.Rotator:
    r = unreal.Rotator()
    r.pitch = float(pitch)
    r.yaw = float(yaw)
    r.roll = float(roll)
    return r


_ACTOR_FIELD_GETTERS = {
    "name": lambda a: a.get_name(),
    "label": lambda a: a.get_actor_label(),
    "class": lambda a: a.get_class().get_name(),
    "path": lambda a: a.get_path_name(),
    "location": lambda a: serialize(a.get_actor_location()),
    "rotation": lambda a: serialize(a.get_actor_rotation()),
    "scale": lambda a: serialize(a.get_actor_scale3d()),
}

ALL_ACTOR_FIELDS = list(_ACTOR_FIELD_GETTERS.keys())


def serialize_actor(actor: unreal.Actor, fields: Optional[List[str]] = None) -> dict:
    # A destroyed/invalid ref reaching a native getter crashes the editor — never deref it.
    if not is_live(actor):
        return {"invalid": True}
    if not fields:
        return {k: getter(actor) for k, getter in _ACTOR_FIELD_GETTERS.items()}
    out: dict = {}
    for field in fields:
        getter = _ACTOR_FIELD_GETTERS.get(field)
        if getter is not None:
            out[field] = getter(actor)
    return out


def serialize_asset_entry(asset_path: str, fields: Optional[List[str]] = None) -> Any:
    if not fields or fields == ["path"]:
        return str(asset_path)
    data = unreal.EditorAssetLibrary.find_asset_data(str(asset_path))
    if data is None:
        return {"path": str(asset_path)}
    full = serialize(data)
    if not fields:
        return full
    out: dict = {"path": str(asset_path)}
    for field in fields:
        if field in full:
            out[field] = full[field]
    return out
