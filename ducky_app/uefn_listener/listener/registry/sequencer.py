"""Animation authoring tools: Level Sequences and AnimSequence keyframes.

Composable primitives for CREATING animations (retargeting converts existing
ones — see animation_retarget):

  PROBE   anim_author_capabilities
  READ    get_sequence_info, get_anim_sequence_info
  CREATE  create_level_sequence, create_anim_sequence
  CHANGE  add_sequence_binding, add_transform_keys, set_anim_bone_keys

Known engine traps handled here (from UE docs/forums):
  * Sequencer keys silently vanish unless the section range covers them —
    add_transform_keys always extends ``section.set_range`` over all keys.
  * ``AnimDataController.set_bone_track_keys`` crashes the editor when the
    positional/rotational/scaling arrays differ in length or don't match the
    frame count — set_anim_bone_keys resamples every key to per-frame arrays.
Everything is capability-guarded per UEFN build and self-reports available
members on a miss instead of crashing.
"""

from __future__ import annotations

from typing import Any, Dict, List

import unreal

from listener import lookup
from listener.dispatch import register

_SEQUENCE_CLASSES = (
    "LevelSequence",
    "LevelSequenceFactoryNew",
    "MovieScene3DTransformTrack",
    "MovieSceneSequenceExtensions",
    "MovieSceneKeyInterpolation",
)

_ANIM_CLASSES = (
    "AnimSequence",
    "AnimSequenceFactory",
    "AnimDataController",
)


def _capabilities() -> dict:
    caps = {name: hasattr(unreal, name) for name in _SEQUENCE_CLASSES + _ANIM_CLASSES}
    return caps


def _require(name: str):
    cls = getattr(unreal, name, None)
    if cls is None:
        raise ValueError(f"unreal.{name} is not exposed in this UEFN build. Capabilities: {_capabilities()}")
    return cls


def _members(obj: Any, contains: str = "") -> List[str]:
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


def _save(asset: Any) -> None:
    unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False)


def _sequence_fps(sequence: Any) -> float:
    try:
        rate = sequence.get_display_rate()
        return float(rate.numerator) / float(max(1, rate.denominator))
    except Exception:
        return 30.0


def _rotator(roll: float, pitch: float, yaw: float):
    r = unreal.Rotator()
    r.roll = float(roll)
    r.pitch = float(pitch)
    r.yaw = float(yaw)
    return r


def anim_author_capabilities() -> dict:
    """Probe which animation-authoring classes this UEFN build exposes (run first)."""
    seq_ok = all(hasattr(unreal, n) for n in ("LevelSequence", "LevelSequenceFactoryNew"))
    anim_ok = all(hasattr(unreal, n) for n in ("AnimSequence", "AnimSequenceFactory"))
    return {
        "classes": _capabilities(),
        "level_sequence_route": seq_ok,
        "anim_sequence_route": anim_ok,
        "notes": [
            "Level Sequence route: create_level_sequence -> add_sequence_binding -> add_transform_keys; play via the Cinematic Sequence device.",
            "AnimSequence route: create_anim_sequence -> set_anim_bone_keys (bone-local transforms).",
            "Runtime prop animation needs no editor tools — Verse animation_controller + keyframe_delta (see the animation skill).",
        ],
    }


# ---------------------------------------------------------------------------
# Level Sequence route
# ---------------------------------------------------------------------------


def create_level_sequence(dest_folder: str, name: str, fps: int = 30, length_seconds: float = 5.0) -> dict:
    """Create a LevelSequence asset with the given display rate and playback length."""
    seq_cls = _require("LevelSequence")
    factory_cls = _require("LevelSequenceFactoryNew")
    unreal.EditorAssetLibrary.make_directory(dest_folder)
    full = f"{dest_folder}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        raise ValueError(f"Asset already exists: {full} (delete_asset first to replace)")
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    sequence = asset_tools.create_asset(name, dest_folder, seq_cls, factory_cls())
    if sequence is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    frames = max(1, int(round(float(length_seconds) * int(fps))))
    errors: Dict[str, str] = {}
    try:
        sequence.set_display_rate(unreal.FrameRate(int(fps), 1))
    except Exception as e:
        errors["set_display_rate"] = str(e)
    try:
        sequence.set_playback_start(0)
        sequence.set_playback_end(frames)
    except Exception as e:
        errors["set_playback_range"] = str(e)
    _save(sequence)
    out = {
        "sequence_path": str(sequence.get_path_name()),
        "fps": int(fps),
        "length_seconds": float(length_seconds),
        "frames": frames,
    }
    if errors:
        out["errors"] = errors
        out["sequence_members"] = _members(sequence, contains="playback")
    return out


def add_sequence_binding(sequence_path: str, actor_path: str) -> dict:
    """Bind a placed level actor into a LevelSequence as a possessable."""
    sequence = _load_asset(sequence_path)
    actor = lookup.require_actor(actor_path)
    add_possessable = getattr(sequence, "add_possessable", None)
    if not callable(add_possessable):
        raise ValueError(
            "add_possessable not available on this sequence (SequencerScripting not exposed). "
            f"Sequence members: {_members(sequence, contains='possess')}"
        )
    binding = add_possessable(actor)
    _save(sequence)
    return {
        "sequence_path": sequence_path,
        "actor_label": actor.get_actor_label(),
        "binding_name": str(binding.get_name()),
        "binding_display_name": str(binding.get_display_name()),
    }


def _find_binding(sequence: Any, binding_name: str):
    names = []
    for b in sequence.get_bindings():
        try:
            n, d = str(b.get_name()), str(b.get_display_name())
        except Exception:
            continue
        names.append(d or n)
        if binding_name in (n, d):
            return b
    raise ValueError(f"Binding not found: {binding_name!r}. Bindings in sequence: {names}")


_INTERP_NAMES = {"auto": "AUTO", "linear": "LINEAR", "constant": "CONSTANT"}


def _interp_value(interp: str):
    enum_cls = getattr(unreal, "MovieSceneKeyInterpolation", None)
    if enum_cls is None:
        return None
    return getattr(enum_cls, _INTERP_NAMES.get(interp.lower(), "AUTO"), None)


def add_transform_keys(sequence_path: str, binding_name: str, keys: List[dict]) -> dict:
    """Key a binding's transform. keys=[{time, location?, rotation?, scale?, interp?}].

    ``time`` is seconds; ``location`` [x,y,z] uu; ``rotation`` [roll,pitch,yaw]
    degrees (transform channel order); ``scale`` [x,y,z]; ``interp`` one of
    auto|linear|constant. Creates the transform track/section on first use and
    always extends the section range to cover every key (unkeyed channels are
    left alone).
    """
    if not keys:
        raise ValueError("keys is empty")
    track_cls = _require("MovieScene3DTransformTrack")
    sequence = _load_asset(sequence_path)
    binding = _find_binding(sequence, binding_name)
    tracks = [t for t in binding.get_tracks() if t.get_class().get_name() == "MovieScene3DTransformTrack"]
    track = tracks[0] if tracks else binding.add_track(track_cls)
    sections = track.get_sections()
    section = sections[0] if sections else track.add_section()

    fps = _sequence_fps(sequence)
    channels = section.get_all_channels()
    if len(channels) < 9:
        raise ValueError(
            f"Expected >=9 transform channels, got {len(channels)}. Section members: {_members(section)[:40]}"
        )
    # Channel order on a 3D transform section: Loc XYZ, Rot XYZ (roll/pitch/yaw), Scale XYZ.
    groups = {"location": (0, 3), "rotation": (3, 6), "scale": (6, 9)}

    frames: List[int] = []
    keyed = 0
    for key in keys:
        frame = int(round(float(key["time"]) * fps))
        frames.append(frame)
        interp = _interp_value(str(key.get("interp", "auto")))
        for prop, (lo, hi) in groups.items():
            vals = key.get(prop)
            if vals is None:
                continue
            vals = [float(v) for v in vals]
            for i, ch in enumerate(channels[lo:hi]):
                kwargs: Dict[str, Any] = {"time": unreal.FrameNumber(frame), "new_value": vals[i]}
                if interp is not None:
                    kwargs["interpolation"] = interp
                try:
                    ch.add_key(**kwargs)
                except TypeError:
                    ch.add_key(time=unreal.FrameNumber(frame), new_value=vals[i])
                keyed += 1

    # The silent-failure trap: keys outside the section range don't show up.
    lo_frame, hi_frame = min(0, min(frames)), max(frames) + 1
    try:
        section.set_range(lo_frame, hi_frame)
    except Exception as e:
        raise ValueError(f"section.set_range failed: {e}. Section members: {_members(section, contains='range')}")
    _save(sequence)
    return {
        "sequence_path": sequence_path,
        "binding_name": binding_name,
        "keys_added": keyed,
        "key_frames": sorted(set(frames)),
        "section_range": [lo_frame, hi_frame],
        "fps": fps,
    }


def get_sequence_info(sequence_path: str) -> dict:
    """Read a LevelSequence's playback range, bindings, tracks, and key counts."""
    sequence = _load_asset(sequence_path)
    info: dict = {"sequence_path": sequence_path, "fps": _sequence_fps(sequence)}
    try:
        info["playback_range"] = [int(sequence.get_playback_start()), int(sequence.get_playback_end())]
    except Exception as e:
        info["playback_range_error"] = str(e)
    bindings = []
    for b in sequence.get_bindings():
        entry: dict = {}
        try:
            entry["name"] = str(b.get_name())
            entry["display_name"] = str(b.get_display_name())
        except Exception as e:
            entry["error"] = str(e)
            bindings.append(entry)
            continue
        tracks = []
        for t in b.get_tracks():
            trk: dict = {"class": t.get_class().get_name()}
            secs = []
            for s in t.get_sections():
                sec: dict = {}
                try:
                    sec["range"] = [int(s.get_start_frame()), int(s.get_end_frame())]
                except Exception:
                    pass
                try:
                    sec["keys"] = sum(len(ch.get_keys()) for ch in s.get_all_channels())
                except Exception:
                    pass
                secs.append(sec)
            trk["sections"] = secs
            tracks.append(trk)
        entry["tracks"] = tracks
        bindings.append(entry)
    info["bindings"] = bindings
    return info


# ---------------------------------------------------------------------------
# AnimSequence route (skeletal keyframes)
# ---------------------------------------------------------------------------


def _anim_controller(seq: Any):
    try:
        controller = seq.get_editor_property("controller")
    except Exception:
        controller = getattr(seq, "controller", None)
    if controller is None:
        raise ValueError(
            f"AnimDataController not reachable on this AnimSequence. Members: {_members(seq, contains='controller')}"
        )
    return controller


def create_anim_sequence(
    skeletal_mesh_path: str, dest_folder: str, name: str, length_seconds: float = 1.0, fps: int = 30
) -> dict:
    """Create an empty AnimSequence bound to a skeletal mesh's skeleton."""
    _require("AnimSequence")
    factory_cls = _require("AnimSequenceFactory")
    mesh = _load_asset(skeletal_mesh_path)
    skeleton = mesh.get_editor_property("skeleton")
    if skeleton is None:
        raise ValueError(f"No skeleton on mesh: {skeletal_mesh_path}")
    unreal.EditorAssetLibrary.make_directory(dest_folder)
    full = f"{dest_folder}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        raise ValueError(f"Asset already exists: {full} (delete_asset first to replace)")
    factory = factory_cls()
    factory.set_editor_property("target_skeleton", skeleton)
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    seq = asset_tools.create_asset(name, dest_folder, _require("AnimSequence"), factory)
    if seq is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    frames = max(1, int(round(float(length_seconds) * int(fps))))
    controller = _anim_controller(seq)
    try:
        controller.open_bracket("Initialize anim sequence")
        controller.set_frame_rate(unreal.FrameRate(int(fps), 1))
        controller.set_number_of_frames(frames)
        controller.notify_populated()
    finally:
        try:
            controller.close_bracket()
        except Exception:
            pass
    _save(seq)
    return {
        "anim_path": str(seq.get_path_name()),
        "skeleton": str(skeleton.get_path_name()),
        "fps": int(fps),
        "frames": frames,
        "length_seconds": float(length_seconds),
    }


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _resample_keys(keys: List[dict], num_samples: int, fps: float):
    """Resample sparse {time, location?, rotation?, scale?} keys to per-frame arrays.

    set_bone_track_keys demands equal-length arrays matching the frame count —
    anything else is a reported engine crash, so we always densify.
    Rotation input is [roll, pitch, yaw] degrees. Transforms are bone-local.
    """
    resolved = []
    loc, rot, scale = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]
    for key in sorted(keys, key=lambda k: float(k["time"])):
        loc = [float(v) for v in key.get("location", loc)]
        rot = [float(v) for v in key.get("rotation", rot)]
        scale = [float(v) for v in key.get("scale", scale)]
        resolved.append({"frame": float(key["time"]) * fps, "loc": loc, "rot": rot, "scale": scale})

    positions, rotations, scales = [], [], []
    for f in range(num_samples):
        prev = resolved[0]
        nxt = resolved[-1]
        for k in resolved:
            if k["frame"] <= f:
                prev = k
            if k["frame"] >= f:
                nxt = k
                break
        span = nxt["frame"] - prev["frame"]
        t = 0.0 if span <= 0 else max(0.0, min(1.0, (f - prev["frame"]) / span))
        loc = [_lerp(prev["loc"][i], nxt["loc"][i], t) for i in range(3)]
        r = [_lerp(prev["rot"][i], nxt["rot"][i], t) for i in range(3)]
        s = [_lerp(prev["scale"][i], nxt["scale"][i], t) for i in range(3)]
        positions.append(unreal.Vector(loc[0], loc[1], loc[2]))
        rotations.append(_rotator(r[0], r[1], r[2]).quaternion())
        scales.append(unreal.Vector(s[0], s[1], s[2]))
    return positions, rotations, scales


def set_anim_bone_keys(anim_path: str, bone: str, keys: List[dict]) -> dict:
    """Key one bone's local transform over the sequence.

    keys=[{time, location?, rotation?, scale?}] — time in seconds, rotation
    [roll,pitch,yaw] degrees, transforms in BONE-LOCAL space (relative to the
    parent bone). Sparse keys are linearly resampled to every frame.
    """
    if not keys:
        raise ValueError("keys is empty")
    seq = _load_asset(anim_path)
    controller = _anim_controller(seq)
    model = None
    try:
        model = seq.get_editor_property("data_model")
    except Exception:
        model = getattr(seq, "data_model", None)
    frames = None
    fps = 30.0
    if model is not None:
        try:
            frames = int(model.get_number_of_frames())
            rate = model.get_frame_rate()
            fps = float(rate.numerator) / float(max(1, rate.denominator))
        except Exception:
            pass
    if frames is None:
        raise ValueError(
            f"Cannot read frame count from the data model. Model members: {_members(model)[:40] if model else 'no model'}"
        )
    num_samples = frames + 1  # keys array length is frame count + 1 (inclusive end)
    positions, rotations, scales = _resample_keys(keys, num_samples, fps)

    set_keys = getattr(controller, "set_bone_track_keys", None)
    if not callable(set_keys):
        raise ValueError(
            f"set_bone_track_keys not available. Controller members: {_members(controller, contains='bone')}"
        )
    try:
        controller.open_bracket("Set bone keys")
        add_track = getattr(controller, "add_bone_curve", None) or getattr(controller, "add_bone_track", None)
        if callable(add_track):
            try:
                add_track(bone)
            except Exception:
                pass  # track may already exist
        ok = set_keys(bone, positions, rotations, scales)
    finally:
        try:
            controller.close_bracket()
        except Exception:
            pass
    if ok is False:
        raise ValueError(
            f"set_bone_track_keys returned False for bone {bone!r} — check the bone exists "
            f"(list_skeleton_bones) and the sequence is initialized (get_anim_sequence_info)."
        )
    _save(seq)
    return {"anim_path": anim_path, "bone": bone, "keys_in": len(keys), "frames_written": num_samples, "fps": fps}


def get_anim_sequence_info(anim_path: str) -> dict:
    """Read an AnimSequence's length, fps, and tracked bones."""
    seq = _load_asset(anim_path)
    info: dict = {"anim_path": anim_path, "class": seq.get_class().get_name()}
    model = None
    try:
        model = seq.get_editor_property("data_model")
    except Exception:
        model = getattr(seq, "data_model", None)
    if model is None:
        info["error"] = f"No data model. Members: {_members(seq)[:40]}"
        return info
    for label, getter in (
        ("frames", "get_number_of_frames"),
        ("keys", "get_number_of_keys"),
        ("length_seconds", "get_play_length"),
    ):
        fn = getattr(model, getter, None)
        if callable(fn):
            try:
                info[label] = float(fn()) if label == "length_seconds" else int(fn())
            except Exception:
                pass
    try:
        rate = model.get_frame_rate()
        info["fps"] = float(rate.numerator) / float(max(1, rate.denominator))
    except Exception:
        pass
    fn = getattr(model, "get_bone_track_names", None)
    if callable(fn):
        try:
            info["bone_tracks"] = [str(n) for n in list(fn())][:200]
        except Exception as e:
            info["bone_tracks_error"] = str(e)
    return info


register("anim_author_capabilities")(anim_author_capabilities)
register("create_level_sequence")(create_level_sequence)
register("add_sequence_binding")(add_sequence_binding)
register("add_transform_keys")(add_transform_keys)
register("get_sequence_info")(get_sequence_info)
register("create_anim_sequence")(create_anim_sequence)
register("set_anim_bone_keys")(set_anim_bone_keys)
register("get_anim_sequence_info")(get_anim_sequence_info)
