"""Record what each editor command changed, and how to undo it.

``tick.dispatch`` is the one place every editor command passes through — core
handlers, Store-plugin handlers, and the ``listener_command`` passthrough alike —
so capture hooks there rather than in ~120 individual handlers.

For a registered command, :func:`before` reads the state the command is about to
overwrite (one or two property reads, never a level scan) and :func:`after` turns
that plus the result into a **sidecar**: what was touched, what it looked like
before, and the commands that would put it back. The host attaches the sidecar to
its change journal, so the panel can show "Hacker moved VerifyCube +250 on Z" and
offer a Revert.

Three rules this module never breaks:

1. **It cannot fail a command.** Both entry points swallow every exception. A
   capture bug must never turn a working edit into an error, and it must never
   raise inside the editor tick.
2. **It cannot be slow.** An unregistered command costs one dict lookup. A
   registered one costs the reads listed in its spec. Level-wide snapshots are
   reserved for opaque commands and only in ``full`` mode.
3. **It never claims more than it knows.** ``revertable`` is ``auto`` only when
   the recorded state can actually be posted back. Anything else is ``manual``
   with a reason a human can act on, and a lossy read is labelled as such.

``unreal`` and ``listener.*`` are imported inside functions so the host test
suite can import this module and check it against the host-side classifier.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

#: Sidecar shape version. Bump when a consumer would misread an older payload.
CAPTURE_VERSION = 1

REVERT_AUTO = "auto"
REVERT_MANUAL = "manual"
REVERT_NONE = "none"

_enabled = True
_mode = "full"  # "basic" (no opaque snapshots) | "full"


def configure(enabled: Optional[bool] = None, mode: Optional[str] = None) -> dict:
    """Host-controlled kill switch and capture depth."""
    global _enabled, _mode
    if enabled is not None:
        _enabled = bool(enabled)
    if mode in ("basic", "full"):
        _mode = mode
    return {"enabled": _enabled, "mode": _mode}


def enabled() -> bool:
    return _enabled


# --- small helpers ---------------------------------------------------------------


def _actor_guid(actor) -> str:
    """A stable id that survives relabelling, or "" when unavailable."""
    from listener.serialize import actor_guid

    return actor_guid(actor)


def _actor_target(actor) -> dict:
    """Identity block for one actor: guid preferred, path and label for humans."""
    from listener.serialize import is_live

    if not is_live(actor):
        return {"kind": "actor", "id": "", "guid": "", "label": "", "path": "", "invalid": True}
    try:
        path = actor.get_path_name()
    except Exception:
        path = ""
    try:
        label = actor.get_actor_label()
    except Exception:
        label = ""
    guid = _actor_guid(actor)
    return {"kind": "actor", "id": guid or path, "guid": guid, "label": label, "path": path}


def _xyz(vec) -> List[float]:
    return [float(vec.x), float(vec.y), float(vec.z)]


def _transform_state(actor) -> dict:
    return {
        "location": _xyz(actor.get_actor_location()),
        "rotation": [
            float(actor.get_actor_rotation().pitch),
            float(actor.get_actor_rotation().yaw),
            float(actor.get_actor_rotation().roll),
        ],
        "scale": _xyz(actor.get_actor_scale3d()),
    }


def _find(params: dict, *keys: str):
    """Resolve the actor named by the first present key, or None."""
    from listener import lookup

    for key in keys:
        ident = params.get(key)
        if isinstance(ident, str) and ident.strip():
            return lookup.find_actor(ident.strip())
    return None


def _restore_transform(target: dict, state: dict) -> dict:
    from listener.device_scale import _is_identity_scale

    params = {
        "actor_path": target.get("path") or target.get("id"),
        "location": state["location"],
        "rotation": state["rotation"],
    }
    # Identity scale is noise from the snapshot. Sending it on a Creative
    # device used to refuse the whole move-revert.
    if not _is_identity_scale(state.get("scale")):
        params["scale"] = state["scale"]
    return {"command": "set_actor_transform", "params": params}


# --- before / after per command ---------------------------------------------------
# before(params) -> {"targets": [...], "state": {...}} | None
# inverse(params, cap, result) -> [ {command, params}, ... ] | None
# after_state(params, cap, result) -> {...} merged into the recorded before-state
#
# `cap` is this module's own dict for the duration of one dispatch. Only its
# "targets" and "state" keys reach the sidecar, so a bulky working value (a level
# snapshot) can live beside them without being written to the change journal.


#: Rows to keep from a bracketing snapshot. Above this the diff is not worth the tick.
_OPAQUE_SNAPSHOT_LIMIT = 6000
#: Changes listed in the sidecar. The count is always exact; the list is not.
_OPAQUE_DIFF_LIMIT = 200


def _level_snapshot() -> Optional[dict]:
    """Every actor in the level, keyed for comparison. None when it cannot be taken."""
    from listener.registry.device_graph import actor_state_snapshot

    snap = actor_state_snapshot(limit=_OPAQUE_SNAPSHOT_LIMIT, scope="all", fields=["guid"])
    if not isinstance(snap, dict) or snap.get("skipped"):
        return None
    return snap


def _before_opaque(params: dict) -> Optional[dict]:
    """Bracket the level so a script's spawns can be identified afterwards.

    Only in ``full`` mode: this is the one capture that walks the whole level, and
    it runs twice per opaque command.
    """
    if _mode != "full":
        return None
    snap = _level_snapshot()
    if snap is None:
        return None
    # Under "snapshot", not "state": the raw rows must never reach the journal.
    return {"targets": [], "state": {}, "snapshot": snap}


def _after_opaque(params: dict, cap: Optional[dict], result: Any) -> Optional[dict]:
    """What the level looked like afterwards, as a diff rather than a second copy.

    Also stashes the added actors on ``cap`` so :func:`_created_opaque` can report
    them without taking a third snapshot.
    """
    from listener.registry.device_graph import actor_state_diff

    state: dict = {}
    code = params.get("code") or params.get("command") or params.get("script")
    if isinstance(code, str) and code.strip():
        # Recorded verbatim: an opaque change is only auditable if you can read it.
        state["code"] = code
    before_snap = (cap or {}).get("snapshot")
    if not isinstance(before_snap, dict):
        return state or None
    after_snap = _level_snapshot()
    if after_snap is None:
        return state or None
    diff = actor_state_diff(before_snap, after_snap)
    changes = list(diff.get("changes") or [])
    added = [c for c in changes if c.get("change") == "added"]
    if isinstance(cap, dict):
        cap["added"] = added
    state["diff"] = {
        "count": len(changes),
        "added": len(added),
        "removed": sum(1 for c in changes if c.get("change") == "removed"),
        "moved": sum(1 for c in changes if c.get("change") == "moved"),
        "changes": changes[:_OPAQUE_DIFF_LIMIT],
        "truncated": len(changes) > _OPAQUE_DIFF_LIMIT,
    }
    return state


def _created_opaque(params: dict, cap: Optional[dict], result: Any) -> Optional[list]:
    """The actors that appeared while the command ran — exactly what a revert removes."""
    added = (cap or {}).get("added")
    if not added:
        return None
    return [
        {
            "kind": "actor",
            "id": str(row.get("guid") or row.get("path") or ""),
            "guid": str(row.get("guid") or ""),
            "label": str(row.get("label") or ""),
            "path": str(row.get("path") or ""),
        }
        for row in added
        if row.get("guid") or row.get("path")
    ]


def _before_actor_transform(params: dict) -> Optional[dict]:
    actor = _find(params, "actor_path")
    if actor is None:
        return None
    return {"targets": [_actor_target(actor)], "state": _transform_state(actor)}


def _inverse_transform(params: dict, cap: dict, result: Any) -> Optional[list]:
    targets = cap.get("targets") or []
    state = cap.get("state") or {}
    if not targets or "location" not in state:
        return None
    return [_restore_transform(targets[0], state)]


def _before_actor_label(params: dict) -> Optional[dict]:
    actor = _find(params, "actor_path")
    if actor is None:
        return None
    return {"targets": [_actor_target(actor)], "state": {"label": actor.get_actor_label()}}


def _inverse_label(params: dict, cap: dict, result: Any) -> Optional[list]:
    targets = cap.get("targets") or []
    label = (cap.get("state") or {}).get("label")
    if not targets or label is None:
        return None
    return [{"command": "set_actor_label",
             "params": {"actor_path": targets[0].get("path") or targets[0].get("id"), "label": label}}]


def _before_actor_folder(params: dict) -> Optional[dict]:
    actor = _find(params, "actor_path")
    if actor is None:
        return None
    try:
        folder = str(actor.get_folder_path())
    except Exception:
        folder = ""
    return {"targets": [_actor_target(actor)], "state": {"folder": folder}}


def _inverse_folder(params: dict, cap: dict, result: Any) -> Optional[list]:
    targets = cap.get("targets") or []
    folder = (cap.get("state") or {}).get("folder")
    if not targets or folder is None:
        return None
    return [{"command": "set_actor_folder",
             "params": {"actor_path": targets[0].get("path") or targets[0].get("id"), "folder": folder}}]


def _before_actor_tags(params: dict) -> Optional[dict]:
    actor = _find(params, "actor_path")
    if actor is None:
        return None
    try:
        tags = [str(t) for t in (actor.get_editor_property("tags") or [])]
    except Exception:
        return None
    return {"targets": [_actor_target(actor)], "state": {"tags": tags}}


def _inverse_tags(params: dict, cap: dict, result: Any) -> Optional[list]:
    targets = cap.get("targets") or []
    tags = (cap.get("state") or {}).get("tags")
    if not targets or tags is None:
        return None
    return [{"command": "set_actor_tags",
             "params": {"actor_path": targets[0].get("path") or targets[0].get("id"), "tags": tags}}]


def _before_actor_properties(params: dict) -> Optional[dict]:
    """Read exactly the properties about to be written — not the whole actor."""
    from listener.serialize import serialize

    actor = _find(params, "actor_path")
    props = params.get("properties")
    if actor is None or not isinstance(props, dict):
        return None
    before: Dict[str, Any] = {}
    unreadable: List[str] = []
    for name in props:
        try:
            before[str(name)] = serialize(actor.get_editor_property(str(name)))
        except Exception:
            unreadable.append(str(name))
    state: Dict[str, Any] = {"properties": before}
    if unreadable:
        state["unreadable"] = unreadable
    return {"targets": [_actor_target(actor)], "state": state}


def _inverse_properties(params: dict, cap: dict, result: Any) -> Optional[list]:
    targets = cap.get("targets") or []
    state = cap.get("state") or {}
    before = state.get("properties") or {}
    if not targets or not before or state.get("unreadable"):
        # A partial restore would silently leave some properties changed.
        return None
    return [{"command": "set_actor_properties",
             "params": {"actor_path": targets[0].get("path") or targets[0].get("id"), "properties": before}}]


def _before_group_locations(params: dict) -> Optional[dict]:
    """Locations of every actor in a multi-actor move, keyed by path (labels are not unique)."""
    from listener import lookup

    paths = params.get("actor_paths")
    if not isinstance(paths, list) or not paths:
        return None
    targets, locations = [], []
    for ident in paths:
        actor = lookup.find_actor(str(ident))
        if actor is None:
            return None
        targets.append(_actor_target(actor))
        locations.append(_xyz(actor.get_actor_location()))
    return {"targets": targets, "state": {"locations": locations}}


def _inverse_group_locations(params: dict, cap: dict, result: Any) -> Optional[list]:
    targets = cap.get("targets") or []
    locations = (cap.get("state") or {}).get("locations") or []
    if not targets or len(targets) != len(locations):
        return None
    return [
        {"command": "set_actor_transform",
         "params": {"actor_path": t.get("path") or t.get("id"), "location": loc}}
        for t, loc in zip(targets, locations)
    ]


def _before_single_location(params: dict) -> Optional[dict]:
    actor = _find(params, "actor_path")
    if actor is None:
        return None
    return {"targets": [_actor_target(actor)], "state": {"location": _xyz(actor.get_actor_location())}}


def _inverse_single_location(params: dict, cap: dict, result: Any) -> Optional[list]:
    targets = cap.get("targets") or []
    loc = (cap.get("state") or {}).get("location")
    if not targets or loc is None:
        return None
    return [{"command": "set_actor_transform",
             "params": {"actor_path": targets[0].get("path") or targets[0].get("id"), "location": loc}}]


def _before_attach(params: dict) -> Optional[dict]:
    child = _find(params, "child_path")
    if child is None:
        return None
    parent = None
    try:
        parent = child.get_attach_parent_actor()
    except Exception:
        pass
    state = {"parent": _actor_target(parent)["path"] if parent is not None else ""}
    return {"targets": [_actor_target(child)], "state": state}


def _inverse_attach(params: dict, cap: dict, result: Any) -> Optional[list]:
    prior = (cap.get("state") or {}).get("parent")
    targets = cap.get("targets") or []
    if not targets or not prior:
        # Detaching is not expressible: attach_actor has no "no parent" form.
        return None
    return [{"command": "attach_actor",
             "params": {"child_path": targets[0].get("path") or targets[0].get("id"),
                        "parent_path": prior}}]


def _before_verse_editable(params: dict) -> Optional[dict]:
    """Current SavedActor / AssetForEditor / scalar on the field about to be written."""
    actor = _find(params, "actor_path")
    field = str(params.get("field") or params.get("array_field") or "")
    if actor is None or not field:
        return None
    from listener.verse_editable_editor import peek_verse_field_links

    try:
        path = actor.get_path_name()
    except Exception:
        path = str(params.get("actor_path") or "")
    links = peek_verse_field_links(path, field)
    return {"targets": [_actor_target(actor)], "state": links}


def _verse_actor_path(cap: dict, params: dict) -> str:
    targets = cap.get("targets") or []
    if targets:
        return str(targets[0].get("path") or targets[0].get("id") or "")
    return str(params.get("actor_path") or "")


def _is_placed_actor_path(path: str) -> bool:
    """A wire target must be a placed actor, not a Verse property object.

    peek sometimes returns ``Actor.Verse-….__verse_0xHASH_Field`` when SavedActor
    is empty. Wiring that back fails with Actor not found.
    """
    text = (path or "").strip()
    return bool(text) and ".__verse_" not in text


def _placed_target_paths(cap: dict) -> list[str]:
    return [p for p in (cap.get("state") or {}).get("target_paths") or [] if _is_placed_actor_path(str(p))]


def _inverse_wire_ref(params: dict, cap: dict, result: Any) -> Optional[list]:
    path = _verse_actor_path(cap, params)
    field = str(params.get("field") or "")
    if not path or not field:
        return None
    prev = _placed_target_paths(cap)
    if prev:
        return [{"command": "wire_verse_device_ref",
                 "params": {"actor_path": path, "field": field, "target_path": prev[0]}}]
    return [{"command": "set_verse_editable",
             "params": {"actor_path": path, "field": field, "value": None}}]


def _inverse_wire_array(params: dict, cap: dict, result: Any) -> Optional[list]:
    path = _verse_actor_path(cap, params)
    field = str(params.get("field") or "")
    if not path or not field:
        return None
    prev = _placed_target_paths(cap)
    return [{"command": "wire_verse_device_array",
             "params": {"actor_path": path, "field": field, "target_paths": prev, "replace": True}}]


def _inverse_wire_props(params: dict, cap: dict, result: Any) -> Optional[list]:
    path = _verse_actor_path(cap, params)
    field = str(params.get("field") or "")
    if not path or not field:
        return None
    prev = list((cap.get("state") or {}).get("asset_paths") or [])
    return [{"command": "wire_verse_prop_assets",
             "params": {"actor_path": path, "field": field, "asset_paths": prev}}]


def _inverse_set_editable(params: dict, cap: dict, result: Any) -> Optional[list]:
    path = _verse_actor_path(cap, params)
    field = str(params.get("field") or "")
    if not path or not field:
        return None
    state = cap.get("state") or {}
    prev = _placed_target_paths(cap)
    if prev:
        return [{"command": "set_verse_editable",
                 "params": {"actor_path": path, "field": field, "target_path": prev[0]}}]
    return [{"command": "set_verse_editable",
             "params": {"actor_path": path, "field": field, "value": state.get("value")}}]


def _created_actor(params: dict, cap: Optional[dict], result: Any) -> Optional[dict]:
    """Identity of the actor a spawn/duplicate produced, read back from the result."""
    if not isinstance(result, dict):
        return None
    actor = result.get("actor")
    if not isinstance(actor, dict) or actor.get("invalid"):
        return None
    path = str(actor.get("path") or "")
    guid = str(actor.get("guid") or "")
    if not (path or guid):
        return None
    return {"kind": "actor", "id": guid or path, "guid": guid,
            "label": str(actor.get("label") or ""), "path": path}


#: Where each asset-creating handler reports the new asset's path.
_ASSET_PATH_KEYS = ("dest", "material_path", "material_instance_path", "system_path",
                    "data_table_path", "widget_path", "asset_path")


def _created_blockout(params: dict, cap: Optional[dict], result: Any) -> Optional[list]:
    """Actors a blockout / area_create placed, read back from the result."""
    if not isinstance(result, dict):
        return None
    created: List[dict] = []
    seen: set[str] = set()

    def _add(ident: str, path: str = "") -> None:
        ident = (ident or path or "").strip()
        if not ident or ident in seen:
            return
        seen.add(ident)
        created.append({"kind": "actor", "id": ident, "guid": "", "label": ident, "path": path or ident})

    for label in result.get("labels") or []:
        if isinstance(label, str):
            _add(label)
    terrain = result.get("terrain") if isinstance(result.get("terrain"), dict) else {}
    _add(str(terrain.get("actor_path") or ""), str(terrain.get("actor_path") or ""))
    blockout = result.get("blockout") if isinstance(result.get("blockout"), dict) else {}
    for label in blockout.get("labels") or []:
        if isinstance(label, str):
            _add(label)
    folder = str(result.get("folder") or blockout.get("folder") or "")
    if folder and not created:
        _add(folder, folder)
    return created or None


def _created_asset(params: dict, cap: Optional[dict], result: Any) -> Optional[list]:
    """Identity of the asset(s) a create/duplicate/import produced, from its result."""
    if not isinstance(result, dict) or result.get("success") is False:
        return None
    paths: List[str] = []
    for key in _ASSET_PATH_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value.startswith("/"):
            paths.append(value)
            break
    imported = result.get("imported")
    if isinstance(imported, list):
        paths.extend(p for p in imported if isinstance(p, str) and p.startswith("/"))
    return [
        {"kind": "asset", "id": p, "guid": "", "label": p.rsplit("/", 1)[-1].split(".")[0], "path": p}
        for p in paths
    ] or None


# --- registry ---------------------------------------------------------------------


class _Spec:
    __slots__ = ("kind", "facet", "before", "inverse", "created", "after_state", "note")

    def __init__(self, kind, facet, before=None, inverse=None, created=None,
                 after_state=None, note=""):
        self.kind = kind
        self.facet = facet
        self.before: Optional[Callable[[dict], Optional[dict]]] = before
        self.inverse: Optional[Callable[[dict, dict, Any], Optional[list]]] = inverse
        #: May return one target or a list of them — an opaque script creates many.
        self.created: Optional[Callable[[dict, Optional[dict], Any], Any]] = created
        #: Extra recorded state only knowable once the command has run.
        self.after_state: Optional[Callable[[dict, Optional[dict], Any], Optional[dict]]] = after_state
        self.note = note


CAPTURE: Dict[str, _Spec] = {
    # actors — transform and organisation
    "set_actor_transform": _Spec("actor", "transform", _before_actor_transform, _inverse_transform),
    "set_actor_label": _Spec("actor", "label", _before_actor_label, _inverse_label),
    "set_actor_folder": _Spec("actor", "folder", _before_actor_folder, _inverse_folder),
    "set_actor_tags": _Spec("actor", "tags", _before_actor_tags, _inverse_tags),
    "set_actor_properties": _Spec("actor", "props", _before_actor_properties, _inverse_properties),
    "snap_actor_to_ground": _Spec("actor", "transform", _before_single_location, _inverse_single_location),
    "snap_actor_to_grid": _Spec("actor", "transform", _before_single_location, _inverse_single_location),
    "align_actors": _Spec("actor", "transform", _before_group_locations, _inverse_group_locations),
    "distribute_actors": _Spec("actor", "transform", _before_group_locations, _inverse_group_locations),
    "attach_actor": _Spec(
        "actor", "attach", _before_attach, _inverse_attach,
        note="the actor had no parent before, and attach_actor cannot detach",
    ),
    # Verse @editable wiring — inverse lives in the same ScopedEditorTransaction
    "wire_verse_device_ref": _Spec("verse", "editable", _before_verse_editable, _inverse_wire_ref),
    "set_verse_editable": _Spec("verse", "editable", _before_verse_editable, _inverse_set_editable),
    "wire_verse_device_array": _Spec("verse", "editable", _before_verse_editable, _inverse_wire_array),
    "wire_verse_prop_assets": _Spec("verse", "editable", _before_verse_editable, _inverse_wire_props),
    # actors — creation (the inverse is a delete, handled by the host's carve-out)
    "spawn_actor": _Spec("actor", "exists", None, None, _created_actor),
    "duplicate_actor": _Spec("actor", "exists", None, None, _created_actor),
    # assets — creation (the inverse is ducky_revert_creation(kind="asset"))
    "duplicate_asset": _Spec("asset", "exists", None, None, _created_asset),
    "import_asset": _Spec("asset", "exists", None, None, _created_asset),
    "create_material": _Spec("material", "exists", None, None, _created_asset),
    "create_material_instance": _Spec("material", "exists", None, None, _created_asset),
    "duplicate_material": _Spec("material", "exists", None, None, _created_asset),
    "create_niagara_system": _Spec("niagara", "exists", None, None, _created_asset),
    "create_niagara_mesh": _Spec("niagara", "exists", None, None, _created_asset),
    "create_widget_blueprint": _Spec("umg", "exists", None, None, _created_asset),
    "create_data_table": _Spec("datatable", "exists", None, None, _created_asset),
    # opaque — arbitrary code, bracketed by a level snapshot
    "execute_python": _Spec(
        "world", "opaque", _before_opaque, None, _created_opaque, _after_opaque,
        note="arbitrary Python: only what the level snapshot noticed is known",
    ),
    "exec_console_command": _Spec(
        "world", "opaque", _before_opaque, None, _created_opaque, _after_opaque,
        note="a console command's effects are not modelled; only spawns are noticed",
    ),
    # worldgen / blockout — created from the result, or a level snapshot for the rest
    "area_create": _Spec("world", "exists", None, None, _created_blockout),
    "blockout_layout": _Spec("world", "exists", None, None, _created_blockout),
    "landscape_create": _Spec(
        "world", "exists", _before_opaque, None, _created_opaque, _after_opaque,
    ),
    "foliage_scatter": _Spec(
        "world", "", _before_opaque, None, _created_opaque, _after_opaque,
    ),
    "terrain_generate": _Spec(
        "world", "", _before_opaque, None, _created_opaque, _after_opaque,
    ),
    "pcg_generate": _Spec(
        "world", "", _before_opaque, None, _created_opaque, _after_opaque,
    ),
}


def _summary(
    command: str,
    cap: Optional[dict],
    result: Any,
    created: Optional[list] = None,
    state: Optional[dict] = None,
) -> str:
    """One short line for the change row. Never raises."""
    try:
        state = dict(state if state is not None else ((cap or {}).get("state") or {}))
        diff = state.get("diff")
        if isinstance(diff, dict):
            parts = [
                f"+{diff['added']} actors" if diff.get("added") else "",
                f"-{diff['removed']} actors" if diff.get("removed") else "",
                f"{diff['moved']} moved" if diff.get("moved") else "",
            ]
            shown = ", ".join(p for p in parts if p)
            return shown or "changed nothing the level snapshot could see"
        if created:
            first = created[0]
            name = first.get("label") or first.get("path") or "actor"
            if len(created) > 1:
                return f"created {name} and {len(created) - 1} more"
            return f"created {name}"
        if command == "set_actor_transform" and isinstance(result, dict):
            after = (result.get("actor") or {}).get("location")
            before = state.get("location")
            if isinstance(after, dict) and isinstance(before, list):
                deltas = [
                    (axis, round(float(after.get(axis, 0.0)) - before[i], 1))
                    for i, axis in enumerate("xyz")
                ]
                moved = [f"{d:+g} on {axis.upper()}" for axis, d in deltas if abs(d) >= 0.05]
                if moved:
                    return "moved " + ", ".join(moved)
                return "transform set"
        if command == "set_actor_label":
            return f"renamed from {state.get('label', '')!r}"
        if command == "set_actor_folder":
            return f"moved out of {state.get('folder') or '(no folder)'}"
        if command == "set_actor_tags":
            return f"tags were {state.get('tags') or []}"
        if command == "set_actor_properties":
            names = sorted((state.get("properties") or {}).keys())
            return "set " + ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
        if command in ("align_actors", "distribute_actors"):
            return f"moved {len(state.get('locations') or [])} actors"
        if command in ("snap_actor_to_ground", "snap_actor_to_grid"):
            return "snapped"
        if command in ("wire_verse_device_ref", "wire_verse_device_array", "wire_verse_prop_assets",
                       "set_verse_editable"):
            field = state.get("field") or ""
            prev = state.get("target_paths") or state.get("asset_paths") or []
            if prev:
                return f"rewired {field} (was {len(prev)})" if field else "rewired"
            if state.get("value") is not None:
                return f"set {field}" if field else "set editable"
            return f"wired {field}" if field else "wired"
    except Exception:
        pass
    return command.replace("_", " ")


def before(command: str, params: Optional[dict]) -> Optional[dict]:
    """State the command is about to overwrite, or None. Never raises."""
    if not _enabled:
        return None
    try:
        spec = CAPTURE.get(command)
        if spec is None or spec.before is None:
            return None
        return spec.before(dict(params or {}))
    except Exception:
        return None


def after(
    command: str,
    params: Optional[dict],
    result: Any,
    cap: Optional[dict],
    *,
    ok: bool = True,
) -> Optional[dict]:
    """Sidecar for one dispatched command, or None when there is nothing to say.

    Never raises: a capture failure must not turn a successful edit into an error.
    """
    if not _enabled:
        return None
    try:
        spec = CAPTURE.get(command)
        if spec is None:
            return None
        params = dict(params or {})
        # Each of these is guarded on its own: a bug building the inverse must
        # cost the inverse, not the record of what changed.
        state = dict((cap or {}).get("state") or {})
        if ok and spec.after_state is not None:
            try:
                extra = spec.after_state(params, cap, result)
                if isinstance(extra, dict):
                    state.update(extra)
            except Exception:
                pass

        created: List[dict] = []
        if ok and spec.created is not None:
            try:
                made = spec.created(params, cap, result)
                if isinstance(made, dict):
                    created = [made]
                elif isinstance(made, list):
                    created = [t for t in made if isinstance(t, dict)]
            except Exception:
                created = []

        targets = list((cap or {}).get("targets") or [])
        if not targets and created:
            targets = list(created)

        inverse = None
        if ok and spec.inverse is not None and cap:
            try:
                inverse = spec.inverse(params, cap, result)
            except Exception:
                inverse = None

        diff = state.get("diff")
        if not ok:
            revertable, reason = REVERT_NONE, "the command failed, so nothing changed"
        elif inverse:
            revertable, reason = REVERT_AUTO, ""
        elif created:
            # The host decides: it knows whether this run created the thing.
            revertable, reason = REVERT_AUTO, ""
        elif isinstance(diff, dict) and not diff.get("count"):
            # Bracketing snapshots agree: nothing to undo, so nothing to hand a human.
            revertable, reason = REVERT_NONE, "changed nothing the level snapshot could see"
        else:
            revertable = REVERT_MANUAL
            reason = spec.note or "no inverse could be built for this command"

        return {
            "v": CAPTURE_VERSION,
            "command": command,
            "kind": spec.kind,
            "facet": spec.facet,
            "targets": targets,
            "before": state or None,
            "inverse": inverse,
            "created": created,
            "revertable": revertable,
            "reason": reason,
            "summary": _summary(command, cap, result, created, state),
            "program": "uefn",
            "outcome": "ok" if ok else "error",
        }
    except Exception:
        return None
