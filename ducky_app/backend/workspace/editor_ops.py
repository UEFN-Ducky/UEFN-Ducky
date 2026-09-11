"""Which UEFN editor operations mutate the project, and how far they can be undone.

The host is the only gateway to the editor — every Ducky listener command goes
through ``backend.bridge.send_command`` and every Epic tool through
``backend.mcp_plugins.client_pool.call_tool`` — so this classification lives
host-side only. The listener owns the *other* half of the question (how to read
the before-state and build an inverse); see ``uefn_listener/listener/ducky_capture.py``.

Two explicit tables, because a classifier that guesses is a classifier that
silently stops recording:

``EDITOR_OPS``     every command that changes project state, with how revertable it is
``READ_COMMANDS``  every command that cannot change project state

Anything in neither is treated as :data:`MUT_OPAQUE` — recorded, never trusted to
have an inverse. ``test_editor_ops.py`` parses the listener tree and fails if a
registered command is missing from both tables, so a new command cannot be added
without a deliberate decision about it.

Note on ``_HEAVY_COMMANDS`` (``uefn_listener/listener/tick.py``): that set is a
main-thread **throttle** list, not a mutation list. It contains heavy reads
(``search_assets``, ``list_assets``, ``get_all_actors``) and omits many real
mutations. The two must never be conflated.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

# --- how much a command changes -------------------------------------------------
MUT_READ = "read"
MUT_WRITE = "write"
MUT_OPAQUE = "opaque"

# --- how far it can be undone ---------------------------------------------------
REVERT_AUTO = "auto"      # an inverse can be computed and posted
REVERT_MANUAL = "manual"  # recorded, but a human has to undo it
REVERT_NONE = "none"      # nothing to undo, or undoing is not meaningful

# --- what it touches (drives the slot namespace and the UI icon) ----------------
KIND_ACTOR = "actor"
KIND_ASSET = "asset"
KIND_DEVICE = "device"
KIND_VERSE = "verse"
KIND_MATERIAL = "material"
KIND_DATATABLE = "datatable"
KIND_NIAGARA = "niagara"
KIND_UMG = "umg"
KIND_ENTITY = "entity"
KIND_WORLD = "world"
KIND_OTHER = "other"


@dataclass(frozen=True)
class OpSpec:
    command: str
    mutates: str
    kind: str = KIND_OTHER
    #: Facet of the target this command changes; half of the journal slot.
    slot: str = ""
    #: Ceiling on revertability. The listener may downgrade (never upgrade) it
    #: per call — e.g. an object ref whose old target no longer exists.
    revertable: str = REVERT_MANUAL
    #: Brings something new into existence, so its inverse is a delete.
    creates: bool = False
    note: str = ""


def _op(
    command: str,
    kind: str,
    slot: str = "",
    *,
    revertable: str = REVERT_MANUAL,
    creates: bool = False,
    note: str = "",
) -> OpSpec:
    return OpSpec(
        command=command,
        mutates=MUT_WRITE,
        kind=kind,
        slot=slot,
        revertable=revertable,
        creates=creates,
        note=note,
    )


def _opaque(command: str, kind: str = KIND_OTHER, *, slot: str = "", creates: bool = False,
            revertable: str = REVERT_MANUAL, note: str = "") -> OpSpec:
    return OpSpec(command=command, mutates=MUT_OPAQUE, kind=kind, slot=slot,
                  revertable=revertable, creates=creates, note=note)


_MUTATIONS: tuple[OpSpec, ...] = (
    # -- actors ------------------------------------------------------------------
    _op("spawn_actor", KIND_ACTOR, "exists", revertable=REVERT_AUTO, creates=True),
    _op("spawn_actor_batch", KIND_ACTOR, "exists", revertable=REVERT_AUTO, creates=True,
        note="removed from the tool surface; still registered on the listener"),
    _op("duplicate_actor", KIND_ACTOR, "exists", revertable=REVERT_AUTO, creates=True),
    _op("delete_actors", KIND_ACTOR, "exists", revertable=REVERT_NONE,
        note="refused by the listener — recorded as a blocked attempt"),
    _op("set_actor_transform", KIND_ACTOR, "transform", revertable=REVERT_AUTO),
    _op("set_actor_properties", KIND_ACTOR, "props", revertable=REVERT_AUTO),
    _op("set_actor_label", KIND_ACTOR, "label", revertable=REVERT_AUTO),
    _op("set_actor_folder", KIND_ACTOR, "folder", revertable=REVERT_AUTO),
    _op("set_actor_tags", KIND_ACTOR, "tags", revertable=REVERT_AUTO),
    _op("attach_actor", KIND_ACTOR, "attach",
        note="no detach command exists, so a prior parent of None cannot be restored"),
    _op("snap_actor_to_ground", KIND_ACTOR, "transform", revertable=REVERT_AUTO),
    _op("snap_actor_to_grid", KIND_ACTOR, "transform", revertable=REVERT_AUTO),
    _op("align_actors", KIND_ACTOR, "transform", revertable=REVERT_AUTO),
    _op("distribute_actors", KIND_ACTOR, "transform", revertable=REVERT_AUTO),
    _op("set_object_property", KIND_ASSET, "props",
        note="no transaction and saves by default; capture the prior value before writing"),
    _op("set_mesh_collision", KIND_ASSET, "collision"),
    # -- creative devices --------------------------------------------------------
    _op("set_device_settings", KIND_DEVICE, "settings", revertable=REVERT_AUTO),
    _op("bulk_set_device_settings", KIND_DEVICE, "settings",
        note="bulk_* is retired from the tool surface"),
    _op("set_creative_device_fields", KIND_DEVICE, "settings",
        note="listed in the throttle set; superseded by Epic device tools"),
    # -- Verse @editable wiring --------------------------------------------------
    _op("set_verse_editable", KIND_VERSE, "editable", revertable=REVERT_AUTO),
    _op("wire_verse_device_ref", KIND_VERSE, "editable", revertable=REVERT_AUTO),
    _op("wire_verse_device_array", KIND_VERSE, "editable", revertable=REVERT_AUTO),
    _op("wire_verse_prop_assets", KIND_VERSE, "editable", revertable=REVERT_AUTO),
    _op("wire_player_spawners", KIND_VERSE, "editable"),
    _op("patch_verse_array_entry", KIND_VERSE, "editable", revertable=REVERT_AUTO),
    _op("resize_verse_array_field", KIND_VERSE, "editable",
        note="replaces the array with fresh defaults; prior contents are lost"),
    _op("set_verse_texture_icon", KIND_VERSE, "editable"),
    _op("set_currency_config_entries", KIND_VERSE, "editable"),
    _op("setup_verse_device", KIND_VERSE, "editable",
        note="retired from the tool surface; still registered"),
    _op("bulk_set_verse_editables", KIND_VERSE, "editable", note="bulk_* is retired"),
    _op("bulk_wire_verse_device", KIND_VERSE, "editable", note="bulk_* is retired"),
    # -- assets ------------------------------------------------------------------
    _op("rename_asset", KIND_ASSET, "exists", revertable=REVERT_AUTO,
        note="self-inverting; pair the revert with fixup_redirectors"),
    _op("duplicate_asset", KIND_ASSET, "exists", revertable=REVERT_AUTO, creates=True),
    _op("import_asset", KIND_ASSET, "exists", revertable=REVERT_AUTO, creates=True),
    _op("create_folder", KIND_ASSET, "exists", creates=True, revertable=REVERT_NONE,
        note="an empty content folder is harmless and UEFN forgets it on reload"),
    _op("delete_asset", KIND_ASSET, "exists", revertable=REVERT_NONE,
        note="refused by the listener — recorded as a blocked attempt"),
    _op("delete_directory", KIND_ASSET, "exists", revertable=REVERT_NONE,
        note="refused by the listener — recorded as a blocked attempt"),
    _op("export_asset", KIND_ASSET, "", revertable=REVERT_NONE,
        note="writes outside the project; nothing in the project changes"),
    _op("fixup_redirectors", KIND_ASSET, "", revertable=REVERT_NONE),
    # -- materials ---------------------------------------------------------------
    _op("create_material", KIND_MATERIAL, "exists", revertable=REVERT_AUTO, creates=True,
        note="silently deletes an existing asset at the path — see ADR 0002"),
    _op("create_material_instance", KIND_MATERIAL, "exists", revertable=REVERT_AUTO, creates=True,
        note="silently deletes an existing asset at the path — see ADR 0002"),
    _op("duplicate_material", KIND_MATERIAL, "exists", revertable=REVERT_AUTO, creates=True,
        note="silently deletes an existing asset at the destination — see ADR 0002"),
    _op("set_material_instance_scalar", KIND_MATERIAL, "param", revertable=REVERT_AUTO),
    _op("set_material_instance_vector", KIND_MATERIAL, "param", revertable=REVERT_AUTO),
    _op("set_material_instance_texture", KIND_MATERIAL, "param", revertable=REVERT_AUTO),
    _op("set_material_flags", KIND_MATERIAL, "flags"),
    _op("assign_material_to_mesh", KIND_MATERIAL, "slot"),
    _op("add_material_expression", KIND_MATERIAL, "graph"),
    _op("delete_material_expression", KIND_MATERIAL, "graph"),
    _op("clear_material_expressions", KIND_MATERIAL, "graph"),
    _op("set_material_expression_property", KIND_MATERIAL, "graph"),
    _op("connect_material_nodes", KIND_MATERIAL, "graph"),
    _op("disconnect_material_nodes", KIND_MATERIAL, "graph"),
    _op("connect_material_output", KIND_MATERIAL, "graph"),
    _op("layout_material_expressions", KIND_MATERIAL, "graph",
        note="cosmetic node layout only"),
    _op("recompile_material", KIND_MATERIAL, "", revertable=REVERT_NONE),
    # -- Niagara -----------------------------------------------------------------
    _op("create_niagara_system", KIND_NIAGARA, "exists", revertable=REVERT_AUTO, creates=True),
    _op("create_niagara_mesh", KIND_NIAGARA, "exists", revertable=REVERT_AUTO, creates=True),
    _op("add_niagara_emitter", KIND_NIAGARA, "graph"),
    _op("add_niagara_module", KIND_NIAGARA, "graph"),
    _op("add_niagara_renderer", KIND_NIAGARA, "graph"),
    _op("set_niagara_module_parameter", KIND_NIAGARA, "param"),
    _op("set_niagara_component_parameter", KIND_NIAGARA, "param"),
    _op("finalize_niagara_system", KIND_NIAGARA, ""),
    _op("control_niagara_actor", KIND_NIAGARA, "", revertable=REVERT_NONE,
        note="runtime playback control, not a project change"),
    # -- UMG ---------------------------------------------------------------------
    _op("create_widget_blueprint", KIND_UMG, "exists", revertable=REVERT_AUTO, creates=True),
    _op("add_widget_to_tree", KIND_UMG, "tree"),
    _op("remove_widget_from_tree", KIND_UMG, "tree"),
    _op("set_widget_property", KIND_UMG, "props"),
    _op("add_widget_binding", KIND_UMG, "bindings"),
    _op("remove_widget_binding", KIND_UMG, "bindings"),
    # -- data tables -------------------------------------------------------------
    _op("create_data_table", KIND_DATATABLE, "exists", revertable=REVERT_AUTO, creates=True),
    _op("fill_data_table_from_json", KIND_DATATABLE, "rows",
        note="replaces every row and saves; a row read-back is lossy, so this is never auto-reverted"),
    _op("fill_data_table_from_csv", KIND_DATATABLE, "rows",
        note="replaces every row and saves; a row read-back is lossy, so this is never auto-reverted"),
    # -- scene graph entities ----------------------------------------------------
    _op("create_entity", KIND_ENTITY, "exists", creates=True),
    _op("duplicate_entity", KIND_ENTITY, "exists", creates=True),
    _op("destroy_entity", KIND_ENTITY, "exists", revertable=REVERT_NONE,
        note="irreversible and not covered by the actor delete refusal"),
    _op("rename_entity", KIND_ENTITY, "label"),
    _op("set_entity_transform", KIND_ENTITY, "transform"),
    _op("set_entity_parent", KIND_ENTITY, "attach"),
    _op("add_entity_component", KIND_ENTITY, "components"),
    _op("remove_entity_component", KIND_ENTITY, "components"),
    _op("set_entity_component_property", KIND_ENTITY, "props"),
    _op("create_prefab_from_entities", KIND_ENTITY, "exists", creates=True),
    _op("create_empty_prefab", KIND_ENTITY, "exists", revertable=REVERT_AUTO, creates=True),
    _op("instantiate_prefab", KIND_ENTITY, "exists", creates=True),
    _op("convert_actors_to_entities", KIND_ENTITY, "exists", revertable=REVERT_NONE,
        note="documented one-way conversion"),
    # -- animation / IK ----------------------------------------------------------
    _op("create_ik_rig_asset", KIND_ASSET, "exists", revertable=REVERT_AUTO, creates=True),
    _op("create_ik_retargeter_asset", KIND_ASSET, "exists", revertable=REVERT_AUTO, creates=True),
    _op("set_retarget_root", KIND_ASSET, "props"),
    _op("add_retarget_chains", KIND_ASSET, "chains"),
    _op("remove_retarget_chains", KIND_ASSET, "chains"),
    _op("auto_map_retarget_chains", KIND_ASSET, "chains"),
    _op("retarget_animation", KIND_ASSET, "exists", creates=True),
    _op("retarget_animation_pipeline", KIND_ASSET, "exists", creates=True),
    _op("add_skeleton_socket", KIND_ASSET, "sockets"),
    _op("remove_skeleton_socket", KIND_ASSET, "sockets"),
    # -- world generation --------------------------------------------------------
    _op("pcg_generate", KIND_WORLD, "", revertable=REVERT_AUTO, creates=True),
    _op("terrain_generate", KIND_WORLD, "", revertable=REVERT_AUTO, creates=True),
    _op("terrain_remove_generated", KIND_WORLD, ""),
    _op("foliage_scatter", KIND_WORLD, "", revertable=REVERT_AUTO, creates=True),
    _op("foliage_clear_generated", KIND_WORLD, ""),
    _op("landscape_create", KIND_WORLD, "exists", revertable=REVERT_AUTO, creates=True),
    _op("landscape_rename", KIND_WORLD, "label"),
    _op("landscape_sculpt", KIND_WORLD, ""),
    _op("area_create", KIND_WORLD, "exists", revertable=REVERT_AUTO, creates=True),
    _op("blockout_layout", KIND_WORLD, "exists", revertable=REVERT_AUTO, creates=True),
    # -- internal --------------------------------------------------------------
    _op("ducky_revert_creation", KIND_OTHER, "exists", revertable=REVERT_NONE,
        note="the change journal's undo of a creation; never callable by an agent"),
)

_OPAQUE: tuple[OpSpec, ...] = (
    # Bracketed by a level snapshot, so a script that spawns actors names them and
    # can be undone. One that changes anything else is still manual, and says so.
    _opaque("execute_python", KIND_WORLD, slot="opaque", creates=True, revertable=REVERT_AUTO,
            note="arbitrary Python in the editor; only a snapshot diff can say what it did"),
    _opaque("exec_console_command", KIND_WORLD, slot="opaque", creates=True, revertable=REVERT_AUTO,
            note="fire-and-forget console command with no result"),
    _opaque("batch_commands", note="runs several commands in one call; retired from the tool surface"),
)

EDITOR_OPS: dict[str, OpSpec] = {spec.command: spec for spec in (*_MUTATIONS, *_OPAQUE)}

#: Commands that cannot change project state. Editor *view* state (selection,
#: camera, PIE) counts as read: nothing in the project differs afterwards.
READ_COMMANDS: frozenset[str] = frozenset(
    {
        # queries
        "actor_state_diff", "actor_state_snapshot", "area_list", "blockout_list_presets",
        "check_area_clear", "data_table_capabilities", "describe_class", "describe_commands",
        "device_graph_snapshot", "does_asset_exist", "find_clear_area", "foliage_get_stats",
        "foliage_list_sources", "get_actor_bone_transform", "get_actor_bounds",
        "get_actor_properties", "get_actors_in_radius", "get_all_actors", "get_asset_info",
        "get_data_table_info", "get_data_table_rows", "get_dependencies", "get_device_settings",
        "get_editor_log", "get_editor_stats", "get_entity_component_property", "get_entity_info",
        "get_fort_actor_info", "get_ground_z", "get_level_bounds", "get_level_info", "get_log",
        "get_material_expression_info", "get_material_info", "get_niagara_component_info",
        "get_niagara_system_info", "get_project_info", "get_referencers", "get_selected_actors",
        "get_selected_assets", "get_selected_entities", "get_static_mesh_info", "get_verse_api",
        "get_verse_editables", "get_viewport_camera", "get_widget_blueprint_info",
        "landscape_get_info", "landscape_list", "list_actor_classes", "list_actor_components",
        "list_assets", "list_creative_devices", "list_data_tables", "list_entities",
        "list_fort_actors", "list_material_expressions", "list_niagara_systems",
        "list_niagara_user_parameters", "list_scene_component_classes", "list_subsystems",
        "list_uefn_material_expression_classes", "list_verse_devices", "list_verse_modules",
        "list_verse_property_hashes", "list_verse_reference_types", "list_widget_bindings",
        "list_widget_blueprints", "measure_distance", "niagara_capabilities", "pcg_get_graph_info",
        "ping", "scene_graph_capabilities", "search_assets", "search_unreal_api",
        "search_verse_digest", "session_status", "status", "terrain_get_info", "umg_capabilities",
        "validate_uefn_asset", "worldgen_capabilities",
        "get_npc_definition_info", "list_npc_definitions", "npc_author_capabilities",
        # editor view / session state — no project change
        "focus_selected", "open_asset_in_uefn", "play_in_editor", "select_actors",
        "select_entities", "set_viewport_camera", "stop_pie", "take_high_res_screenshot",
        # process control
        "reload_listener", "shutdown",
        # saves flush edits that are already journaled; they are not changes of their own
        "save_all_dirty", "save_asset", "save_current_level", "save_directory",
    }
)

_UNKNOWN_NOTE = "a plugin command Ducky cannot model — record an inverse via _ducky or api.changeset.record"

_READ_PREFIXES = ("get_", "list_", "search_", "find_", "describe_", "inspect_", "read_", "query_")


def classify(command: str) -> OpSpec:
    """Spec for a listener command. Unknown commands are opaque unless named like a read.

    Store plugins register handlers this table cannot know. A ``list_*`` /
    ``get_*`` / ``*_capabilities`` from one of them is a read by any sane naming;
    recording it as an un-undoable mutation is what put "list npc definitions" on
    a user's remove-by-hand list.
    """
    name = (command or "").strip()
    spec = EDITOR_OPS.get(name)
    if spec is not None:
        return spec
    if name in READ_COMMANDS:
        return OpSpec(command=name, mutates=MUT_READ, revertable=REVERT_NONE)
    if name.startswith(_READ_PREFIXES) or name.endswith("_capabilities"):
        return OpSpec(command=name, mutates=MUT_READ, revertable=REVERT_NONE,
                      note="classified by name; not in the table")
    return OpSpec(command=name, mutates=MUT_OPAQUE, revertable=REVERT_MANUAL, note=_UNKNOWN_NOTE)


def is_mutation(command: str) -> bool:
    """True when the command can change project state (including unknown commands)."""
    return classify(command).mutates != MUT_READ


def classify_plugin_tool(name: str, annotations: Any = None) -> OpSpec:
    """Spec for a nested-MCP tool (Epic ``unreal__*`` and friends).

    Prefers the server's own MCP annotations. Those are only available because
    ``client_pool`` preserves them; without them this falls back to the tool
    name, biased towards recording — an empty diff is cheaper than a blind spot.
    """
    leaf = (name or "").rsplit("__", 1)[-1].strip()
    read_only = getattr(annotations, "readOnlyHint", None) if annotations is not None else None
    destructive = getattr(annotations, "destructiveHint", None) if annotations is not None else None
    if read_only is True:
        return OpSpec(command=leaf, mutates=MUT_READ, revertable=REVERT_NONE)
    if destructive is True:
        return OpSpec(command=leaf, mutates=MUT_OPAQUE, revertable=REVERT_NONE,
                      note="the tool declares itself destructive")
    if annotations is not None and (read_only is False or destructive is False):
        return OpSpec(command=leaf, mutates=MUT_WRITE, revertable=REVERT_MANUAL,
                      note="declared as a write by the tool's own annotations")
    if leaf.startswith(_READ_PREFIXES):
        return OpSpec(command=leaf, mutates=MUT_READ, revertable=REVERT_NONE,
                      note="classified by name; the tool declares no annotations")
    return OpSpec(command=leaf, mutates=MUT_OPAQUE, revertable=REVERT_MANUAL,
                  note="the tool declares no annotations — treated as opaque")


def usable_ident(value: str) -> str:
    """A real actor/asset id, or empty when UEFN handed back an all-zero GUID.

    ``get_actor_guid`` often returns 32 zeros for Creative devices. Treating that
    as an id collapses every labelled trigger into one Changes row.
    """
    text = (value or "").strip()
    compact = text.replace("{", "").replace("}", "").replace("-", "")
    if not compact or set(compact) <= {"0"}:
        return ""
    return text


def slot_path(kind: str, ident: str, facet: str = "", program: str = "uefn") -> str:
    """Journal slot for one target and facet, e.g. ``uefn://actor/<guid>/transform``.

    A slot is a *target*, not a call: repeated edits to one actor's transform
    share a slot, so they collapse into one row and one restore to the state
    before the run touched it. ``program`` namespaces programs so a Blender Cube
    never collides with a UEFN Cube (``blender://object/Cube/mesh``).
    """
    prog = (program or "uefn").strip() or "uefn"
    target = (ident or "").strip().replace("\\", "/").strip("/") or "unknown"
    base = f"{prog}://{kind or KIND_OTHER}/{target}"
    facet = (facet or "").strip("/")
    return f"{base}/{facet}" if facet else base


def downgrade(spec: OpSpec, revertable: str, note: str = "") -> OpSpec:
    """Lower a spec's revertability (never raise it) for one call."""
    order = {REVERT_AUTO: 2, REVERT_MANUAL: 1, REVERT_NONE: 0}
    if order.get(revertable, 1) >= order.get(spec.revertable, 1):
        return spec
    return replace(spec, revertable=revertable, note=note or spec.note)
