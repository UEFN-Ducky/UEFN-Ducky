"""Animation registry tools: IK Rig creation and animation retargeting.

Composable primitives — NOT one do-it-all tool. Each does a single job so the
agent can chain them in different orders for different tasks (build a rig, fix
one chain, reuse a rig across many anims, inspect before editing):

  READ    ik_retarget_capabilities, list_skeleton_bones, get_retarget_preset,
          get_ik_rig_info, get_ik_retargeter_info, list_skeleton_sockets,
          get_skeletal_mesh_info
  CREATE  create_ik_rig_asset, create_ik_retargeter_asset
  CHANGE  set_retarget_root, add_retarget_chains, remove_retarget_chains,
          auto_map_retarget_chains, add_skeleton_socket, remove_skeleton_socket
  BAKE    retarget_animation
  COMPOSE retarget_animation_pipeline   (thin convenience over the above)

Why explicit chains: engine "Auto Characterize" only knows Epic Mannequin bone
names (pelvis/spine_01/upperarm_l…). A 3ds Max Biped skeleton uses ``Bip001-*``,
so auto-characterize matches nothing and makes zero chains. ``get_retarget_preset``
supplies the Biped (and Mannequin) chain table; ``add_retarget_chains`` applies it.

The IK Rig editor classes live in engine plugins a given UEFN build may or may
not expose, and method names shift between UE versions. Every tool guards on
availability and, on a method miss, RETURNS the members that ARE present
(self-describing probe) instead of crashing. If the API is absent, use the FBX
round-trip in the ``animation`` skill.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import unreal

from listener.dispatch import register
from listener.serialize import rotator_pyr

_IK_CLASSES = (
    "IKRigDefinition",
    "IKRigDefinitionFactory",
    "IKRigController",
    "IKRetargeter",
    "IKRetargetFactory",
    "IKRetargeterController",
    "IKRetargetBatchOperation",
)

_CORE_CLASSES = ("IKRigDefinition", "IKRigController", "IKRetargeter", "IKRetargeterController")

# Chain presets: name -> {root bone, [ {name,start,end} ]}. Chain NAMES are
# identical across presets so source and target rigs auto-map by name. The Biped
# table was validated live in the editor for Corpse_Sword.
RETARGET_CHAIN_PRESETS: dict = {
    "biped": {
        "root": "Bip001-Pelvis",
        "chains": [
            {"name": "Spine", "start": "Bip001-Spine", "end": "Bip001-Spine2"},
            {"name": "Head", "start": "Bip001-Neck", "end": "Bip001-Head"},
            {"name": "LeftArm", "start": "Bip001-L-UpperArm", "end": "Bip001-L-Hand"},
            {"name": "RightArm", "start": "Bip001-R-UpperArm", "end": "Bip001-R-Hand"},
            {"name": "LeftLeg", "start": "Bip001-L-Thigh", "end": "Bip001-L-Foot"},
            {"name": "RightLeg", "start": "Bip001-R-Thigh", "end": "Bip001-R-Foot"},
            {"name": "LeftClavicle", "start": "Bip001-L-Clavicle", "end": "Bip001-L-Clavicle"},
            {"name": "RightClavicle", "start": "Bip001-R-Clavicle", "end": "Bip001-R-Clavicle"},
        ],
    },
    "mannequin": {
        "root": "pelvis",
        "chains": [
            {"name": "Spine", "start": "spine_01", "end": "spine_03"},
            {"name": "Head", "start": "neck_01", "end": "head"},
            {"name": "LeftArm", "start": "upperarm_l", "end": "hand_l"},
            {"name": "RightArm", "start": "upperarm_r", "end": "hand_r"},
            {"name": "LeftLeg", "start": "thigh_l", "end": "foot_l"},
            {"name": "RightLeg", "start": "thigh_r", "end": "foot_r"},
            {"name": "LeftClavicle", "start": "clavicle_l", "end": "clavicle_l"},
            {"name": "RightClavicle", "start": "clavicle_r", "end": "clavicle_r"},
        ],
    },
}


# --- shared helpers ---------------------------------------------------------


def _asset_tools():
    return unreal.AssetToolsHelpers.get_asset_tools()


def _members(obj: Any) -> List[str]:
    """Public callable member names — makes 'method not found' errors actionable."""
    out = []
    for n in dir(obj):
        if n.startswith("_"):
            continue
        try:
            if callable(getattr(obj, n)):
                out.append(n)
        except Exception:
            continue
    return sorted(out)


def _capabilities() -> dict:
    return {name: hasattr(unreal, name) for name in _IK_CLASSES}


def _load_asset(path: str):
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is None:
        raise ValueError(f"Asset not found: {path}")
    return asset


def _save(asset: Any) -> None:
    unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False)


def _first_method(obj: Any, names: List[str]):
    for n in names:
        fn = getattr(obj, n, None)
        if callable(fn):
            return n, fn
    return None, None


def _path_of(obj: Any) -> str:
    getter = getattr(obj, "get_path_name", None)
    return str(getter()) if callable(getter) else str(obj)


def _rig_controller(ik_rig_path: str) -> Tuple[Any, Any, Optional[dict]]:
    """(rig, controller, error). error is a ready-to-return dict or None."""
    caps = _capabilities()
    if not caps["IKRigController"]:
        return None, None, {"ok": False, "error": "IK Rig API not available", "capabilities": caps}
    rig = _load_asset(ik_rig_path)
    get = getattr(unreal.IKRigController, "get_controller", None)
    controller = get(rig) if callable(get) else None
    if controller is None:
        return rig, None, {
            "ok": False,
            "error": "IKRigController.get_controller unavailable",
            "ik_rig_controller_methods": _members(unreal.IKRigController),
        }
    return rig, controller, None


def _retargeter_controller(path: str) -> Tuple[Any, Any, Optional[dict]]:
    caps = _capabilities()
    if not caps["IKRetargeterController"]:
        return None, None, {"ok": False, "error": "IK Retargeter API not available", "capabilities": caps}
    retargeter = _load_asset(path)
    get = getattr(unreal.IKRetargeterController, "get_controller", None)
    controller = get(retargeter) if callable(get) else None
    if controller is None:
        return retargeter, None, {
            "ok": False,
            "error": "IKRetargeterController.get_controller unavailable",
            "ik_retargeter_controller_methods": _members(unreal.IKRetargeterController),
        }
    return retargeter, controller, None


def _mesh_bones(mesh: Any) -> List[str]:
    """Bone names off a skeletal mesh via a transient component. Verified on UEFN —
    ``IKRigController`` has no ``get_skeleton()`` there, so this is the reliable path."""
    try:
        comp = unreal.SkeletalMeshComponent()
        _, setter = _first_method(comp, ["set_skeletal_mesh", "set_skinned_asset_and_update"])
        if setter:
            setter(mesh)
        get_num = getattr(comp, "get_num_bones", None)
        get_name = getattr(comp, "get_bone_name", None)
        if callable(get_num) and callable(get_name):
            n = int(get_num())
            return [str(get_name(i)) for i in range(n)]
    except Exception:
        pass
    return []


def _controller_bones(controller: Any) -> List[str]:
    # Some UE builds expose get_skeleton().bone_names; UEFN does not, so fall back to
    # reading the bound skeletal mesh (get_skeletal_mesh IS present there).
    getter = getattr(controller, "get_skeleton", None)
    if callable(getter):
        try:
            names = getattr(getter(), "bone_names", None)
            if names:
                return [str(n) for n in names]
        except Exception:
            pass
    get_mesh = getattr(controller, "get_skeletal_mesh", None)
    if callable(get_mesh):
        try:
            mesh = get_mesh()
            if mesh is not None:
                return _mesh_bones(mesh)
        except Exception:
            pass
    return []


def _guess_preset(bones: List[str]) -> str:
    if not bones:
        return "unknown"
    if any(str(b).lower().replace(" ", "").startswith("bip001") for b in bones):
        return "biped"
    s = {str(b).lower() for b in bones}
    if "pelvis" in s and ("spine_01" in s or "upperarm_l" in s):
        return "mannequin"
    return "unknown"


def _chain_to_dict(ch: Any) -> dict:
    def bone(x):
        return str(getattr(x, "bone_name", x)) if x is not None else None

    return {
        "name": str(getattr(ch, "chain_name", "") or ""),
        "start": bone(getattr(ch, "start_bone", None)),
        "end": bone(getattr(ch, "end_bone", None)),
        "goal": str(getattr(ch, "ik_goal_name", "") or ""),
    }


def _add_chains(controller: Any, chains: List[dict], bones: List[str]) -> dict:
    _, add = _first_method(controller, ["add_retarget_chain"])
    if not add:
        return {"error": "add_retarget_chain not found", "controller_methods": _members(controller)}
    boneset = set(bones)
    added: List[str] = []
    skipped: List[dict] = []
    for ch in chains:
        name, start, end = ch.get("name"), ch.get("start"), ch.get("end")
        goal = ch.get("goal", "")
        if not (name and start and end):
            skipped.append({"chain": name, "reason": "missing name/start/end"})
            continue
        if boneset and (start not in boneset or end not in boneset):
            missing = [b for b in (start, end) if b not in boneset]
            skipped.append({"chain": name, "reason": f"bone(s) not in skeleton: {missing}"})
            continue
        try:
            add(name, start, end, goal)
            added.append(name)
        except Exception as e:
            skipped.append({"chain": name, "reason": str(e)})
    return {"added": added, "skipped": skipped}


# --- READ -------------------------------------------------------------------


def ik_retarget_capabilities() -> dict:
    """Report whether this UEFN build exposes the IK Rig / retarget API (call first)."""
    caps = _capabilities()
    out: dict = {"capabilities": caps, "available": all(caps[c] for c in _CORE_CLASSES)}
    if caps["IKRigController"]:
        out["ik_rig_controller_methods"] = _members(unreal.IKRigController)
    if caps["IKRetargeterController"]:
        out["ik_retargeter_controller_methods"] = _members(unreal.IKRetargeterController)
    if caps["IKRetargetBatchOperation"]:
        out["batch_operation_methods"] = _members(unreal.IKRetargetBatchOperation)
    return out


def list_skeleton_bones(skeletal_mesh_path: str) -> dict:
    """List a skeletal mesh's bone names and guess its chain preset (biped/mannequin)."""
    mesh = _load_asset(skeletal_mesh_path)
    names = _mesh_bones(mesh)
    if names:
        return {"ok": True, "count": len(names), "preset_guess": _guess_preset(names), "bones": names}
    return {
        "ok": False,
        "error": "could not enumerate bones; create_ik_rig_asset also returns bone_count + preset_guess",
    }


def get_retarget_preset(name: str = "") -> dict:
    """Return a retarget chain preset's root + chains as data (no UEFN needed).

    Feed the ``chains`` into ``add_retarget_chains``. With no ``name``, lists all
    presets. Pair with ``create_ik_rig_asset``'s ``preset_guess`` to pick one.
    """
    if not name:
        return {"presets": sorted(RETARGET_CHAIN_PRESETS), "detail": RETARGET_CHAIN_PRESETS}
    preset = RETARGET_CHAIN_PRESETS.get(name)
    if not preset:
        return {"ok": False, "error": f"unknown preset: {name}", "presets": sorted(RETARGET_CHAIN_PRESETS)}
    return {"ok": True, "name": name, "root": preset["root"], "chains": list(preset["chains"])}


def get_ik_rig_info(ik_rig_path: str) -> dict:
    """Read an IK Rig's retarget root and chains — verify a rig before/after edits."""
    _, controller, err = _rig_controller(ik_rig_path)
    if err:
        return err
    info: dict = {"ok": True, "ik_rig": ik_rig_path}
    _, get_root = _first_method(controller, ["get_retarget_root"])
    if get_root:
        try:
            info["retarget_root"] = str(get_root())
        except Exception as e:
            info["retarget_root_error"] = str(e)
    _, get_chains = _first_method(controller, ["get_retarget_chains"])
    if get_chains:
        try:
            info["chains"] = [_chain_to_dict(c) for c in (get_chains() or [])]
        except Exception as e:
            info["chains_error"] = str(e)
    else:
        info["chains_error"] = "get_retarget_chains not found"
        info["controller_methods"] = _members(controller)
    return info


def get_ik_retargeter_info(ik_retargeter_path: str) -> dict:
    """Read an IK Retargeter's source/target rigs (best effort chain mapping too)."""
    _, controller, err = _retargeter_controller(ik_retargeter_path)
    if err:
        return err
    info: dict = {"ok": True, "ik_retargeter": ik_retargeter_path}
    enum = getattr(unreal, "RetargetSourceOrTarget", None)
    get_ik = getattr(controller, "get_ik_rig", None)
    if callable(get_ik) and enum is not None:
        try:
            info["source_rig"] = _path_of(get_ik(enum.SOURCE))
            info["target_rig"] = _path_of(get_ik(enum.TARGET))
        except Exception as e:
            info["rigs_error"] = str(e)
    _, get_map = _first_method(controller, ["get_chain_mappings", "get_chain_mapping"])
    info["has_chain_mapping_api"] = bool(get_map)
    return info


# --- CREATE -----------------------------------------------------------------


def create_ik_rig_asset(skeletal_mesh_path: str, dest_folder: str, name: str) -> dict:
    """Create an IK Rig asset and bind its skeletal mesh. Returns bone_count and
    a preset_guess. Then set the root + chains with the CHANGE tools."""
    caps = _capabilities()
    if not (caps["IKRigDefinition"] and caps["IKRigController"]):
        return {"ok": False, "error": "IK Rig API not available", "capabilities": caps,
                "fallback": "Use a prebuilt IK rig or the FBX round-trip (animation skill)."}
    mesh = _load_asset(skeletal_mesh_path)
    unreal.EditorAssetLibrary.make_directory(dest_folder)
    factory = unreal.IKRigDefinitionFactory() if caps["IKRigDefinitionFactory"] else None
    ik_rig = _asset_tools().create_asset(name, dest_folder, unreal.IKRigDefinition, factory)
    if ik_rig is None:
        return {"ok": False, "error": f"create_asset returned None for {dest_folder}/{name} (bad path or name clash)"}
    _, controller, err = _rig_controller(f"{dest_folder}/{name}")
    if err:
        return err
    out: dict = {"ok": True, "ik_rig": f"{dest_folder}/{name}"}
    _, set_mesh = _first_method(controller, ["set_skeletal_mesh"])
    if set_mesh:
        set_mesh(mesh)
        out["skeletal_mesh"] = skeletal_mesh_path
    else:
        out["skeletal_mesh_error"] = "set_skeletal_mesh not found"
        out["controller_methods"] = _members(controller)
    bones = _mesh_bones(mesh) or _controller_bones(controller)
    if bones:
        out["bone_count"] = len(bones)
        out["preset_guess"] = _guess_preset(bones)
        if out["preset_guess"] == "unknown":
            out["bones_sample"] = bones[:60]
    _save(ik_rig)
    return out


def create_ik_retargeter_asset(
    source_ik_rig_path: str, target_ik_rig_path: str, dest_folder: str, name: str
) -> dict:
    """Create an IK Retargeter and bind its source + target IK Rigs. Map chains
    separately with ``auto_map_retarget_chains``."""
    caps = _capabilities()
    if not (caps["IKRetargeter"] and caps["IKRetargeterController"]):
        return {"ok": False, "error": "IK Retargeter API not available", "capabilities": caps}
    source_rig = _load_asset(source_ik_rig_path)
    target_rig = _load_asset(target_ik_rig_path)
    unreal.EditorAssetLibrary.make_directory(dest_folder)
    factory = unreal.IKRetargetFactory() if caps["IKRetargetFactory"] else None
    retargeter = _asset_tools().create_asset(name, dest_folder, unreal.IKRetargeter, factory)
    if retargeter is None:
        return {"ok": False, "error": f"create_asset returned None for {dest_folder}/{name}"}
    _, controller, err = _retargeter_controller(f"{dest_folder}/{name}")
    if err:
        return err
    out: dict = {"ok": True, "ik_retargeter": f"{dest_folder}/{name}"}
    rigs_set = False
    enum = getattr(unreal, "RetargetSourceOrTarget", None)
    set_ik = getattr(controller, "set_ik_rig", None)
    if callable(set_ik) and enum is not None:
        try:
            set_ik(enum.SOURCE, source_rig)
            set_ik(enum.TARGET, target_rig)
            rigs_set = True
            out["ik_rigs"] = "set via set_ik_rig(SOURCE/TARGET)"
        except Exception as e:
            out["ik_rigs_error"] = f"set_ik_rig: {e}"
    if not rigs_set:
        _, set_src = _first_method(controller, ["set_source_ik_rig"])
        _, set_tgt = _first_method(controller, ["set_target_ik_rig"])
        if set_src and set_tgt:
            set_src(source_rig)
            set_tgt(target_rig)
            rigs_set = True
            out["ik_rigs"] = "set via set_source/target_ik_rig"
    if not rigs_set:
        out["ik_rigs_error"] = "could not find a set-IK-rig method"
        out["controller_methods"] = _members(controller)
    _save(retargeter)
    return out


# --- CHANGE -----------------------------------------------------------------


def set_retarget_root(ik_rig_path: str, bone: str) -> dict:
    """Set an IK Rig's retarget root (the pelvis/hips bone)."""
    rig, controller, err = _rig_controller(ik_rig_path)
    if err:
        return err
    _, setr = _first_method(controller, ["set_retarget_root"])
    if not setr:
        return {"ok": False, "error": "set_retarget_root not found", "controller_methods": _members(controller)}
    out: dict = {"ok": True, "ik_rig": ik_rig_path, "retarget_root": bone}
    bones = _controller_bones(controller)
    if bones and bone not in bones:
        out["warning"] = f"{bone!r} not found in skeleton"
    setr(bone)
    _save(rig)
    return out


def add_retarget_chains(ik_rig_path: str, chains: List[dict], replace_existing: bool = False) -> dict:
    """Add (or replace) retarget chains on an IK Rig. ``chains`` is
    ``[{name,start,end[,goal]}]``; bones are validated against the skeleton."""
    rig, controller, err = _rig_controller(ik_rig_path)
    if err:
        return err
    if replace_existing:
        _, get_chains = _first_method(controller, ["get_retarget_chains"])
        _, remove = _first_method(controller, ["remove_retarget_chain"])
        if get_chains and remove:
            try:
                for ch in get_chains() or []:
                    nm = getattr(ch, "chain_name", None) or ch
                    try:
                        remove(nm)
                    except Exception:
                        continue
            except Exception:
                pass
    res = _add_chains(controller, chains, _controller_bones(controller))
    _save(rig)
    if res.get("error"):
        return {"ok": False, "ik_rig": ik_rig_path, **res}
    return {"ok": True, "ik_rig": ik_rig_path, "added": res["added"], "skipped": res["skipped"]}


def remove_retarget_chains(ik_rig_path: str, names: List[str]) -> dict:
    """Remove named retarget chains from an IK Rig."""
    rig, controller, err = _rig_controller(ik_rig_path)
    if err:
        return err
    _, remove = _first_method(controller, ["remove_retarget_chain"])
    if not remove:
        return {"ok": False, "error": "remove_retarget_chain not found", "controller_methods": _members(controller)}
    removed, failed = [], []
    for nm in names:
        try:
            remove(nm)
            removed.append(nm)
        except Exception as e:
            failed.append({"chain": nm, "reason": str(e)})
    _save(rig)
    return {"ok": True, "ik_rig": ik_rig_path, "removed": removed, "failed": failed}


def auto_map_retarget_chains(ik_retargeter_path: str) -> dict:
    """Auto-map source→target chains on an IK Retargeter (pairs by identical name)."""
    retargeter, controller, err = _retargeter_controller(ik_retargeter_path)
    if err:
        return err
    a_name, auto_fn = _first_method(controller, ["auto_map_chains"])
    if not auto_fn:
        return {"ok": False, "error": "auto_map_chains not found", "controller_methods": _members(controller)}
    try:
        map_type = getattr(unreal, "AutoMapChainType", None)
        if map_type is not None:
            auto_fn(map_type.EXACT, True)
        else:
            auto_fn()
    except Exception as e:
        return {"ok": False, "error": f"{a_name}: {e}", "controller_methods": _members(controller)}
    _save(retargeter)
    return {"ok": True, "ik_retargeter": ik_retargeter_path, "method": a_name}


# --- BAKE -------------------------------------------------------------------


def retarget_animation(
    ik_retargeter_path: str,
    source_mesh_path: str,
    target_mesh_path: str,
    anim_paths: List[str],
    prefix: str = "",
    suffix: str = "_Retargeted",
) -> dict:
    """Bake source AnimSequences onto the target skeleton via an existing IK
    Retargeter. Duplicated assets land beside the source with prefix/suffix."""
    caps = _capabilities()
    if not caps["IKRetargetBatchOperation"]:
        return {"ok": False, "error": "IKRetargetBatchOperation not available", "capabilities": caps}
    retargeter = _load_asset(ik_retargeter_path)
    source_mesh = _load_asset(source_mesh_path)
    target_mesh = _load_asset(target_mesh_path)
    anims = [_load_asset(p) for p in anim_paths]

    dup = getattr(unreal.IKRetargetBatchOperation, "duplicate_and_retarget", None)
    if callable(dup):
        try:
            created = dup(anims, source_mesh, target_mesh, retargeter, "", "", prefix, suffix, True)
            paths = [_path_of(c) for c in (created or [])]
            return {"ok": True, "method": "duplicate_and_retarget", "created": paths, "count": len(paths)}
        except Exception as e:
            err = f"duplicate_and_retarget: {e}"
    else:
        err = "duplicate_and_retarget not found"
    return {
        "ok": False,
        "error": "batch retarget API differs on this build — adapt via execute_python",
        "detail": err,
        "batch_operation_methods": _members(unreal.IKRetargetBatchOperation),
    }


# --- COMPOSE (thin convenience over the primitives) -------------------------


def retarget_animation_pipeline(
    source_mesh_path: str,
    target_mesh_path: str,
    anim_path: str,
    dest_folder: str = "/Game/Retargeting",
    source_preset: str = "auto",
    target_preset: str = "auto",
    suffix: str = "_Retargeted",
) -> dict:
    """Convenience: chain the primitives for the common single-animation case.
    For anything non-standard, call the primitives directly instead."""
    caps = _capabilities()
    if not all(caps[c] for c in _CORE_CLASSES) or not caps["IKRetargetBatchOperation"]:
        return {"ok": False, "error": "Full IK retarget pipeline not available in this build",
                "capabilities": caps, "fallback": "Use the FBX round-trip (animation skill)."}

    def _stem(path: str) -> str:
        return path.rstrip("/").rsplit("/", 1)[-1].split(".")[0]

    def _build_rig(mesh_path: str, rig_name: str, preset_arg: str) -> dict:
        rig = create_ik_rig_asset(mesh_path, dest_folder, rig_name)
        if not rig.get("ok"):
            return rig
        key = preset_arg if preset_arg != "auto" else rig.get("preset_guess", "unknown")
        rig["preset"] = key
        preset = RETARGET_CHAIN_PRESETS.get(key)
        if not preset:
            rig["chains_error"] = "preset unknown — call get_retarget_preset / add_retarget_chains manually"
            return rig
        rig["root"] = set_retarget_root(f"{dest_folder}/{rig_name}", preset["root"])
        rig["chains"] = add_retarget_chains(f"{dest_folder}/{rig_name}", preset["chains"], replace_existing=True)
        return rig

    src_name, tgt_name = _stem(source_mesh_path), _stem(target_mesh_path)
    report: dict = {}
    report["source_rig"] = _build_rig(source_mesh_path, f"IK_{src_name}", source_preset)
    report["target_rig"] = _build_rig(target_mesh_path, f"IK_{tgt_name}", target_preset)
    if not (report["source_rig"].get("ok") and report["target_rig"].get("ok")):
        return {"ok": False, "stage": "ik_rig", "report": report}

    rtg_name = f"RTG_{src_name}_to_{tgt_name}"
    report["retargeter"] = create_ik_retargeter_asset(
        f"{dest_folder}/IK_{src_name}", f"{dest_folder}/IK_{tgt_name}", dest_folder, rtg_name
    )
    if not report["retargeter"].get("ok"):
        return {"ok": False, "stage": "retargeter", "report": report}
    report["auto_map"] = auto_map_retarget_chains(f"{dest_folder}/{rtg_name}")
    report["bake"] = retarget_animation(
        f"{dest_folder}/{rtg_name}", source_mesh_path, target_mesh_path, [anim_path], "", suffix
    )
    unreal.EditorAssetLibrary.save_directory(dest_folder, only_if_is_dirty=False, recursive=True)
    return {"ok": bool(report["bake"].get("ok")), "report": report, "output_folder": dest_folder}


# --- skeleton sockets --------------------------------------------------------
#
# Agents kept freehanding socket creation via execute_python with
# ``unreal.SkeletalMeshSocket()`` — direct construction of an asset-owned UObject
# with no outer. Stitching that into a skeleton is a native access violation that
# Python cannot catch (kills the whole editor). These tools do it the sanctioned
# way: ``unreal.new_object(cls, outer=skeleton)`` + property writes + save.


def _resolve_skeleton(asset_path: str) -> Tuple[Any, Any, Optional[dict]]:
    """(skeleton, skeletal_mesh_or_None, error). Accepts a Skeleton or SkeletalMesh path."""
    asset = _load_asset(asset_path)
    cls = asset.get_class().get_name()
    if cls == "Skeleton":
        return asset, None, None
    skel = None
    try:
        skel = asset.get_editor_property("skeleton")
    except Exception:
        skel = None
    if skel is None:
        return None, None, {
            "ok": False,
            "error": f"Not a Skeleton or SkeletalMesh (class {cls}): {asset_path}",
        }
    return skel, asset, None


def _socket_rows(skel: Any) -> List[dict]:
    rows: List[dict] = []
    try:
        sockets = list(skel.get_editor_property("sockets") or [])
    except Exception:
        return rows
    for s in sockets:
        try:
            loc = s.get_editor_property("relative_location")
            rot = s.get_editor_property("relative_rotation")
            rows.append(
                {
                    "socket": str(s.get_editor_property("socket_name")),
                    "bone": str(s.get_editor_property("bone_name")),
                    "location": [loc.x, loc.y, loc.z],
                    "rotation": [rot.pitch, rot.yaw, rot.roll],
                }
            )
        except Exception:
            continue
    return rows


def list_skeleton_sockets(asset_path: str) -> dict:
    """List sockets on a Skeleton (pass a Skeleton or SkeletalMesh path)."""
    skel, _mesh, err = _resolve_skeleton(asset_path)
    if err:
        return err
    rows = _socket_rows(skel)
    return {"ok": True, "skeleton": _path_of(skel), "count": len(rows), "sockets": rows}


def get_skeletal_mesh_info(asset_path: str) -> dict:
    """One-call skeletal mesh inspection: skeleton, bones, sockets, materials, LODs, bounds.

    Replaces the execute_python probe loop agents were doing (load → dir() → poke) —
    everything a task needs to plan socket placement or retargeting in one READ.
    """
    mesh = _load_asset(asset_path)
    cls = mesh.get_class().get_name()
    if cls != "SkeletalMesh":
        return {"ok": False, "error": f"Not a SkeletalMesh (class {cls}): {asset_path}"}

    info: dict = {"ok": True, "mesh": _path_of(mesh), "class": cls}

    skel = None
    try:
        skel = mesh.get_editor_property("skeleton")
    except Exception:
        pass
    info["skeleton"] = _path_of(skel) if skel is not None else None

    bones = _mesh_bones(mesh)
    info["bone_count"] = len(bones)
    info["preset_guess"] = _guess_preset(bones)
    info["bones"] = bones

    info["sockets"] = _socket_rows(skel) if skel is not None else []

    mats = []
    try:
        for m in mesh.get_editor_property("materials") or []:
            try:
                slot = str(m.get_editor_property("material_slot_name"))
                iface = m.get_editor_property("material_interface")
                mats.append({"slot": slot, "material": _path_of(iface) if iface else None})
            except Exception:
                continue
    except Exception:
        pass
    info["materials"] = mats

    for attr, key in (("get_lod_num", "lod_count"),):
        fn = getattr(mesh, attr, None)
        if callable(fn):
            try:
                info[key] = int(fn())
            except Exception:
                pass
    try:
        b = mesh.get_bounds()
        origin, extent = b.origin, b.box_extent
        info["bounds"] = {
            "origin": [origin.x, origin.y, origin.z],
            "extent": [extent.x, extent.y, extent.z],
        }
    except Exception:
        pass
    return info


def add_skeleton_socket(
    asset_path: str,
    bone_name: str,
    socket_name: str,
    location: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
    update_existing: bool = False,
) -> dict:
    """Add a socket to a Skeleton under ``bone_name`` (safe alternative to execute_python).

    ``asset_path`` may be the Skeleton or its SkeletalMesh. location=[x,y,z] and
    rotation=[pitch,yaw,roll] are relative to the bone. ``update_existing=true``
    re-fits an existing socket (iterate placement) instead of erroring. Attach things
    afterwards (attach_actor rule="snap_to_target" / component socket fields).
    """
    if not hasattr(unreal, "SkeletalMeshSocket"):
        return {"ok": False, "error": "SkeletalMeshSocket class not exposed in this UEFN build"}
    if not (socket_name or "").strip() or not (bone_name or "").strip():
        return {"ok": False, "error": "bone_name and socket_name are required"}

    skel, mesh, err = _resolve_skeleton(asset_path)
    if err:
        return err

    # Validate the bone when we can enumerate it (mesh path given). A typo'd bone
    # makes a socket that silently never renders — cheap to catch here.
    if mesh is not None:
        bones = _mesh_bones(mesh)
        if bones and bone_name not in bones:
            close = [b for b in bones if bone_name.lower() in b.lower()]
            return {"ok": False, "error": f"Bone not found: {bone_name}", "did_you_mean": close[:10]}

    try:
        sockets = list(skel.get_editor_property("sockets") or [])
        current = None
        for s in sockets:
            if str(s.get_editor_property("socket_name")).lower() == socket_name.lower():
                current = s
                break
        if current is not None and not update_existing:
            return {
                "ok": False,
                "error": f"Socket already exists: {socket_name} (pass update_existing=true to re-fit)",
                "sockets": _socket_rows(skel),
            }

        skel.modify()
        if current is None:
            # new_object with the skeleton as outer — the ONLY safe way to make an
            # asset-owned subobject from Python. Never unreal.SkeletalMeshSocket().
            current = unreal.new_object(unreal.SkeletalMeshSocket, skel)
            current.set_editor_property("socket_name", socket_name)
            sockets.append(current)
            updated = False
        else:
            updated = True
        current.set_editor_property("bone_name", bone_name)
        if location:
            current.set_editor_property("relative_location", unreal.Vector(*location))
        if rotation:
            current.set_editor_property("relative_rotation", rotator_pyr(*rotation))
        skel.set_editor_property("sockets", sockets)
        _save(skel)
    except Exception as e:
        return {"ok": False, "error": f"add socket failed: {e}", "skeleton_members": _members(skel)[:40]}

    return {
        "ok": True,
        "skeleton": _path_of(skel),
        "socket": socket_name,
        "bone": bone_name,
        "updated": updated,
        "count": len(_socket_rows(skel)),
    }


def remove_skeleton_socket(asset_path: str, socket_name: str) -> dict:
    """Remove a named socket from a Skeleton (pass a Skeleton or SkeletalMesh path)."""
    skel, _mesh, err = _resolve_skeleton(asset_path)
    if err:
        return err
    try:
        sockets = list(skel.get_editor_property("sockets") or [])
        keep = [
            s for s in sockets
            if str(s.get_editor_property("socket_name")).lower() != socket_name.lower()
        ]
        if len(keep) == len(sockets):
            return {
                "ok": False,
                "error": f"Socket not found: {socket_name}",
                "sockets": [str(s.get_editor_property("socket_name")) for s in sockets],
            }
        skel.modify()
        skel.set_editor_property("sockets", keep)
        _save(skel)
    except Exception as e:
        return {"ok": False, "error": f"remove socket failed: {e}"}
    return {"ok": True, "skeleton": _path_of(skel), "removed": socket_name, "count": len(keep)}


# --- registration -----------------------------------------------------------

register("ik_retarget_capabilities")(ik_retarget_capabilities)
register("list_skeleton_bones")(list_skeleton_bones)
register("get_retarget_preset")(get_retarget_preset)
register("get_ik_rig_info")(get_ik_rig_info)
register("get_ik_retargeter_info")(get_ik_retargeter_info)
register("create_ik_rig_asset")(create_ik_rig_asset)
register("create_ik_retargeter_asset")(create_ik_retargeter_asset)
register("set_retarget_root")(set_retarget_root)
register("add_retarget_chains")(add_retarget_chains)
register("remove_retarget_chains")(remove_retarget_chains)
register("auto_map_retarget_chains")(auto_map_retarget_chains)
register("retarget_animation")(retarget_animation)
register("retarget_animation_pipeline")(retarget_animation_pipeline)
register("list_skeleton_sockets")(list_skeleton_sockets)
register("add_skeleton_socket")(add_skeleton_socket)
register("remove_skeleton_socket")(remove_skeleton_socket)
register("get_skeletal_mesh_info")(get_skeletal_mesh_info)
