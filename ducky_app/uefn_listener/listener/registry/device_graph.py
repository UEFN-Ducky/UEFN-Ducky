"""Device graph snapshot for offline wiring simulation / Tester panel.

Walks placed Creative + Verse devices and returns nodes + edges for the host-side
simulator. Edges come from Verse ``@editable`` device refs and Creative direct-event
bindings. Capability-guarded and bounded so a busy island does not freeze the editor.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import unreal

from listener import lookup
from listener.device_editor import (
    get_device_settings,
    is_creative_device,
    list_creative_devices,
)
from listener.dispatch import register
from listener.serialize import serialize
from listener.verse_editable_editor import get_verse_editables

_ACTOR_PATH_RE = re.compile(r"^/[\w\.\-/]+$")
_MAX_DEVICES = 150


def _location_of(actor: Any) -> Optional[dict]:
    try:
        loc = actor.get_actor_location()
        return serialize(loc)
    except Exception:
        return None


def _value_targets(raw: Any) -> List[str]:
    """Extract actor paths / labels from an editable or option value."""
    out: List[str] = []
    if raw is None:
        return out
    if isinstance(raw, list):
        for item in raw:
            out.extend(_value_targets(item))
        return out
    if isinstance(raw, dict):
        for key in ("path", "actor_path", "value", "SavedActor", "label"):
            if key in raw:
                out.extend(_value_targets(raw[key]))
        return out
    text = str(raw).strip()
    if not text or text in ("None", "null", "False", "True", "0"):
        return out
    # Skip pure scalars
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return out
    if _ACTOR_PATH_RE.match(text) or text.startswith("/Game/") or text.startswith("/Script/"):
        out.append(text)
        return out
    # Soft label match — only when it looks like a device label (has letters)
    if re.search(r"[A-Za-z]", text) and len(text) < 120 and "\n" not in text:
        # Drop enum-like ALL_CAPS tokens
        if not (text.isupper() and "_" in text):
            out.append(text)
    return out


def _edges_from_verse_editables(node_id: str, editables: dict) -> List[dict]:
    edges: List[dict] = []
    for field, entry in (editables or {}).items():
        if not isinstance(entry, dict):
            continue
        wiring = entry.get("wiring") or {}
        verse_type = str(wiring.get("verse_type") or "")
        tool = str(wiring.get("tool") or "")
        is_ref = (
            tool in ("wire_verse_device_ref", "wire_verse_device_array", "set_verse_editable")
            or verse_type.endswith("_device")
            or verse_type in ("creative_prop",)
        )
        if not is_ref and verse_type in ("int", "float", "logic", "string", ""):
            continue
        targets = _value_targets(entry.get("value"))
        if not targets:
            # Still record empty ref slots so audit can flag unwired fields
            edges.append(
                {
                    "from": node_id,
                    "to": None,
                    "field": field,
                    "kind": "verse_editable",
                    "verse_type": verse_type or None,
                    "wired": False,
                }
            )
            continue
        for target in targets:
            edges.append(
                {
                    "from": node_id,
                    "to": target,
                    "field": field,
                    "kind": "verse_editable",
                    "verse_type": verse_type or None,
                    "wired": True,
                }
            )
    return edges


def _edges_from_creative_settings(node_id: str, settings: dict) -> List[dict]:
    edges: List[dict] = []
    for key, entry in (settings or {}).items():
        key_l = key.lower()
        looks_event = any(
            tok in key_l
            for tok in ("event", "trigger", "on ", "enable", "disable", "activate", "grant")
        )
        if not looks_event and not isinstance(entry, dict):
            continue
        value = entry.get("value") if isinstance(entry, dict) else entry
        targets = _value_targets(value)
        for target in targets:
            edges.append(
                {
                    "from": node_id,
                    "to": target,
                    "field": key,
                    "kind": "creative_binding",
                    "wired": True,
                }
            )
    return edges


def device_graph_snapshot(
    label_filter: str = "",
    class_filter: str = "",
    limit: int = 100,
    include_editables: bool = True,
    include_events: bool = True,
) -> dict:
    """Snapshot placed devices as nodes + wiring edges for offline simulation."""
    cap = max(1, min(int(limit or 100), _MAX_DEVICES))
    listed = list_creative_devices(
        class_filter=class_filter or "",
        label_filter=label_filter or "",
        limit=cap,
    )
    devices = listed.get("devices") or []
    nodes: List[dict] = []
    edges: List[dict] = []
    errors: List[dict] = []

    # Index path → label for edge resolution
    path_to_label: Dict[str, str] = {}
    for d in devices:
        path_to_label[str(d.get("path") or "")] = str(d.get("label") or "")

    # ponytail: full get_verse_editables walks hashes — cap deep inspect so a busy
    # island stays usable; remaining verse nodes still appear without edges.
    _MAX_DEEP_VERSE = 40
    _MAX_DEEP_CREATIVE = 60
    deep_verse = 0
    deep_creative = 0

    for d in devices:
        path = str(d.get("path") or "")
        label = str(d.get("label") or "")
        kind = str(d.get("kind") or "actor")
        cls = str(d.get("class") or "")
        node_id = path or label
        node: dict = {
            "id": node_id,
            "label": label,
            "class": cls,
            "kind": kind,
            "path": path,
            "location": None,
            "editables": {},
            "settings_summary": {},
        }
        try:
            actor = lookup.require_actor(path) if path else None
            if actor is not None:
                node["location"] = _location_of(actor)
        except Exception as exc:
            errors.append({"label": label, "stage": "locate", "error": str(exc)[:200]})

        if include_editables and kind == "verse_script" and path and deep_verse < _MAX_DEEP_VERSE:
            try:
                info = get_verse_editables(path, include_wiring_hints=True)
                node["editables"] = info.get("editables") or {}
                node["script_class"] = info.get("script_class")
                node["verse_source"] = info.get("verse_source")
                node["STOP"] = info.get("STOP")
                edges.extend(_edges_from_verse_editables(node_id, node["editables"]))
                deep_verse += 1
            except Exception as exc:
                errors.append({"label": label, "stage": "verse_editables", "error": str(exc)[:200]})

        if include_events and kind == "creative_device" and path and deep_creative < _MAX_DEEP_CREATIVE:
            try:
                info = get_device_settings(path, include_events=True)
                settings = info.get("settings") or {}
                # Keep a compact summary — full settings can be huge
                node["settings_summary"] = {
                    k: (v.get("value") if isinstance(v, dict) else v)
                    for k, v in list(settings.items())[:40]
                }
                edges.extend(_edges_from_creative_settings(node_id, settings))
                deep_creative += 1
            except Exception as exc:
                errors.append({"label": label, "stage": "creative_settings", "error": str(exc)[:200]})

        nodes.append(node)

    # Resolve edge.to labels when the target is a path we know
    for edge in edges:
        target = edge.get("to")
        if not target:
            continue
        if target in path_to_label:
            edge["to_label"] = path_to_label[target]
        else:
            # Match by label
            for n in nodes:
                if n["label"] == target or n["id"] == target:
                    edge["to_label"] = n["label"]
                    edge["to"] = n["id"]
                    break

    return {
        "nodes": nodes,
        "edges": edges,
        "count": len(nodes),
        "edge_count": len(edges),
        "errors": errors,
        "truncated": len(devices) >= cap,
    }


def session_status() -> dict:
    """Whether a play session / PIE is active, plus world grounding."""
    world = unreal.EditorLevelLibrary.get_editor_world()
    playing = False
    method = None
    try:
        # Prefer LevelEditorSubsystem play-state helpers when exposed
        sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        for name in ("is_in_play_session", "editor_is_playing", "is_playing"):
            fn = getattr(sub, name, None)
            if callable(fn):
                playing = bool(fn())
                method = name
                break
    except Exception:
        pass
    if not method:
        # Fallback: GameWorld vs EditorWorld
        try:
            gw = unreal.EditorLevelLibrary.get_game_world()
            playing = gw is not None and gw != world
            method = "get_game_world"
        except Exception:
            pass
    return {
        "playing": bool(playing),
        "method": method,
        "world_name": world.get_name() if world else None,
        "project_dir": str(unreal.Paths.project_dir()),
    }


def actor_state_snapshot(
    labels: Optional[List[str]] = None,
    label_filter: str = "",
    limit: int = 50,
) -> dict:
    """Capture transforms (+ class) for selected actors for before/after diffs."""
    wanted = {str(x).strip().lower() for x in (labels or []) if str(x).strip()}
    filt = (label_filter or "").strip().lower()
    actors = []
    for actor in lookup.actor_list():
        if not is_creative_device(actor):
            # Allow non-device actors when explicitly listed
            if not wanted:
                continue
        label = actor.get_actor_label()
        low = label.lower()
        if wanted and low not in wanted and actor.get_path_name().lower() not in wanted:
            continue
        if filt and filt not in low:
            continue
        try:
            loc = actor.get_actor_location()
            rot = actor.get_actor_rotation()
            scale = actor.get_actor_scale3d()
        except Exception:
            continue
        actors.append(
            {
                "label": label,
                "path": actor.get_path_name(),
                "class": actor.get_class().get_name(),
                "location": serialize(loc),
                "rotation": serialize(rot),
                "scale": serialize(scale),
            }
        )
        if len(actors) >= max(1, min(int(limit or 50), 200)):
            break
    return {"actors": actors, "count": len(actors)}


def actor_state_diff(before: dict, after: dict, epsilon: float = 1.0) -> dict:
    """Diff two actor_state_snapshot payloads. ``epsilon`` is uu for location."""
    before_map = {
        str(a.get("path") or a.get("label")): a for a in (before or {}).get("actors") or []
    }
    after_map = {
        str(a.get("path") or a.get("label")): a for a in (after or {}).get("actors") or []
    }
    changes = []
    for key, a in after_map.items():
        b = before_map.get(key)
        if not b:
            changes.append({"id": key, "label": a.get("label"), "change": "added"})
            continue
        delta = {}
        for axis in ("location", "rotation", "scale"):
            bv, av = b.get(axis) or {}, a.get(axis) or {}
            if not isinstance(bv, dict) or not isinstance(av, dict):
                continue
            d = {k: float(av.get(k, 0)) - float(bv.get(k, 0)) for k in av}
            if any(abs(v) > float(epsilon) for v in d.values()):
                delta[axis] = {"before": bv, "after": av, "delta": d}
        if delta:
            changes.append({"id": key, "label": a.get("label"), "change": "moved", **delta})
    for key, b in before_map.items():
        if key not in after_map:
            changes.append({"id": key, "label": b.get("label"), "change": "removed"})
    return {"changes": changes, "count": len(changes), "epsilon": float(epsilon)}


register("device_graph_snapshot")(device_graph_snapshot)
register("session_status")(session_status)
register("actor_state_snapshot")(actor_state_snapshot)
register("actor_state_diff")(actor_state_diff)
