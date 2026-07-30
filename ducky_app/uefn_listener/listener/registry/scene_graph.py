"""Scene Graph registry tools: entities, components, and prefabs in the UEFN editor.

Composable primitives — NOT one do-it-all tool. Each does a single job so the
agent can chain them (probe, find an entity, add a component, tune a property):

  PROBE   scene_graph_capabilities
  READ    list_entities, get_entity_info, list_scene_component_classes,
          get_selected_entities
  CREATE  create_entity, add_entity_component, create_prefab_from_entities,
          instantiate_prefab
  CHANGE  set_entity_transform, set_entity_component_property, rename_entity,
          set_entity_parent, duplicate_entity, remove_entity_component,
          select_entities
  DESTROY destroy_entity

Everything rides on ``SceneGraphScriptSubsystem`` (plugin PinballTools), Epic's
editor scripting API for Scene Graph. It may be missing on older UEFN builds —
every tool guards on availability and reports what IS present instead of
crashing. Verse component/struct properties use compiler-mangled names; these
tools translate friendly names via ``get_real_property_name`` and repr-based
struct field discovery so callers never see the mangling.

SpatialMath convention: translations/scales are ``[forward, left, up]`` (NOT
Unreal's X/Y/Z), rotations are quaternions ``[x, y, z, w]``. Tools accept and
report that convention verbatim.

Nothing here saves the level — call save_current_level once at the end of a
batch of edits (same contract as the actor tools).
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

import unreal

from listener.dispatch import register
from listener.project_paths import pin_project_folder

_HARD_LIST_CAP = 200

# VNI object paths for the built-in component classes (short-name aliases).
# Discovered from a live UEFN session; resolution falls back to a full scan of
# VerseClass objects when an alias is missing on this build.
_BUILTIN_COMPONENT_PATHS = {
    "transform_component": "/EntityFramework/_Verse/VNI/Entity.transform_component",
    "mesh_component": "/EntityFramework/_Verse/VNI/Component.mesh_component",
    "light_component": "/EntityFramework/_Verse/VNI/Component.light_component",
    "capsule_light_component": "/EntityFramework/_Verse/VNI/Component.capsule_light_component",
    "directional_light_component": "/EntityFramework/_Verse/VNI/Component.directional_light_component",
    "sphere_light_component": "/EntityFramework/_Verse/VNI/Component.sphere_light_component",
    "rect_light_component": "/EntityFramework/_Verse/VNI/Component.rect_light_component",
    "spot_light_component": "/EntityFramework/_Verse/VNI/Component.spot_light_component",
    "particle_system_component": "/EntityFramework/_Verse/VNI/Component.particle_system_component",
    "sound_component": "/EntityFramework/_Verse/VNI/Component.sound_component",
    "possessable_component": "/EntityFramework/_Verse/VNI/Component.possessable_component",
    "keyframed_movement_component": "/EntityFramework/_Verse/VNI/Component.KeyframedMovement_keyframed_movement_component",
    "icon_component": "/EntityFramework/_Verse/VNI/Entity.icon_component",
    "rarity_component": "/EntityFramework/_Verse/VNI/Entity.rarity_component",
    "basic_stackable_component": "/EntityFramework/_Verse/VNI/Entity.basic_stackable_component",
}

_ENTITY_CLASS_PATH = "/EntityFramework/_Verse/VNI/Entity.entity"
_COMPONENT_CLASS_PATH = "/EntityFramework/_Verse/VNI/Entity.component"

# Per-tick entity index (mirrors listener.lookup for actors) — avoids repeated
# full ObjectIterator scans within one editor tick.
_entities_cache: Optional[List[Any]] = None
_by_name: Optional[dict] = None
_by_path: Optional[dict] = None

# Session-long cache of addable component classes; only changes after a Verse
# recompile that adds new component classes, so it is never invalidated per-tick.
_component_classes_cache: Optional[List[dict]] = None


def invalidate() -> None:
    """Drop the cached entity index. Called once per tick (see listener.tick)."""
    global _entities_cache, _by_name, _by_path
    _entities_cache = None
    _by_name = None
    _by_path = None


def _subsystem():
    cls = getattr(unreal, "SceneGraphScriptSubsystem", None)
    if cls is None:
        raise ValueError(
            "SceneGraphScriptSubsystem is not exposed in this UEFN build — "
            "Scene Graph editor scripting is unavailable. Enable Scene Graph in "
            "Project Settings and update UEFN."
        )
    ss = unreal.get_editor_subsystem(cls)
    if ss is None:
        raise ValueError("SceneGraphScriptSubsystem could not be acquired (editor not ready?)")
    return ss


def _editor_level():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    levels = unreal.EditorLevelUtils.get_levels(world)
    if not levels:
        raise ValueError("No levels in the editor world")
    return levels[0]


def _load_class(path: str):
    cls = unreal.load_object(None, path)
    if cls is None:
        raise ValueError(f"Class not found: {path}")
    return cls


def _is_verse_class(cls) -> bool:
    try:
        return cls.get_class().get_name() == "VerseClass"
    except Exception:
        return False


def _iter_verse_classes():
    for cls in unreal.ObjectIterator(unreal.Class):
        if _is_verse_class(cls):
            yield cls


def _resolve_component_class(spec: str):
    """Resolve a component class from an alias, class name, or full object path."""
    s = (spec or "").strip()
    if not s:
        raise ValueError("component_class must not be empty")
    if s in _BUILTIN_COMPONENT_PATHS:
        return _load_class(_BUILTIN_COMPONENT_PATHS[s])
    if "/" in s:
        return _load_class(s)
    base = _load_class(_COMPONENT_CLASS_PATH)
    matches = []
    for cls in _iter_verse_classes():
        try:
            if cls.get_name() == s and _class_is_component(cls, base):
                matches.append(cls)
        except Exception:
            continue
    if not matches:
        aliases = sorted(_BUILTIN_COMPONENT_PATHS)
        raise ValueError(
            f"No Verse component class named {s!r}. Built-in aliases: {aliases}. "
            "For project components use the class name from list_scene_component_classes "
            "or a full object path."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous component class {s!r}: " + ", ".join(m.get_path_name() for m in matches)
        )
    return matches[0]


def _class_is_component(cls, component_base) -> bool:
    try:
        cur = cls
        seen = 0
        while cur is not None and seen < 32:
            if cur.get_path_name() == component_base.get_path_name():
                return True
            cur = cur.get_editor_property("super_struct") if hasattr(cur, "get_editor_property") else None
            seen += 1
    except Exception:
        pass
    return False


def _entity_objects() -> List[Any]:
    """Live entity instances in the open editor world, cached for this tick.

    Excludes CDOs, trash, and transient Prefab-Editor worlds.
    """
    global _entities_cache
    if _entities_cache is not None:
        return _entities_cache
    ent_cls = _load_class(_ENTITY_CLASS_PATH)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    world_prefix = world.get_package().get_name() + "."
    out = []
    for obj in unreal.ObjectIterator(ent_cls):
        path = obj.get_path_name()
        if ":PersistentLevel." not in path or not path.startswith(world_prefix):
            continue
        name = obj.get_name()
        if name.startswith("Default__") or name.startswith("TRASH_"):
            continue
        out.append(obj)
    _entities_cache = out
    return out


def _ensure_entity_maps() -> None:
    """Build name/path lookup indices for the cached entity list, once per tick."""
    global _by_name, _by_path
    if _by_name is not None and _by_path is not None:
        return
    by_name: dict = {}
    by_path: dict = {}
    for obj in _entity_objects():
        by_path[obj.get_path_name()] = obj
        for key in (obj.get_name(), _display_name(obj)):
            by_name.setdefault(key, [])
            if obj not in by_name[key]:
                by_name[key].append(obj)
    _by_name = by_name
    _by_path = by_path


def _display_name(obj) -> str:
    """Entity object name without the editor-appended uniquifier suffix."""
    return re.sub(r"_[0-9a-z]{12,}_\d+$", "", obj.get_name())


def _handle_for(obj):
    """Wrap a raw verse entity object in an EntityScriptHandle."""
    h = unreal.new_object(unreal.EntityScriptHandle)
    h.set_object_reference(obj)
    if not h.is_valid_handle():
        raise ValueError(f"Could not create a valid handle for {obj.get_path_name()}")
    return h


def _resolve_entity(entity: str):
    """Find one entity by display name, raw object name, or full object path.

    Uses the cached per-tick name/path index (built once, reused across every
    ``_resolve_entity`` call in the same tick — e.g. from ``select_entities``
    resolving many names) instead of scanning ``_entity_objects()`` per call.
    """
    s = (entity or "").strip()
    if not s:
        raise ValueError("entity must not be empty")
    _ensure_entity_maps()
    if "/" in s:
        obj = _by_path.get(s)
        if obj is not None:
            return _handle_for(obj), obj
        raise ValueError(f"No entity at path: {s}")
    exact = _by_name.get(s, [])
    if len(exact) == 1:
        return _handle_for(exact[0]), exact[0]
    if len(exact) > 1:
        raise ValueError(
            f"Ambiguous entity name {s!r}: " + ", ".join(o.get_path_name() for o in exact[:8])
        )
    objs = _entity_objects()
    partial = [o for o in objs if s.lower() in o.get_name().lower()]
    if len(partial) == 1:
        return _handle_for(partial[0]), partial[0]
    names = sorted(_display_name(o) for o in objs)[:40]
    raise ValueError(f"No entity matching {s!r}. Entities in level: {names}")


def _struct_field(struct_val, suffix: str) -> str:
    """Discover a mangled Verse struct field name by its human-readable suffix."""
    text = repr(struct_val)
    m = re.search(r"([A-Za-z0-9_]*verse_[0-9a-zA-Z_]+_" + re.escape(suffix) + r")\b", text)
    if m:
        return m.group(1)
    if re.search(r"\b" + re.escape(suffix) + r"\b", text):
        return suffix
    raise ValueError(f"Could not find a {suffix!r} field on struct {type(struct_val).__name__}: {text[:200]}")


def _real_prop(ss, cls, name: str) -> str:
    rp = ss.get_real_property_name(cls, name)
    rp = str(rp) if rp else ""
    if not rp or rp == "None":
        raise ValueError(
            f"No editable property {name!r} on {cls.get_name()} "
            "(names are case-sensitive; use the digest field name, e.g. 'Visible')"
        )
    return rp


def _vector3_to_list(v) -> list:
    return [
        float(v.get_editor_property(_struct_field(v, "forward"))),
        float(v.get_editor_property(_struct_field(v, "left"))),
        float(v.get_editor_property(_struct_field(v, "up"))),
    ]


def _set_vector3(v, values: list) -> None:
    v.set_editor_property(_struct_field(v, "forward"), float(values[0]))
    v.set_editor_property(_struct_field(v, "left"), float(values[1]))
    v.set_editor_property(_struct_field(v, "up"), float(values[2]))


def _transform_to_dict(t) -> dict:
    tr = t.get_editor_property(_struct_field(t, "translation"))
    sc = t.get_editor_property(_struct_field(t, "scale"))
    rot = t.get_editor_property(_struct_field(t, "rotation"))
    quat = rot.get_editor_property(_struct_field(rot, "quaternion"))
    return {
        "translation": _vector3_to_list(tr),
        "rotation_quat": [
            float(quat.get_editor_property("x")),
            float(quat.get_editor_property("y")),
            float(quat.get_editor_property("z")),
            float(quat.get_editor_property("w")),
        ],
        "scale": _vector3_to_list(sc),
    }


def _transform_component(ss, handle, create: bool = False):
    xf_cls = _load_class(_BUILTIN_COMPONENT_PATHS["transform_component"])
    comp = handle.get_or_create_component_by_type(xf_cls) if create else handle.get_component_by_type(xf_cls)
    if comp is None or not comp.is_valid_handle():
        raise ValueError("Entity has no transform_component")
    obj = comp.get_object_reference()
    if obj is None:
        raise ValueError("transform_component handle has no object")
    return xf_cls, obj


def _component_summary(comp_handle) -> dict:
    obj = comp_handle.get_object_reference()
    if obj is None:
        return {"class": None, "valid": False}
    cls = obj.get_class()
    return {
        "class": cls.get_name(),
        "class_path": cls.get_path_name(),
        "is_asset_component": bool(comp_handle.is_asset_component()),
    }


def _entity_summary(obj, with_components: bool = True) -> dict:
    info: dict = {
        "name": _display_name(obj),
        "object_name": obj.get_name(),
        "path": obj.get_path_name(),
        "class": obj.get_class().get_name(),
    }
    try:
        h = _handle_for(obj)
        if with_components:
            info["components"] = [_component_summary(c) for c in h.get_components()]
        children = h.get_children()
        info["children"] = [_display_name(c.get_object_reference()) for c in children if c.get_object_reference()]
        parent = h.get_parent()
        pobj = parent.get_object_reference() if parent and parent.is_valid_handle() else None
        info["parent"] = _display_name(pobj) if pobj else None
    except Exception as e:
        info["handle_error"] = str(e)
    return info


def scene_graph_capabilities() -> dict:
    """Probe Scene Graph editor scripting availability (run before other scene graph tools)."""
    have_subsystem = getattr(unreal, "SceneGraphScriptSubsystem", None) is not None
    have_converter = getattr(unreal, "SceneGraphConverterSubsystem", None) is not None
    builtin: dict = {}
    if have_subsystem:
        for alias, path in _BUILTIN_COMPONENT_PATHS.items():
            builtin[alias] = unreal.load_object(None, path) is not None
    entity_count = None
    if have_subsystem:
        try:
            entity_count = len(_entity_objects())
        except Exception:
            entity_count = None
    return {
        "script_subsystem": have_subsystem,
        "converter_subsystem": have_converter,
        "builtin_component_classes": builtin,
        "entities_in_level": entity_count,
        "notes": [
            "Translations/scales are SpatialMath [forward, left, up]; rotations are quaternions [x,y,z,w].",
            "Prefab instances placed from the Content Browser and Verse-spawned entities are both visible here.",
            "Nothing auto-saves — call save_current_level once after a batch of scene graph edits.",
        ],
    }


def list_entities(name_filter: str = "", offset: int = 0, limit: int = 50) -> dict:
    """List Scene Graph entities in the level (filter with ``name_filter``, paged)."""
    _subsystem()
    limit = max(0, min(int(limit), _HARD_LIST_CAP))
    q = (name_filter or "").strip().lower()
    objs = _entity_objects()
    if q:
        objs = [o for o in objs if q in o.get_name().lower()]
    objs.sort(key=lambda o: o.get_path_name())
    total = len(objs)
    page = objs[offset : offset + limit]
    return {
        "entities": [_entity_summary(o, with_components=False) for o in page],
        "count": len(page),
        "total": total,
        "truncated": offset + len(page) < total,
    }


def get_entity_info(entity: str) -> dict:
    """Read one entity: components, children, parent, transform, and bounds."""
    ss = _subsystem()
    handle, obj = _resolve_entity(entity)
    info = _entity_summary(obj, with_components=True)
    try:
        _, xf = _transform_component(ss, handle)
        real = _real_prop(ss, xf.get_class(), "LocalTransform")
        info["local_transform"] = _transform_to_dict(xf.get_editor_property(real))
    except Exception as e:
        info["local_transform_error"] = str(e)
    try:
        b = ss.get_entity_bounds(handle, True)
        origin, ext = b.origin, b.box_extent
        info["bounds"] = {
            "origin": [origin.x, origin.y, origin.z],
            "box_extent": [ext.x, ext.y, ext.z],
            "sphere_radius": float(b.sphere_radius),
        }
    except Exception as e:
        info["bounds_error"] = str(e)
    return info


def list_scene_component_classes(search: str = "", offset: int = 0, limit: int = 50) -> dict:
    """List Verse component classes addable to entities: built-ins, project Verse components, and asset-generated ones.

    The full (unfiltered) class list is scanned once per session via
    ``unreal.ObjectIterator`` and cached — new component classes only appear
    after a Verse recompile.
    """
    _subsystem()
    limit = max(0, min(int(limit), _HARD_LIST_CAP))
    q = (search or "").strip().lower()

    global _component_classes_cache
    if _component_classes_cache is None:
        base = _load_class(_COMPONENT_CLASS_PATH)
        rows = []
        for cls in _iter_verse_classes():
            try:
                if not _class_is_component(cls, base):
                    continue
                path = cls.get_path_name()
                kind = "builtin"
                if "/_Verse/Assets." in path:
                    kind = "asset_generated"
                elif not path.startswith("/EntityFramework/"):
                    kind = "project"
                rows.append({"class": cls.get_name(), "class_path": path, "kind": kind})
            except Exception:
                continue
        rows.sort(key=lambda r: (r["kind"], r["class_path"]))
        _component_classes_cache = rows

    rows = _component_classes_cache
    if q:
        rows = [r for r in rows if q in r["class_path"].lower()]
    total = len(rows)
    page = rows[offset : offset + limit]
    return {"component_classes": page, "count": len(page), "total": total, "truncated": offset + len(page) < total}


def get_selected_entities() -> dict:
    """Read the entities currently selected in the level editor."""
    ss = _subsystem()
    result = ss.get_level_editor_selection()
    handles = list(result[0]) if isinstance(result, tuple) else list(result or [])
    rows = []
    for h in handles:
        obj = h.get_object_reference()
        if obj is not None:
            rows.append(_entity_summary(obj, with_components=False))
    return {"selected": rows, "count": len(rows)}


def select_entities(entity_names: List[str]) -> dict:
    """Select the named entities in the level editor viewport/outliner."""
    ss = _subsystem()
    handles = [_resolve_entity(n)[0] for n in entity_names]
    ss.set_level_editor_selection(handles)
    return {"selected": entity_names, "count": len(handles)}


def create_entity(
    name: str,
    parent_entity: str = "",
    translation: Optional[List[float]] = None,
    rotation_quat: Optional[List[float]] = None,
    scale: Optional[List[float]] = None,
) -> dict:
    """Create an empty entity in the level (optionally parented and placed).

    translation/scale are SpatialMath [forward, left, up]; rotation_quat is [x,y,z,w].
    """
    ss = _subsystem()
    if not (name or "").strip():
        raise ValueError("name must not be empty")
    level = _editor_level()
    existing = ss.find_entity(level, name)
    if existing is not None and existing.is_valid_handle():
        raise ValueError(f"Entity named {name!r} already exists (names must be unique per level)")
    handle = ss.create_entity(level, name)
    if handle is None or not handle.is_valid_handle():
        raise RuntimeError(f"create_entity returned an invalid handle for {name!r}")
    invalidate()
    if parent_entity:
        parent, _ = _resolve_entity(parent_entity)
        ss.set_entity_parent(handle, parent)
    result: dict = {"name": name}
    if translation is not None or rotation_quat is not None or scale is not None:
        result["transform"] = _apply_transform(ss, handle, translation, rotation_quat, scale)
    obj = handle.get_object_reference()
    result["path"] = obj.get_path_name() if obj else None
    result["parent"] = parent_entity or None
    return result


def _apply_transform(ss, handle, translation, rotation_quat, scale) -> dict:
    xf_cls, xf = _transform_component(ss, handle, create=True)
    real = _real_prop(ss, xf_cls, "LocalTransform")
    t = xf.get_editor_property(real)
    if translation is not None:
        if len(translation) != 3:
            raise ValueError("translation must be [forward, left, up]")
        _set_vector3(t.get_editor_property(_struct_field(t, "translation")), translation)
    if scale is not None:
        if len(scale) != 3:
            raise ValueError("scale must be [forward, left, up]")
        _set_vector3(t.get_editor_property(_struct_field(t, "scale")), scale)
    if rotation_quat is not None:
        if len(rotation_quat) != 4:
            raise ValueError("rotation_quat must be [x, y, z, w]")
        rot = t.get_editor_property(_struct_field(t, "rotation"))
        quat = rot.get_editor_property(_struct_field(rot, "quaternion"))
        for axis, val in zip(("x", "y", "z", "w"), rotation_quat):
            quat.set_editor_property(axis, float(val))
        rot.set_editor_property(_struct_field(rot, "quaternion"), quat)
        t.set_editor_property(_struct_field(t, "rotation"), rot)
    xf.set_editor_property(real, t)
    return _transform_to_dict(xf.get_editor_property(real))


def set_entity_transform(
    entity: str,
    translation: Optional[List[float]] = None,
    rotation_quat: Optional[List[float]] = None,
    scale: Optional[List[float]] = None,
) -> dict:
    """Set an entity's local transform (SpatialMath [forward, left, up]; quat [x,y,z,w])."""
    ss = _subsystem()
    if translation is None and rotation_quat is None and scale is None:
        raise ValueError("Provide at least one of translation, rotation_quat, scale")
    handle, _ = _resolve_entity(entity)
    return {"entity": entity, "local_transform": _apply_transform(ss, handle, translation, rotation_quat, scale)}


def add_entity_component(entity: str, component_class: str, asset_path: str = "") -> dict:
    """Add a component to an entity.

    component_class: alias (mesh_component, sound_component, ...), Verse class
    name, or full class object path. Pass ``asset_path`` (a PROJECT content
    asset, e.g. /MyProject/Meshes/SM_Rock.SM_Rock) to create an asset-generated
    component (mesh/particle/sound) referencing that asset.
    """
    ss = _subsystem()
    handle, _ = _resolve_entity(entity)
    cls = _resolve_component_class(component_class)
    if asset_path:
        comp = ss.create_asset_component_from_asset_path(handle, cls, asset_path)
        if comp is None or not comp.is_valid_handle():
            raise ValueError(
                f"create_asset_component_from_asset_path failed for {asset_path!r}. The asset "
                "must live in the PROJECT's own content (it needs a digest-generated Verse class) — "
                "Fortnite /Game content will not work."
            )
    else:
        comp = handle.get_or_create_component_by_type(cls)
        if comp is None or not comp.is_valid_handle():
            raise RuntimeError(f"Could not add component {component_class!r}")
    return {"entity": entity, "component": _component_summary(comp)}


def remove_entity_component(entity: str, component_class: str) -> dict:
    """Remove the component of the given class from an entity."""
    _subsystem()
    handle, _ = _resolve_entity(entity)
    cls = _resolve_component_class(component_class)
    comp = handle.get_component_by_type(cls)
    if comp is None or not comp.is_valid_handle():
        have = [c.get_object_reference().get_class().get_name() for c in handle.get_components() if c.get_object_reference()]
        raise ValueError(f"Entity has no {component_class!r} component. Components: {have}")
    handle.remove_component(comp)
    return {"entity": entity, "removed": component_class}


def set_entity_component_property(entity: str, component_class: str, prop: str, value: Any) -> dict:
    """Set an @editable property on an entity's component by its Verse (digest) name.

    Values: bool for logic, numbers for float/int, string for string/enum-like,
    [f, l, u] list for vector3. Transforms go through set_entity_transform.
    """
    ss = _subsystem()
    handle, _ = _resolve_entity(entity)
    cls = _resolve_component_class(component_class)
    comp = handle.get_component_by_type(cls)
    if comp is None or not comp.is_valid_handle():
        raise ValueError(f"Entity has no {component_class!r} component (add_entity_component first)")
    obj = comp.get_object_reference()
    real = _real_prop(ss, obj.get_class(), prop)
    current = None
    try:
        current = obj.get_editor_property(real)
    except Exception:
        pass
    if isinstance(value, list) and current is not None and "vector3" in type(current).__name__.lower():
        _set_vector3(current, value)
        obj.set_editor_property(real, current)
    else:
        obj.set_editor_property(real, value)
    try:
        readback = obj.get_editor_property(real)
        readback = _vector3_to_list(readback) if "vector3" in type(readback).__name__.lower() else str(readback)
    except Exception:
        readback = None
    return {"entity": entity, "component": component_class, "prop": prop, "real_property": real, "value": readback}


def get_entity_component_property(entity: str, component_class: str, prop: str) -> dict:
    """Read an @editable property on an entity's component by its Verse (digest) name."""
    ss = _subsystem()
    handle, _ = _resolve_entity(entity)
    cls = _resolve_component_class(component_class)
    comp = handle.get_component_by_type(cls)
    if comp is None or not comp.is_valid_handle():
        raise ValueError(f"Entity has no {component_class!r} component")
    obj = comp.get_object_reference()
    real = _real_prop(ss, obj.get_class(), prop)
    val = obj.get_editor_property(real)
    tn = type(val).__name__
    if "vector3" in tn.lower():
        out: Any = _vector3_to_list(val)
    elif "transform" in tn.lower():
        out = _transform_to_dict(val)
    elif isinstance(val, (bool, int, float, str)):
        out = val
    else:
        out = str(val)
    return {"entity": entity, "component": component_class, "prop": prop, "value": out, "value_type": tn}


def rename_entity(entity: str, new_name: str) -> dict:
    """Rename an entity (names must stay unique within the level)."""
    ss = _subsystem()
    handle, _ = _resolve_entity(entity)
    if not (new_name or "").strip():
        raise ValueError("new_name must not be empty")
    ok = ss.rename_entity(handle, new_name)
    if not ok:
        raise ValueError(f"rename_entity failed (is {new_name!r} already taken?)")
    invalidate()  # name index is now stale
    return {"entity": entity, "new_name": new_name}


def set_entity_parent(entity: str, parent_entity: str) -> dict:
    """Re-parent an entity under another entity."""
    ss = _subsystem()
    handle, _ = _resolve_entity(entity)
    parent, _ = _resolve_entity(parent_entity)
    ss.set_entity_parent(handle, parent)
    return {"entity": entity, "parent": parent_entity}


def duplicate_entity(entity: str, new_name: str = "") -> dict:
    """Duplicate an entity (and its components/children); optionally rename the copy."""
    ss = _subsystem()
    handle, _ = _resolve_entity(entity)
    dup = ss.duplicate_entity(handle)
    if dup is None or not dup.is_valid_handle():
        raise RuntimeError("duplicate_entity returned an invalid handle")
    invalidate()
    if new_name:
        if not ss.rename_entity(dup, new_name):
            raise ValueError(f"Duplicated, but rename to {new_name!r} failed (name taken?)")
        invalidate()
    obj = dup.get_object_reference()
    return {"entity": entity, "duplicate": _display_name(obj) if obj else None, "path": obj.get_path_name() if obj else None}


def destroy_entity(entity: str) -> dict:
    """Destroy an entity and everything under it (children + components)."""
    ss = _subsystem()
    handle, obj = _resolve_entity(entity)
    path = obj.get_path_name()
    ss.destroy_entity(handle)
    invalidate()
    return {"destroyed": entity, "path": path}


def create_prefab_from_entities(entity_names: List[str], prefab_name: str, folder: str = "") -> dict:
    """Package existing level entities into a new Prefab asset (they become an instance of it).

    folder is a content path in the PROJECT mount, e.g. /MyProject/Prefabs
    (never invent /Game/Prefabs — omit folder to auto-pin).
    """
    folder = pin_project_folder(folder, default_leaf="Prefabs")
    ss = _subsystem()
    if not entity_names:
        raise ValueError("entity_names must not be empty")
    if not (prefab_name or "").strip():
        raise ValueError("prefab_name must not be empty")
    handles = [_resolve_entity(n)[0] for n in entity_names]
    # create_empty_prefab takes the FULL package path, not a folder.
    package_path = f"{folder.rstrip('/')}/{prefab_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(f"{package_path}.{prefab_name}"):
        raise ValueError(f"Asset already exists: {package_path}")
    try:
        can = ss.can_create_prefab_from_entities(handles)
        if can is False:
            raise ValueError("These entities cannot be packaged into a prefab (must share a level and not already be prefab internals)")
    except AttributeError:
        pass
    prefab = ss.create_empty_prefab(prefab_name, package_path)
    if prefab is None:
        raise RuntimeError(f"create_empty_prefab failed for {package_path}")
    instance = ss.create_prefab_from_entities(handles, prefab)
    if instance is None or not instance.is_valid_handle():
        raise RuntimeError("create_prefab_from_entities returned an invalid handle")
    invalidate()
    unreal.EditorAssetLibrary.save_loaded_asset(prefab, only_if_is_dirty=False)
    obj = instance.get_object_reference()
    return {
        "prefab_path": prefab.get_path_name(),
        "instance": _display_name(obj) if obj else entity_names[0],
        "note": "The source entities became an instance of the new prefab. Its Verse class appears in Assets.digest.verse after the next Verse build (workspace_push_verse_changes / verse build).",
    }


def instantiate_prefab(prefab_path: str, name: str = "", parent_entity: str = "") -> dict:
    """Place an instance of a Prefab asset into the level (best effort — prefab scripting is WIP in UEFN)."""
    ss = _subsystem()
    asset = unreal.EditorAssetLibrary.load_asset(prefab_path)
    if asset is None:
        raise ValueError(f"Prefab asset not found: {prefab_path}")
    label = name or asset.get_name() + "_Instance"
    errors: List[str] = []
    handle = None
    try:
        gen = asset.generated_class()
    except Exception as e:
        gen = None
        errors.append(f"generated_class: {e}")
    if gen is not None:
        # Instance the prefab's generated entity class under the level's entity container.
        containers = [o for o in _entity_objects() if o.get_class().get_name() == "level_entity"]
        if not containers:
            errors.append("no level_entity container found in the level")
        else:
            try:
                newobj = unreal.new_object(gen, containers[0], label)
                h = unreal.new_object(unreal.EntityScriptHandle)
                h.set_object_reference(newobj)
                if h.is_valid_handle():
                    handle = h
                else:
                    errors.append("instanced object did not produce a valid entity handle")
            except Exception as e:
                errors.append(f"new_object strategy: {e}")
    if handle is None:
        raise ValueError(
            "Could not instantiate this prefab from scripting on this build. "
            "Place it by dragging from the Content Browser, or spawn it at runtime in Verse "
            f"(instantiate its Assets.digest class and AddEntities). Details: {errors}"
        )
    invalidate()
    if parent_entity:
        ss.set_entity_parent(handle, _resolve_entity(parent_entity)[0])
    obj = handle.get_object_reference()
    return {"prefab_path": prefab_path, "instance": _display_name(obj) if obj else label, "path": obj.get_path_name() if obj else None}


def convert_actors_to_entities(actor_paths: List[str]) -> dict:
    """Convert level actors to Scene Graph entities via SceneGraphConverterSubsystem (guarded; irreversible)."""
    conv_cls = getattr(unreal, "SceneGraphConverterSubsystem", None)
    if conv_cls is None:
        raise ValueError("SceneGraphConverterSubsystem is not exposed in this UEFN build")
    conv = unreal.get_editor_subsystem(conv_cls)
    from listener import lookup

    actors = [lookup.require_actor(p) for p in actor_paths]
    fn = getattr(conv, "scripted_convert_actors_to_scene_graph", None)
    if not callable(fn):
        members = sorted(m for m in dir(conv) if not m.startswith("_"))
        raise ValueError(f"scripted_convert_actors_to_scene_graph unavailable. Members: {members[:40]}")
    result = fn(actors)
    invalidate()
    return {"converted": [a.get_actor_label() for a in actors], "result": str(result)}


register("scene_graph_capabilities")(scene_graph_capabilities)
register("list_entities")(list_entities)
register("get_entity_info")(get_entity_info)
register("list_scene_component_classes")(list_scene_component_classes)
register("get_selected_entities")(get_selected_entities)
register("select_entities")(select_entities)
register("create_entity")(create_entity)
register("set_entity_transform")(set_entity_transform)
register("add_entity_component")(add_entity_component)
register("remove_entity_component")(remove_entity_component)
register("set_entity_component_property")(set_entity_component_property)
register("get_entity_component_property")(get_entity_component_property)
register("rename_entity")(rename_entity)
register("set_entity_parent")(set_entity_parent)
register("duplicate_entity")(duplicate_entity)
register("destroy_entity")(destroy_entity)
register("create_prefab_from_entities")(create_prefab_from_entities)
register("instantiate_prefab")(instantiate_prefab)
register("convert_actors_to_entities")(convert_actors_to_entities)
