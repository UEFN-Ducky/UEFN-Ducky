"""Niagara registry tools: discover, assemble, place, and drive Niagara VFX.

Composable primitives — NOT one do-it-all tool. Each does a single job so the
agent can chain them (create a system, add one emitter, place it, tune it):

  READ    niagara_capabilities, list_niagara_systems, get_niagara_system_info,
          get_niagara_component_info, list_niagara_user_parameters
  CREATE  create_niagara_system, create_niagara_mesh
  ASSEMBLE add_niagara_emitter, add_niagara_module, add_niagara_renderer,
          set_niagara_module_parameter, finalize_niagara_system
  CHANGE  set_niagara_component_parameter, control_niagara_actor

Two layers, verified against UEFN:

* **Stack assembly works.** ``FXConverterUtilitiesLibrary`` +
  ``NiagaraSystemConversionContext`` / ``NiagaraEmitterConversionContext`` add
  empty emitters, attach **stock** ``/Niagara/Modules/...`` scripts (with
  literal / ``User.*`` linked / nested dynamic-input values) and renderers.
* **Custom module-script graphs do not.** ``NiagaraGraph`` node wiring is not
  exposed, and ``NiagaraToolset_*`` classes carry no callable Python methods.

Stability is a hard requirement, not style: a single ~400-line ``execute_python``
builder that finalized ~22 emitters with deep dynamic-input chains in one call
took UEFN down and lost the never-saved system. Every mutator here does **one**
emitter's worth of work, finalizes, and saves the real Content asset — and
refuses throwaway ``__probe`` / ``*Test`` asset paths.

Place a system with the generic ``spawn_actor(asset_path=...)`` — spawning a
NiagaraSystem asset yields a NiagaraActor.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import unreal

from listener import lookup
from listener.dispatch import register
from listener.project_paths import pin_project_folder
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

# --- assembly guardrails (each one paid for by a UEFN crash or by junk assets) ---

# Nested dynamic-input trees deeper/wider than this mirror the builder that killed
# the editor. Reject instead of finding out again.
_MAX_DYNAMIC_DEPTH = 4
_MAX_DYNAMIC_NODES = 24
_MAX_MODULES_PER_EMITTER_CALL = 16
_MAX_PARAMS_PER_MODULE = 24

_EXEC_CATEGORIES = {
    "emitter_spawn": "EMITTER_SPAWN",
    "emitter_update": "EMITTER_UPDATE",
    "particle_spawn": "PARTICLE_SPAWN",
    "particle_update": "PARTICLE_UPDATE",
}

# Deprecated stock scripts UEFN still resolves but that silently drop half their
# inputs. Rewrite instead of letting an agent assemble a dead emitter.
_DEPRECATED_MODULE_PATHS = {
    "/Niagara/Modules/Spawn/Initialization/InitializeParticle": (
        "/Niagara/Modules/Spawn/Initialization/V2/InitializeParticle"
    ),
}

# Without this in Particle Update, particles never die: Lifetime is set but nothing
# ever compares Age to it, so the sim piles up until the emitter is culled.
_PARTICLE_STATE_PATH = "/Niagara/Modules/Update/Lifetime/ParticleState"

# Scale modules multiply a value that something else must have initialized first.
# module-name fragment -> the input an upstream module has to set.
_SCALE_DEPENDENCIES = {
    "ScaleSpriteSize": "Sprite Size",
    "ScaleMeshSize": "Mesh Scale",
}

# value_type -> (NiagaraScriptInputType member, FXConverter literal factory)
_INPUT_TYPES = {
    "float": ("FLOAT", "create_script_input_float"),
    "int": ("INT", "create_script_input_int"),
    "bool": ("BOOL", "create_script_input_bool"),
    "vec2": ("VEC2", "create_script_input_vec2"),
    "vec3": ("VEC3", "create_script_input_vector"),
    "vec4": ("VEC4", "create_script_input_vec4"),
    "color": ("LINEAR_COLOR", "create_script_input_linear_color"),
    "position": ("POSITION", "create_script_input_position"),
    "quat": ("QUAT", "create_script_input_quat"),
}

# Folder/asset names that mean "throwaway". Deliverable Content only.
_JUNK_SEGMENTS = {
    "__probe",
    "probe",
    "temp",
    "tmp",
    "transient",
    "scratch",
    "throwaway",
    "junk",
    "test",
    "tests",
}
_JUNK_NAME_RE = re.compile(
    r"(?:^|[_\W])(?:probe|scratch|throwaway|junk|dummy)(?:[_\W]|\d*$)|test\d*$",
    re.IGNORECASE,
)

# Live conversion sessions keyed by package path. UEFN's system conversion context
# has no find-emitter, so an emitter can only take further modules while its
# session is open (see add_niagara_module).
_SESSIONS: Dict[str, dict] = {}

# User.* parameters this listener linked during assembly, per system. The engine's
# exposed-parameter store is protected in UEFN, so this is the only in-editor
# record of what a freshly assembled system exposes.
_LINKED_USER_PARAMS: Dict[str, Dict[str, str]] = {}


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


def _package_path(path: str) -> str:
    """``/Proj/VFX/NS_X.NS_X`` -> ``/Proj/VFX/NS_X``."""
    p = (path or "").strip().replace("\\", "/")
    if "." in p.rsplit("/", 1)[-1]:
        p = p.rsplit(".", 1)[0]
    return p.rstrip("/")


def _reject_junk_path(path: str, what: str = "asset") -> str:
    """Refuse probe/test/temp destinations — every VFX asset must be deliverable.

    Agents kept leaving ``/Proj/__probe/NS_MeshTest`` behind in user projects.
    Renderer smoke tests belong in listener unit tests, not in Content.
    """
    p = _package_path(path)
    if not p:
        raise ValueError(f"{what} path required")
    segments = [s for s in p.split("/") if s]
    for seg in segments[:-1] if len(segments) > 1 else segments:
        if seg.lower() in _JUNK_SEGMENTS or seg.startswith("__"):
            raise ValueError(
                f"Refusing to create {what} under throwaway folder {seg!r} ({p}). "
                "Every Niagara asset must be deliverable Content under "
                "{content_root}<Effect>/{Niagara,Materials,MaterialInstances,Meshes}."
            )
    name = segments[-1] if segments else ""
    if _JUNK_NAME_RE.search(name):
        raise ValueError(
            f"Refusing to create throwaway {what} {name!r}. Name the real effect asset "
            "(NS_Sun, SM_Planet_Rocky, …) — no NS_*Test / *Probe / Scratch litter."
        )
    return p


def _fx():
    lib = getattr(unreal, "FXConverterUtilitiesLibrary", None)
    if lib is None:
        raise ValueError(
            "unreal.FXConverterUtilitiesLibrary is missing in this UEFN build — emitter/module "
            "assembly is unavailable. Author the system in the Niagara editor instead. "
            f"Capabilities: {_capabilities()}"
        )
    return lib


def _fx_call(name: str, *args):
    fn = getattr(_fx(), name, None)
    if not callable(fn):
        raise ValueError(
            f"FXConverterUtilitiesLibrary.{name} is not exposed in this build. "
            f"Available: {_members(_fx(), contains='create_')[:40]}"
        )
    return fn(*args)


def _exec_category(category: str):
    key = (category or "").strip().lower().replace(" ", "_")
    member = _EXEC_CATEGORIES.get(key)
    if member is None:
        raise ValueError(f"Unknown category {category!r}. Use one of {sorted(_EXEC_CATEGORIES)}")
    enum = getattr(unreal, "ScriptExecutionCategory", None)
    value = getattr(enum, member, None) if enum else None
    if value is None:
        raise ValueError(f"unreal.ScriptExecutionCategory.{member} is not exposed in this build")
    return value


def _input_type(value_type: str):
    member = _INPUT_TYPES.get((value_type or "float").strip().lower())
    if member is None:
        raise ValueError(f"Unknown value_type {value_type!r}. Use one of {sorted(_INPUT_TYPES)}")
    enum = getattr(unreal, "NiagaraScriptInputType", None)
    value = getattr(enum, member[0], None) if enum else None
    if value is None:
        raise ValueError(f"unreal.NiagaraScriptInputType.{member[0]} is not exposed in this build")
    return value


def _literal_input(value_type: str, value: Any):
    vt = (value_type or "float").strip().lower()
    spec = _INPUT_TYPES.get(vt)
    if spec is None:
        raise ValueError(f"Unknown value_type {value_type!r}. Use one of {sorted(_INPUT_TYPES)}")
    factory = getattr(_fx(), spec[1], None)
    if not callable(factory):
        if vt == "position":
            raise ValueError(
                "Position literals are not exposed by FXConverter in this build. Link a position "
                "parameter instead (e.g. link: 'Engine.Owner.Position') or feed the pin a dynamic "
                "/Niagara/DynamicInputs/Vectors/Position/AddVectorToPosition."
            )
        raise ValueError(f"FXConverterUtilitiesLibrary.{spec[1]} is not exposed in this build")
    if vt == "float":
        return factory(float(value))
    if vt == "int":
        return factory(int(value))
    if vt == "bool":
        return factory(bool(value))
    vals = [float(v) for v in (value if isinstance(value, (list, tuple)) else [value])]
    if vt == "vec2":
        return factory(unreal.Vector2D(*vals[:2]))
    if vt in ("vec3", "position"):
        return factory(unreal.Vector(*vals[:3]))
    if vt == "vec4":
        return factory(unreal.Vector4(*vals[:4]))
    if vt == "color":
        a = vals[3] if len(vals) > 3 else 1.0
        return factory(unreal.LinearColor(vals[0], vals[1], vals[2], a))
    if vt == "quat":
        return factory(unreal.Quat(*vals[:4]))
    raise ValueError(f"Unsupported literal value_type {value_type!r}")


def _script_context(module_path: str):
    """Conversion context for a stock ``/Niagara/Modules`` or ``/Niagara/DynamicInputs`` script."""
    path = _package_path(module_path)
    if not path:
        raise ValueError("module_path required")
    args_cls = getattr(unreal, "CreateScriptContextArgs", None)
    if args_cls is None:
        raise ValueError("unreal.CreateScriptContextArgs is not exposed in this build")
    args = args_cls()
    args.set_editor_property("script_asset", _fx_call("create_asset_data", path))
    ctx = _fx_call("create_script_context", args)
    if ctx is None:
        raise ValueError(
            f"No Niagara script at {path!r} — stock module paths look like "
            "/Niagara/Modules/Spawn/Location/SphereLocation or "
            "/Niagara/DynamicInputs/Multiply/Multiply_Float."
        )
    return ctx


from listener.registry.asset_registry import assets_by_class as _assets_by_class


def _niagara_component(actor_path: str):
    actor = lookup.require_actor(actor_path)
    comps = actor.get_components_by_class(_require("NiagaraComponent"))
    if not comps:
        raise ValueError(f"No NiagaraComponent on actor: {actor.get_actor_label()}")
    return actor, comps[0]


def niagara_capabilities() -> dict:
    """Probe which Niagara classes this UEFN build exposes (run ONCE before other niagara tools)."""
    comp_cls = getattr(unreal, "NiagaraComponent", None)
    fx = getattr(unreal, "FXConverterUtilitiesLibrary", None)
    classes = _capabilities()
    classes.update(
        {
            "FXConverterUtilitiesLibrary": fx is not None,
            "NiagaraSystemConversionContext": hasattr(unreal, "NiagaraSystemConversionContext"),
            "NiagaraEmitterConversionContext": hasattr(unreal, "NiagaraEmitterConversionContext"),
            "GeometryScript_Primitives": hasattr(unreal, "GeometryScript_Primitives"),
            "GeometryScript_NewAssetUtils": hasattr(unreal, "GeometryScript_NewAssetUtils"),
        }
    )
    return {
        "classes": classes,
        "fx_converter": fx is not None,
        "assembly": "mcp_tools" if fx is not None else "niagara_editor_only",
        "component_parameter_setters": _members(comp_cls, contains="set_variable") if comp_cls else [],
        "renderer_types": ["sprite", "mesh", "ribbon", "light"],
        "execution_categories": sorted(_EXEC_CATEGORIES),
        "limits": {
            "max_dynamic_depth": _MAX_DYNAMIC_DEPTH,
            "max_dynamic_nodes_per_module": _MAX_DYNAMIC_NODES,
            "max_modules_per_call": _MAX_MODULES_PER_EMITTER_CALL,
        },
        "open_sessions": sorted(_SESSIONS),
        "notes": [
            "Emitter / stock-module / renderer assembly IS available: add_niagara_emitter, "
            "add_niagara_module, add_niagara_renderer, set_niagara_module_parameter.",
            "Custom module-script GRAPHS (NiagaraGraph node wiring) are not exposed — author "
            "those in the Niagara editor; do not probe for them.",
            "NiagaraToolset_* classes exist in dir(unreal) but expose no callable methods — ignore them.",
            "Mesh particles need a project StaticMesh (create_niagara_mesh). /Engine/BasicShapes "
            "cannot be duplicated in UEFN — duplicate_asset returns None.",
            "add_niagara_emitter rewrites the deprecated Spawn/Initialization/InitializeParticle to "
            "its V2 and auto-adds Update/Lifetime/ParticleState so particles die at Lifetime; "
            "ScaleSpriteSize/ScaleMeshSize are refused unless an upstream module sets the size.",
            "Never assemble Niagara from execute_python: a monolithic builder crashed the editor "
            "and lost the unsaved system. One emitter per call; each call finalizes and saves.",
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
    """Read what a NiagaraSystem exposes: user parameters, emitters, assembly state.

    ``exposed_parameters`` / ``emitter_handles`` are protected in UEFN, so full
    enumeration of a hand-authored system is not always possible. Parameters this
    listener linked during assembly are reported as ``linked_user_parameters``;
    for anything else, place the system and probe with ``list_niagara_user_parameters``.
    """
    system = _load_asset(system_path)
    key = _package_path(system_path)
    info: dict = {"system_path": key, "class": system.get_class().get_name()}

    store = None
    try:
        store = system.get_editor_property("exposed_parameters")
    except Exception:
        store = None
    if store is not None:
        for getter in ("get_parameter_names", "get_parameters"):
            fn = getattr(store, getter, None)
            if callable(fn):
                try:
                    info["user_parameters"] = [str(p) for p in list(fn())][:100]
                    break
                except Exception:
                    continue

    linked = _LINKED_USER_PARAMS.get(key)
    if linked:
        info["linked_user_parameters"] = [{"name": n, "value_type": t} for n, t in sorted(linked.items())]

    try:
        handles = system.get_editor_property("emitter_handles")
        emitters = []
        for h in list(handles)[:50]:
            try:
                emitters.append(str(h.get_editor_property("name")))
            except Exception:
                emitters.append(serialize(h))
        info["emitters"] = emitters
    except Exception:
        pass

    sess = _SESSIONS.get(key)
    if sess is not None:
        info["open_session_emitters"] = sorted(sess["emitters"])
        info["warning"] = (
            "This system has an OPEN conversion session — call finalize_niagara_system to compile "
            "and save it, or the staged emitters are lost."
        )
    if "user_parameters" not in info and "linked_user_parameters" not in info:
        info["enumeration"] = (
            "UEFN protects the exposed-parameter store: user parameters cannot be listed from the "
            "asset. Place the system with spawn_actor and probe names with "
            "list_niagara_user_parameters(actor_path, names=[...])."
        )
    if "emitters" not in info:
        info["emitters_note"] = (
            "Emitter handles are editor-only in this build — informational, not a failure."
        )
    return info


def list_niagara_user_parameters(actor_path: str, names: List[str], value_type: str = "float") -> dict:
    """Probe which ``User.*`` names a PLACED system actually exposes (component getters).

    The engine getters return ``(value, found)`` — the only reliable existence check
    for a user parameter, since the asset's parameter store is protected.
    """
    actor, comp = _niagara_component(actor_path)
    getter_name = _PARAM_SETTERS.get(value_type, "").replace("set_variable", "get_variable")
    if not getter_name:
        raise ValueError(f"Unknown value_type: {value_type!r}. Use one of {sorted(_PARAM_SETTERS)}")
    fn = getattr(comp, getter_name, None)
    if not callable(fn):
        raise ValueError(
            f"{getter_name} is not available on NiagaraComponent in this build. "
            f"Available getters: {_members(comp, contains='get_variable')}"
        )
    found: List[dict] = []
    missing: List[str] = []
    for raw in list(names or [])[:100]:
        name = str(raw)
        try:
            result = fn(name)
        except Exception:
            missing.append(name)
            continue
        value, ok = result if isinstance(result, tuple) else (result, result is not None)
        if ok:
            found.append({"name": name, "value": serialize(value)})
        else:
            missing.append(name)
    return {
        "actor_path": actor.get_path_name(),
        "value_type": value_type,
        "found": found,
        "missing": missing,
    }


def create_niagara_system(asset_name: str, folder: str = "") -> dict:
    """Create an empty NiagaraSystem asset (errors if it already exists)."""
    folder = pin_project_folder(folder, default_leaf="VFX")
    system_cls = _require("NiagaraSystem")
    factory_cls = _require("NiagaraSystemFactoryNew")
    full = _reject_junk_path(f"{folder.rstrip('/')}/{asset_name}", "Niagara system")
    unreal.EditorAssetLibrary.make_directory(folder)
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        raise ValueError(f"Asset already exists: {full} (delete_asset first to replace)")
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    system = asset_tools.create_asset(asset_name, folder, system_cls, factory_cls())
    if system is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    unreal.EditorAssetLibrary.save_loaded_asset(system, only_if_is_dirty=False)
    return {"system_path": str(system.get_path_name()), "asset_name": asset_name, "folder": folder}


# --------------------------------------------------------------------------- assembly


def _build_input(spec: dict, *, system_key: str, where: str, budget: dict, depth: int):
    """Literal / ``User.*`` link / nested dynamic input from one parameter spec."""
    name = spec.get("name") or "<unnamed>"
    if depth > _MAX_DYNAMIC_DEPTH:
        raise ValueError(
            f"{where}: dynamic input nesting deeper than {_MAX_DYNAMIC_DEPTH} on {name!r}. "
            "Deep cos/sin trees are what took UEFN down — use the stock "
            "/Niagara/Modules/Spawn/Location/RotateAroundPoint module instead."
        )

    dynamic = spec.get("dynamic")
    if dynamic:
        budget["nodes"] += 1
        if budget["nodes"] > _MAX_DYNAMIC_NODES:
            raise ValueError(
                f"{where}: more than {_MAX_DYNAMIC_NODES} dynamic-input nodes in one module. "
                "Split the effect across emitters or use a stock module."
            )
        child = _script_context(dynamic.get("module_path", ""))
        _apply_parameters(
            child,
            dynamic.get("parameters") or [],
            system_key=system_key,
            where=f"{where} -> {dynamic.get('module_path')}",
            budget=budget,
            depth=depth + 1,
        )
        return _fx_call("create_script_input_dynamic", child, _input_type(spec.get("value_type", "float")))

    link = spec.get("link")
    if link:
        value_type = spec.get("value_type", "float")
        if str(link).startswith("User."):
            _LINKED_USER_PARAMS.setdefault(system_key, {})[str(link)] = value_type
        return _fx_call("create_script_input_linked_parameter", str(link), _input_type(value_type))

    if "value" not in spec:
        raise ValueError(
            f"{where}: parameter {name!r} needs one of 'value' (+ 'value_type'), 'link', or 'dynamic'."
        )
    return _literal_input(spec.get("value_type", "float"), spec.get("value"))


def _apply_parameters(
    script_ctx,
    parameters: List[dict],
    *,
    system_key: str,
    where: str,
    budget: dict,
    depth: int = 0,
) -> List[str]:
    """Set every input on a script context, or fail before anything is staged."""
    params = list(parameters or [])
    if len(params) > _MAX_PARAMS_PER_MODULE:
        raise ValueError(f"{where}: {len(params)} parameters exceeds the cap of {_MAX_PARAMS_PER_MODULE}")
    applied: List[str] = []
    for spec in params:
        if not isinstance(spec, dict) or not spec.get("name"):
            raise ValueError(f"{where}: each parameter needs a 'name' (got {spec!r})")
        name = str(spec["name"])
        value = _build_input(spec, system_key=system_key, where=where, budget=budget, depth=depth)
        if not script_ctx.set_parameter(name, value):
            raise ValueError(
                f"{where}: module rejected input {name!r} (unknown input name or wrong type). "
                "Do not brute-force names — use the frozen input map in the vfx skill "
                "(references/stock_module_assembly.md) or open the module in the Niagara editor."
            )
        applied.append(name)
    return applied


def _system_asset(system_path: str):
    asset = _load_asset(system_path)
    cls = asset.get_class().get_name()
    if cls != "NiagaraSystem":
        raise ValueError(f"{system_path} is a {cls}, not a NiagaraSystem")
    return asset


def _session_alive(sess: dict) -> bool:
    try:
        return bool(unreal.SystemLibrary.is_valid(sess["system"]))
    except Exception:
        return True


def _open_session(system_path: str) -> dict:
    """Reuse the open conversion session for a system, or start one."""
    key = _package_path(system_path)
    sess = _SESSIONS.get(key)
    if sess is not None and _session_alive(sess):
        return sess
    system = _system_asset(system_path)
    ctx = _fx_call("create_system_conversion_context", system)
    if ctx is None:
        raise RuntimeError(f"create_system_conversion_context returned None for {key}")
    sess = {"key": key, "system": system, "ctx": ctx, "emitters": {}, "opened": time.time()}
    _SESSIONS[key] = sess
    return sess


def _abandon_session(sess: dict) -> None:
    """Drop a session without finalizing — staged modules must never be replayed."""
    try:
        cleanup = getattr(sess["ctx"], "cleanup", None)
        if callable(cleanup):
            cleanup()
    except Exception:
        pass
    _SESSIONS.pop(sess["key"], None)


def _finalize_session(sess: dict) -> dict:
    """Compile the staged changes and persist the asset, then close the session.

    Finalize is one-shot: staged modules/renderers must not be replayed, so the
    session is dropped. A finalized emitter cannot be reopened (UEFN's system
    conversion context exposes no find-emitter).
    """
    sess["ctx"].finalize()
    unreal.EditorAssetLibrary.save_loaded_asset(sess["system"], only_if_is_dirty=False)
    _SESSIONS.pop(sess["key"], None)
    return {"finalized": True, "saved": True}


def _open_emitter(system_path: str, emitter_name: str):
    """The (session, emitter context) pair for an emitter still open for edits."""
    key = _package_path(system_path)
    sess = _SESSIONS.get(key)
    em = sess["emitters"].get(emitter_name) if sess is not None and _session_alive(sess) else None
    if em is None:
        open_emitters = sorted(sess["emitters"]) if sess is not None else []
        raise ValueError(
            f"Emitter {emitter_name!r} is not open in a conversion session for {key}. "
            "UEFN's NiagaraSystemConversionContext has no find-emitter, so an emitter only accepts "
            "further modules while the session that created it is open. Either build the whole "
            "emitter in one add_niagara_emitter call (modules=[...], renderers=[...]), or chain "
            "add_niagara_module calls with finalize=false and finish with finalize_niagara_system. "
            "A finalized emitter cannot be reopened: rebuild it by delete_asset on the system, then "
            "recreate it at the same path — record any placed actor's location first, because "
            "deleting the asset leaves an orphaned NiagaraActor that has to be deleted and re-spawned. "
            f"Open emitters: {open_emitters}"
        )
    return sess, em


def _project_mesh(mesh_path: str):
    path = _package_path(mesh_path)
    if not path:
        raise ValueError("mesh renderer needs a 'mesh' asset path")
    if path.lower().startswith("/engine/"):
        raise ValueError(
            f"{path} is Engine content — Niagara mesh particles must use a project-owned StaticMesh "
            "(and UEFN cannot even duplicate /Engine/BasicShapes: duplicate_asset returns None). "
            "Create one first with create_niagara_mesh."
        )
    asset = _load_asset(path)
    cls = asset.get_class().get_name()
    if cls != "StaticMesh":
        raise ValueError(f"{path} is a {cls}, not a StaticMesh")
    return asset


def _make_renderer(system, spec: dict):
    """Only the two constructions proven safe in UEFN (bare UObject ctors crash)."""
    rtype = str(spec.get("type") or "sprite").strip().lower()
    name = str(spec.get("name") or rtype.capitalize())
    if rtype == "component":
        raise ValueError(
            "Component renderers are a UEFN publish/cook blocker — use mesh or sprite renderers."
        )
    if rtype == "mesh":
        props = _fx_call("create_mesh_renderer_properties")
        entries = list(props.get_editor_property("meshes"))
        entry = entries[0] if entries else unreal.NiagaraMeshRendererMeshProperties()
        entry.set_editor_property("mesh", _project_mesh(spec.get("mesh", "")))
        scale = spec.get("scale")
        if scale:
            entry.set_editor_property("scale", unreal.Vector(*[float(v) for v in list(scale)[:3]]))
        props.set_editor_property("meshes", [entry])
    elif rtype == "sprite":
        # new_object with outer=system — the bare NiagaraSpriteRendererProperties()
        # constructor makes an outer-less UObject and crashes on finalize.
        props = unreal.new_object(unreal.NiagaraSpriteRendererProperties, outer=system)
        material = spec.get("material")
        if material:
            props.set_editor_property("material", _load_asset(_package_path(material)))
    elif rtype == "ribbon":
        props = _fx_call("create_ribbon_renderer_properties")
        material = spec.get("material")
        if material:
            props.set_editor_property("material", _load_asset(_package_path(material)))
    elif rtype == "light":
        props = _fx_call("create_light_renderer_properties")
    else:
        raise ValueError(f"Unknown renderer type {rtype!r}. Use sprite|mesh|ribbon|light")

    sort_mode = getattr(unreal, "NiagaraSortMode", None)
    if sort_mode is not None and rtype in ("sprite", "mesh"):
        try:
            props.set_editor_property("sort_mode", sort_mode.NONE)
        except Exception:
            pass
    return name, props


def _module_leaf(spec: dict) -> str:
    return _package_path(str(spec.get("module_path") or "")).rsplit("/", 1)[-1].lower()


def _sets_input(spec: dict, input_name: str) -> bool:
    """True if this module initializes ``input_name`` (by parameter or by identity)."""
    wanted = input_name.lower()
    for param in spec.get("parameters") or []:
        if isinstance(param, dict) and wanted in str(param.get("name") or "").lower():
            return True
    leaf = _module_leaf(spec)
    return wanted.replace(" ", "") in leaf and "scale" not in leaf


def _prepare_modules(
    modules: Optional[List[dict]],
    staged: Optional[List[dict]] = None,
    particle_state: bool = False,
) -> tuple:
    """Rewrite deprecated module paths, auto-add ParticleState, and refuse scalers
    whose input was never initialized. Returns ``(modules, warnings)``.

    Pure (no Unreal calls) so the three assembly failures it prevents — deprecated
    InitializeParticle, immortal particles, ScaleSpriteSize with an unmet
    dependency — are covered by unit tests instead of by editor sessions.
    """
    warnings: List[str] = []
    mods: List[dict] = []
    for spec in modules or []:
        if not isinstance(spec, dict):
            raise ValueError(f"each module must be an object with a 'module_path' (got {spec!r})")
        spec = dict(spec)
        path = _package_path(str(spec.get("module_path") or ""))
        replacement = _DEPRECATED_MODULE_PATHS.get(path)
        if replacement:
            spec["module_path"] = replacement
            warnings.append(f"{path} is deprecated in UEFN — rewrote to {replacement}")
        mods.append(spec)

    known = list(staged or []) + mods
    if particle_state and not any("particlestate" in _module_leaf(m) for m in known):
        at = next(
            (
                i
                for i, m in enumerate(mods)
                if str(m.get("category") or "particle_update").strip().lower().replace(" ", "_")
                == "particle_update"
            ),
            len(mods),
        )
        mods.insert(
            at,
            {
                "name": "ParticleState",
                "module_path": _PARTICLE_STATE_PATH,
                "category": "particle_update",
                "parameters": [],
            },
        )
        warnings.append(
            f"added {_PARTICLE_STATE_PATH} so particles die at Lifetime (particle_state=false to skip)"
        )
        known = list(staged or []) + mods

    for scaler, dependency in _SCALE_DEPENDENCIES.items():
        if not any(scaler.lower() in _module_leaf(m) for m in known):
            continue
        if any(_sets_input(m, dependency) for m in known):
            continue
        raise ValueError(
            f"{scaler} multiplies an existing {dependency!r}, and nothing on this emitter sets one — "
            f"in UEFN it compiles to an unmet dependency. Either give "
            f"{_DEPRECATED_MODULE_PATHS['/Niagara/Modules/Spawn/Initialization/InitializeParticle']} "
            f"(particle_spawn) a {dependency!r} parameter, or drop {scaler} and set {dependency!r} "
            "once on InitializeParticle for a constant size."
        )
    return mods, warnings


def _add_modules(
    em, sess: dict, modules: List[dict], results: dict, particle_state: bool = False
) -> None:
    emitter_name = str(results.get("emitter_name") or "")
    staged = sess.setdefault("staged", {}).setdefault(emitter_name, [])
    mods, warnings = _prepare_modules(modules, staged=staged, particle_state=particle_state)
    if warnings:
        results.setdefault("warnings", []).extend(warnings)
    if len(mods) > _MAX_MODULES_PER_EMITTER_CALL:
        raise ValueError(
            f"{len(mods)} modules in one call exceeds the cap of {_MAX_MODULES_PER_EMITTER_CALL} — "
            "one emitter's worth of work per call."
        )
    budget = {"nodes": 0}
    for spec in mods:
        module_path = _package_path(str(spec.get("module_path") or ""))
        if not module_path:
            raise ValueError("each module needs a 'module_path'")
        name = str(spec.get("name") or module_path.rsplit("/", 1)[-1])
        category = _exec_category(str(spec.get("category") or "particle_update"))
        ctx = _script_context(module_path)
        applied = _apply_parameters(
            ctx,
            spec.get("parameters") or [],
            system_key=sess["key"],
            where=f"{name} ({module_path})",
            budget=budget,
            depth=0,
        )
        em.add_module_script(name, ctx, category)
        results.setdefault("modules", []).append(
            {"name": name, "module_path": module_path, "parameters_applied": applied}
        )
    staged.extend(mods)
    results["dynamic_nodes"] = budget["nodes"]


def add_niagara_emitter(
    system_path: str,
    emitter_name: str,
    modules: Optional[List[dict]] = None,
    renderers: Optional[List[dict]] = None,
    sim_target: str = "cpu",
    local_space: bool = False,
    enabled: bool = True,
    emitter_state: bool = True,
    particle_state: bool = True,
    loop_duration: Optional[float] = None,
    finalize: bool = True,
) -> dict:
    """Add ONE emitter (with its stock modules + renderers) to a saved NiagaraSystem.

    This is the main assembly call: build a whole emitter here, then it finalizes
    and saves. Never batch a scene's worth of emitters — that is what crashed the
    editor. ``modules`` entries are
    ``{"name", "module_path", "category", "parameters": [...]}`` where each
    parameter is ``{"name", "value"/"value_type"}``, ``{"name", "link": "User.X",
    "value_type"}`` or ``{"name", "dynamic": {"module_path", "parameters"}}``.
    ``renderers`` entries are ``{"type": "mesh", "mesh": "/Proj/.../SM_X"}`` or
    ``{"type": "sprite", "material": "/Proj/.../MI_X"}``.

    Assembly is corrected before anything is staged: the deprecated
    ``Spawn/Initialization/InitializeParticle`` is rewritten to its ``V2``, and
    ``ParticleState`` is added to Particle Update so particles actually die at
    Lifetime (``particle_state=false`` for an intentionally immortal emitter).
    Both show up in ``warnings`` on the result.
    """
    _reject_junk_path(system_path, "Niagara system")
    if not (emitter_name or "").strip():
        raise ValueError("emitter_name required")
    if _JUNK_NAME_RE.search(emitter_name):
        raise ValueError(f"Refusing throwaway emitter name {emitter_name!r} — name it for its job")

    sess = _open_session(system_path)
    if emitter_name in sess["emitters"]:
        raise ValueError(f"Emitter {emitter_name!r} is already open in this session")

    results: dict = {"system_path": sess["key"], "emitter_name": emitter_name}
    try:
        em = sess["ctx"].add_empty_emitter(emitter_name)
        if em is None:
            raise RuntimeError(f"add_empty_emitter returned None for {emitter_name!r}")
        sess["emitters"][emitter_name] = em

        target = (sim_target or "cpu").strip().lower()
        sim_enum = getattr(unreal, "NiagaraSimTarget", None)
        if sim_enum is not None:
            em.set_sim_target(
                sim_enum.GPU_COMPUTE_SIM if target in ("gpu", "gpu_compute") else sim_enum.CPU_SIM
            )
        em.set_local_space(bool(local_space))
        em.set_enabled(bool(enabled))

        mods = list(modules or [])
        if emitter_state and not any("EmitterState" in str(m.get("module_path", "")) for m in mods):
            state: dict = {
                "name": "EmitterState",
                "module_path": "/Niagara/Modules/Emitter/EmitterState",
                "category": "emitter_update",
                "parameters": [],
            }
            if loop_duration is not None:
                state["parameters"] = [
                    {"name": "Loop Duration", "value_type": "float", "value": float(loop_duration)}
                ]
            mods.insert(0, state)
        _add_modules(em, sess, mods, results, particle_state=particle_state)

        for spec in list(renderers or []):
            name, props = _make_renderer(sess["system"], spec)
            em.add_renderer(name, props)
            results.setdefault("renderers", []).append({"name": name, "type": spec.get("type", "sprite")})
    except Exception as exc:
        # Never finalize a half-built emitter; drop the session so the next call starts clean.
        _abandon_session(sess)
        raise ValueError(
            f"{exc} — nothing was finalized; the conversion session for {sess['key']} was discarded. "
            f"Fix the call and re-add emitter {emitter_name!r}."
        ) from exc

    if finalize:
        results.update(_finalize_session(sess))
    else:
        results["finalized"] = False
        results["note"] = "Session left open — call finalize_niagara_system to compile and save."
    return results


def add_niagara_module(
    system_path: str,
    emitter_name: str,
    module_path: str,
    category: str = "particle_update",
    parameters: Optional[List[dict]] = None,
    module_name: str = "",
    finalize: bool = True,
) -> dict:
    """Attach one stock module script to an emitter that is open in this session.

    Only works while the session that created ``emitter_name`` is still open (use
    ``finalize=false`` on the preceding calls). Otherwise pass the module inline to
    ``add_niagara_emitter``.
    """
    sess, em = _open_emitter(system_path, emitter_name)
    results: dict = {"system_path": sess["key"], "emitter_name": emitter_name}
    _add_modules(
        em,
        sess,
        [
            {
                "name": module_name or _package_path(module_path).rsplit("/", 1)[-1],
                "module_path": module_path,
                "category": category,
                "parameters": parameters or [],
            }
        ],
        results,
    )
    if finalize:
        results.update(_finalize_session(sess))
    else:
        results["finalized"] = False
    return results


def set_niagara_module_parameter(
    system_path: str,
    emitter_name: str,
    module_name: str,
    parameters: List[dict],
    module_path: str = "",
    category: str = "particle_update",
    finalize: bool = True,
) -> dict:
    """Set inputs on a module of an emitter open in this session (adds it if missing).

    ``module_path`` is required when the module is not already staged.
    """
    sess, em = _open_emitter(system_path, emitter_name)
    ctx = em.find_module_script(module_name)
    added = False
    if ctx is None:
        if not module_path:
            raise ValueError(
                f"Module {module_name!r} is not staged on {emitter_name!r}; pass module_path to add it."
            )
        ctx = _script_context(module_path)
        added = True
    budget = {"nodes": 0}
    applied = _apply_parameters(
        ctx,
        parameters,
        system_key=sess["key"],
        where=f"{module_name} ({module_path or 'staged'})",
        budget=budget,
        depth=0,
    )
    if added:
        em.find_or_add_module_script(module_name, ctx, _exec_category(category))
    results = {
        "system_path": sess["key"],
        "emitter_name": emitter_name,
        "module_name": module_name,
        "added": added,
        "parameters_applied": applied,
    }
    if finalize:
        results.update(_finalize_session(sess))
    else:
        results["finalized"] = False
    return results


def add_niagara_renderer(
    system_path: str,
    emitter_name: str,
    renderer_type: str = "sprite",
    mesh: str = "",
    material: str = "",
    scale: Optional[List[float]] = None,
    renderer_name: str = "",
    finalize: bool = True,
) -> dict:
    """Add a mesh/sprite/ribbon/light renderer to an emitter open in this session.

    ``mesh`` must be a project-owned StaticMesh (create_niagara_mesh) — Engine
    shapes are rejected and cannot be duplicated in UEFN.
    """
    sess, em = _open_emitter(system_path, emitter_name)
    spec = {
        "type": renderer_type,
        "name": renderer_name or renderer_type,
        "mesh": mesh,
        "material": material,
        "scale": scale,
    }
    name, props = _make_renderer(sess["system"], spec)
    em.add_renderer(name, props)
    results = {
        "system_path": sess["key"],
        "emitter_name": emitter_name,
        "renderer": {"name": name, "type": renderer_type},
    }
    if finalize:
        results.update(_finalize_session(sess))
    else:
        results["finalized"] = False
    return results


def finalize_niagara_system(system_path: str) -> dict:
    """Compile and save the open conversion session for a system."""
    key = _package_path(system_path)
    sess = _SESSIONS.get(key)
    if sess is None:
        return {"system_path": key, "finalized": False, "note": "No open conversion session."}
    emitters = sorted(sess["emitters"])
    out = {"system_path": key, "emitters": emitters}
    out.update(_finalize_session(sess))
    return out


_MESH_SHAPES = ("sphere", "box", "cylinder", "cone", "torus", "capsule", "disc")


def create_niagara_mesh(
    asset_name: str,
    shape: str = "sphere",
    folder: str = "",
    radius: float = 50.0,
    height: float = 100.0,
    size: Optional[List[float]] = None,
    steps: int = 16,
    scale: Optional[List[float]] = None,
    material: str = "",
    replace: bool = False,
) -> dict:
    """Create a project-owned StaticMesh (Geometry Script) for Niagara mesh particles.

    UEFN mesh particles must not reference ``/Engine/BasicShapes`` (and those assets
    cannot be duplicated into a project — duplicate_asset returns None). Bake the
    look by passing ``material`` (a project material/MI) instead of using Niagara
    ``override_materials``.
    """
    prims = getattr(unreal, "GeometryScript_Primitives", None)
    new_assets = getattr(unreal, "GeometryScript_NewAssetUtils", None)
    if prims is None or new_assets is None:
        raise ValueError(
            "Geometry Scripting is not exposed in this UEFN build "
            "(GeometryScript_Primitives / GeometryScript_NewAssetUtils missing)."
        )
    kind = (shape or "sphere").strip().lower()
    if kind not in _MESH_SHAPES:
        raise ValueError(f"Unknown shape {shape!r}. Use one of {list(_MESH_SHAPES)}")

    folder = pin_project_folder(folder, default_leaf="Meshes")
    path = _reject_junk_path(f"{folder.rstrip('/')}/{asset_name}", "static mesh")
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        if not replace:
            raise ValueError(f"Asset already exists: {path} (pass replace=true to rebuild)")
        unreal.EditorAssetLibrary.delete_asset(path)
    unreal.EditorAssetLibrary.make_directory(folder)

    mesh = unreal.new_object(unreal.DynamicMesh)
    opts = unreal.GeometryScriptPrimitiveOptions()
    xf = unreal.Transform()
    steps = max(4, min(int(steps), 64))
    dims = [float(v) for v in (size or [100.0, 100.0, 100.0])][:3]
    while len(dims) < 3:
        dims.append(dims[-1])
    if kind == "sphere":
        prims.append_sphere_lat_long(mesh, opts, xf, float(radius), steps, steps)
    elif kind == "box":
        prims.append_box(mesh, opts, xf, dims[0], dims[1], dims[2])
    elif kind == "cylinder":
        prims.append_cylinder(mesh, opts, xf, float(radius), float(height), steps, 1)
    elif kind == "cone":
        prims.append_cone(mesh, opts, xf, float(radius), float(radius) * 0.05, float(height), steps, 1)
    elif kind == "torus":
        revolve_cls = getattr(unreal, "GeometryScriptRevolveOptions", None)
        major, minor = float(radius), float(radius) * 0.25
        if revolve_cls is not None:
            prims.append_torus(mesh, opts, xf, revolve_cls(), major, minor, steps, steps)
        else:
            prims.append_torus(mesh, opts, xf, major, minor, steps, steps)
    elif kind == "capsule":
        prims.append_capsule(mesh, opts, xf, float(radius), float(height), steps, steps)
    else:
        prims.append_disc(mesh, opts, xf, float(radius), steps)

    if scale:
        transforms = getattr(unreal, "GeometryScript_MeshTransforms", None)
        if transforms is not None:
            vals = [float(v) for v in list(scale)[:3]]
            while len(vals) < 3:
                vals.append(1.0)
            transforms.scale_mesh(mesh, unreal.Vector(*vals), unreal.Vector(0.0, 0.0, 0.0))

    create_opts = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    result = new_assets.create_new_static_mesh_asset_from_mesh(mesh, path, create_opts)
    static_mesh = result[0] if isinstance(result, tuple) else result
    if static_mesh is None:
        raise RuntimeError(f"create_new_static_mesh_asset_from_mesh returned None for {path}")

    applied_material = None
    if material:
        mat = _load_asset(_package_path(material))
        slots = list(static_mesh.get_editor_property("static_materials"))
        if slots:
            slots[0].set_editor_property("material_interface", mat)
            static_mesh.set_editor_property("static_materials", slots)
            applied_material = _package_path(material)
    unreal.EditorAssetLibrary.save_loaded_asset(static_mesh, only_if_is_dirty=False)
    return {
        "mesh_path": path,
        "shape": kind,
        "material": applied_material,
        "folder": folder,
    }


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
register("list_niagara_user_parameters")(list_niagara_user_parameters)
register("create_niagara_system")(create_niagara_system)
register("create_niagara_mesh")(create_niagara_mesh)
register("add_niagara_emitter")(add_niagara_emitter)
register("add_niagara_module")(add_niagara_module)
register("set_niagara_module_parameter")(set_niagara_module_parameter)
register("add_niagara_renderer")(add_niagara_renderer)
register("finalize_niagara_system")(finalize_niagara_system)
register("get_niagara_component_info")(get_niagara_component_info)
register("set_niagara_component_parameter")(set_niagara_component_parameter)
register("control_niagara_actor")(control_niagara_actor)
