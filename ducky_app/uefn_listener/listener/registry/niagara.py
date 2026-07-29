"""Niagara registry tools: discover, create, place, and drive Niagara VFX.

Composable primitives — NOT one do-it-all tool. Each does a single job so the
agent can chain them (find a system, drop it in the level, tune parameters):

  READ    niagara_capabilities, list_niagara_systems, get_niagara_system_info,
          get_niagara_component_info
  CREATE  create_niagara_system
  CHANGE  set_niagara_component_parameter, control_niagara_actor

UEFN's embedded Python exposes only part of the Niagara API (emitter/module
graph editing lives in editor C++ that Python never sees, even in mainline UE).
Every tool guards on availability and, on a miss, returns the members that ARE
present (self-describing probe) instead of crashing. Place a system in the
level with the generic ``spawn_actor(asset_path=...)`` — spawning a
NiagaraSystem asset yields a NiagaraActor.
"""

from __future__ import annotations

from typing import Any, List

import unreal

from listener import lookup
from listener.dispatch import register
from listener.serialize import serialize

_NIAGARA_CLASSES = (
    "NiagaraSystem",
    "NiagaraActor",
    "NiagaraComponent",
    "NiagaraSystemFactoryNew",
    "NiagaraFunctionLibrary",
)

_HARD_LIST_CAP = 200

# value_type -> NiagaraComponent setter method (availability probed per call).
_PARAM_SETTERS = {
    "float": "set_variable_float",
    "int": "set_variable_int",
    "bool": "set_variable_bool",
    "vec2": "set_variable_vec2",
    "vec3": "set_variable_vec3",
    "vec4": "set_variable_vec4",
    "color": "set_variable_linear_color",
    "position": "set_variable_position",
}


def _capabilities() -> dict:
    return {name: hasattr(unreal, name) for name in _NIAGARA_CLASSES}


def _require(name: str):
    cls = getattr(unreal, name, None)
    if cls is None:
        raise ValueError(f"unreal.{name} is not exposed in this UEFN build. Capabilities: {_capabilities()}")
    return cls


def _members(obj: Any, contains: str = "") -> List[str]:
    """Public member names — makes 'not available' errors actionable."""
    out = []
    for n in dir(obj):
        if n.startswith("_"):
            continue
        if contains and contains not in n:
            continue
        out.append(n)
    return sorted(out)


def _load_asset(path: str):
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is None:
        raise ValueError(f"Asset not found: {path}")
    return asset


from listener.registry.asset_registry import assets_by_class as _assets_by_class


def _niagara_component(actor_path: str):
    actor = lookup.require_actor(actor_path)
    comps = actor.get_components_by_class(_require("NiagaraComponent"))
    if not comps:
        raise ValueError(f"No NiagaraComponent on actor: {actor.get_actor_label()}")
    return actor, comps[0]


def niagara_capabilities() -> dict:
    """Probe which Niagara classes this UEFN build exposes (run before other niagara tools)."""
    comp_cls = getattr(unreal, "NiagaraComponent", None)
    return {
        "classes": _capabilities(),
        "component_parameter_setters": _members(comp_cls, contains="set_variable") if comp_cls else [],
        "notes": [
            "Emitter/module graph editing is not exposed to Python; drive user parameters instead.",
            "Place a system with spawn_actor(asset_path=<NiagaraSystem path>) — it yields a NiagaraActor.",
        ],
    }


def list_niagara_systems(search: str = "", offset: int = 0, limit: int = 50) -> dict:
    """List NiagaraSystem assets in the project (filter with ``search``, paged)."""
    _require("NiagaraSystem")
    limit = max(0, min(int(limit), _HARD_LIST_CAP))
    q = (search or "").strip().lower()
    paths: List[str] = []
    for data in _assets_by_class("/Script/Niagara", "NiagaraSystem"):
        try:
            full = f"{data.package_name}.{data.asset_name}"
        except Exception:
            continue
        if q and q not in full.lower():
            continue
        paths.append(full)
    paths.sort()
    total = len(paths)
    page = paths[offset : offset + limit]
    return {"systems": page, "count": len(page), "total": total, "truncated": offset + len(page) < total}


def get_niagara_system_info(system_path: str) -> dict:
    """Read a NiagaraSystem's exposed user parameters and emitter names (guarded per build)."""
    system = _load_asset(system_path)
    info: dict = {"system_path": system_path, "class": system.get_class().get_name()}
    try:
        store = system.get_editor_property("exposed_parameters")
    except Exception as e:
        store = None
        info["user_parameters_error"] = str(e)
    if store is not None:
        params = None
        for getter in ("get_parameter_names", "get_parameters"):
            fn = getattr(store, getter, None)
            if callable(fn):
                try:
                    params = [str(p) for p in list(fn())][:100]
                    break
                except Exception:
                    continue
        if params is None:
            info["user_parameters_probe"] = _members(store)[:40]
        else:
            info["user_parameters"] = params
    try:
        handles = system.get_editor_property("emitter_handles")
        emitters = []
        for h in list(handles)[:50]:
            try:
                emitters.append(str(h.get_editor_property("name")))
            except Exception:
                emitters.append(serialize(h))
        info["emitters"] = emitters
    except Exception as e:
        info["emitters_error"] = str(e)
    return info


def create_niagara_system(asset_name: str, folder: str = "/Game/VFX") -> dict:
    """Create an empty NiagaraSystem asset (errors if it already exists)."""
    system_cls = _require("NiagaraSystem")
    factory_cls = _require("NiagaraSystemFactoryNew")
    unreal.EditorAssetLibrary.make_directory(folder)
    full = f"{folder}/{asset_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        raise ValueError(f"Asset already exists: {full} (delete_asset first to replace)")
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    system = asset_tools.create_asset(asset_name, folder, system_cls, factory_cls())
    if system is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    unreal.EditorAssetLibrary.save_loaded_asset(system, only_if_is_dirty=False)
    return {"system_path": str(system.get_path_name()), "asset_name": asset_name, "folder": folder}


def get_niagara_component_info(actor_path: str) -> dict:
    """Read the system + available parameter setters on an actor's NiagaraComponent."""
    actor, comp = _niagara_component(actor_path)
    info: dict = {"actor_path": actor.get_path_name(), "label": actor.get_actor_label()}
    get_asset = getattr(comp, "get_asset", None)
    if callable(get_asset):
        try:
            asset = get_asset()
            info["system_path"] = str(asset.get_path_name()) if asset else None
        except Exception as e:
            info["system_error"] = str(e)
    info["parameter_setters"] = _members(comp, contains="set_variable")
    return info


def _coerce_niagara_value(value_type: str, value: Any):
    if value_type == "float":
        return float(value)
    if value_type == "int":
        return int(value)
    if value_type == "bool":
        return bool(value)
    vals = [float(v) for v in (value if isinstance(value, (list, tuple)) else [value])]
    if value_type == "vec2":
        return unreal.Vector2D(*vals[:2])
    if value_type in ("vec3", "position"):
        return unreal.Vector(*vals[:3])
    if value_type == "vec4":
        return unreal.Vector4(*vals[:4])
    if value_type == "color":
        a = vals[3] if len(vals) > 3 else 1.0
        return unreal.LinearColor(vals[0], vals[1], vals[2], a)
    raise ValueError(f"Unknown value_type: {value_type!r}. Use one of {sorted(_PARAM_SETTERS)}")


def set_niagara_component_parameter(actor_path: str, param_name: str, value_type: str, value: Any) -> dict:
    """Set a Niagara USER parameter on an actor's NiagaraComponent.

    Engine setters are fire-and-forget: an unknown ``param_name`` is silently
    ignored — check names first with ``get_niagara_system_info``.
    """
    actor, comp = _niagara_component(actor_path)
    method_name = _PARAM_SETTERS.get(value_type)
    if method_name is None:
        raise ValueError(f"Unknown value_type: {value_type!r}. Use one of {sorted(_PARAM_SETTERS)}")
    fn = getattr(comp, method_name, None)
    if not callable(fn):
        raise ValueError(
            f"{method_name} not available on NiagaraComponent in this build. "
            f"Available setters: {_members(comp, contains='set_variable')}"
        )
    fn(param_name, _coerce_niagara_value(value_type, value))
    return {"actor_path": actor.get_path_name(), "param_name": param_name, "value_type": value_type, "value": value}


def control_niagara_actor(actor_path: str, action: str) -> dict:
    """Activate/deactivate/reset/reinitialize the NiagaraComponent on an actor."""
    actor, comp = _niagara_component(actor_path)
    actions = {
        "activate": lambda: comp.activate(True),
        "deactivate": lambda: comp.deactivate(),
        "reset": lambda: comp.reset_system(),
        "reinitialize": lambda: comp.reinitialize_system(),
    }
    fn = actions.get(action)
    if fn is None:
        raise ValueError(f"Unknown action: {action!r}. Use one of {sorted(actions)}")
    try:
        fn()
    except AttributeError:
        raise ValueError(
            f"{action!r} not available on NiagaraComponent in this build. Members: {_members(comp)[:60]}"
        )
    return {"actor_path": actor.get_path_name(), "action": action}


register("niagara_capabilities")(niagara_capabilities)
register("list_niagara_systems")(list_niagara_systems)
register("get_niagara_system_info")(get_niagara_system_info)
register("create_niagara_system")(create_niagara_system)
register("get_niagara_component_info")(get_niagara_component_info)
register("set_niagara_component_parameter")(set_niagara_component_parameter)
register("control_niagara_actor")(control_niagara_actor)
