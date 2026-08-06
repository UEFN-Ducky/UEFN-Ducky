"""Text blobs for MCP: help models write correct ``execute_python`` without repo example files."""

HINTS_GENERAL = """
UEFN editor Python (execute_python) — general
- Code runs in the editor with full ``unreal``; assign to ``result`` to return a value.
- Prefer ``set_editor_property("PropName", value)`` when direct attributes fail (structs, reflection).
- Try both snake_case and PascalCase for property names if one fails (e.g. ``input_name`` vs ``InputName``).
- Many actors in UEFN are ``Fort*`` classes (e.g. ``FortStaticMeshActor``); use ``get_all_actors_of_class``
  with the class that actually exists in the level.
- NEVER construct UObject classes directly (``unreal.SkeletalMeshSocket()`` etc.) — that makes a
  malformed object with no outer, and stitching it into an asset is a NATIVE access violation that
  Python cannot catch: the whole editor dies. Structs (``unreal.Vector()``, ``unreal.CustomInput()``)
  are fine. For asset-owned objects use ``unreal.new_object(unreal.X, outer=owning_asset)`` — or the
  dedicated tool if one exists (skeleton sockets: add_skeleton_socket / list_skeleton_sockets).
- Blueprint SCS/subobject surgery (SubobjectDataSubsystem) crashes UEFN builds — read
  ``bp.simple_construction_script`` freely, but never mutate it from Python.
"""

HINTS_MATERIALS = """
Material graph via MaterialEditingLibrary (UEFN)
- ``MaterialExpressionConstant3Vector``: use ``set_editor_property("constant", unreal.LinearColor(r,g,b,1))``,
  not ``expr.constant = ...`` (often AttributeError).
- ``MaterialExpressionConstant``: no ``.constant``. Use ``set_editor_property("r"|"g"|"b"|"a", float)``.
- Scalars: ``MaterialExpressionScalarParameter`` — ``set_editor_property("parameter_name", unreal.Name("X"))``,
  ``set_editor_property("default_value", 1.0)``.
- ``MaterialExpressionCustom``: ``set_editor_property("code", "return float4(...);")``,
  ``set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT4)``.
- ``CustomInput``: do NOT set ``ci.input_name``. Build ``ci = unreal.CustomInput()`` then
  ``ci.set_editor_property("input_name", unreal.Name("T"))``; if that fails use ``"InputName"``.
  Then ``custom.set_editor_property("inputs", [ci])`` and connect with
  ``connect_material_expressions(mult, "", custom, "T")``.
- If Custom nodes misbehave, build the effect with standard nodes: ``MaterialExpressionTime``,
  ``Multiply``, ``Add``, ``Sine``, ``ConstantBiasScale`` (bias/scale via set_editor_property), etc.
- After edits: ``MaterialEditingLibrary.recompile_material(mat)`` and
  ``EditorAssetLibrary.save_loaded_asset(mat)``.
"""

HINTS_MATERIAL_END_TO_END = """
End-to-end: create a solid material that exists in Content (UEFN)
1. ``folder = "<content_root>Materials"`` from ``get_project_info().content_root``
   (e.g. ``/MyProject/Materials``). **Never invent ``/Game/Materials``** — cook fails with
   Disallowed reference / unsaved ``/Game`` packages. Prefer ``create_material`` (omit folder;
   listener pins the project mount).
2. ``EditorAssetLibrary.make_directory(folder)`` if missing.
3. ``asset_tools.create_asset("M_MyName", folder, unreal.Material, unreal.MaterialFactoryNew())`` —
   if this returns ``None``, stop and return that error (wrong path, read-only project, or name clash).
4. ``MaterialEditingLibrary.delete_all_material_expressions(mat)`` on the new material.
5. Add ``MaterialExpressionConstant3Vector``; set color with ``set_editor_property("constant", unreal.LinearColor(...))``.
6. ``connect_material_property(const_expr, "", unreal.MaterialProperty.MP_BASE_COLOR)``.
7. ``MaterialEditingLibrary.recompile_material(mat)`` then ``mat.modify(True)``.
8. Save: ``EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=False)`` then
   ``EditorLoadingAndSavingUtils.save_dirty_packages(False, True)`` (or keyword form).
9. Verify: ``EditorAssetLibrary.does_asset_exist(f"{folder}/M_MyName")`` should be True;
   ``EditorAssetLibrary.load_asset(...)`` should return a ``Material``.
10. If step 9 fails but no Python error: stale browser — try ``EditorAssetLibrary.delete_asset(path)`` and repeat,
    or ask the user to **File > Save All** / **Save Current Level** once (UEFN sometimes defers disk flush).
11. Assign to mesh with ``mesh.set_material(0, loaded_material)``; then save dirty packages again so the **level**
    references persist.
"""

HINTS_ACTORS = """
Actors / mesh components
- ``StaticMeshComponent`` on mesh actors; ``mesh.set_material(slot_index, material)``.
- Labels: ``actor.get_actor_label()`` — match the Outliner name you see.
"""

HINTS_SAVE_ASSETS = """
Saving materials / .uasset files (UEFN)
- ``MaterialExpressionConstant3Vector``: prefer
  ``expr.set_editor_property("constant", unreal.LinearColor(r, g, b, 1.0))``.
  Direct ``expr.constant = ...`` often raises AttributeError in Python builds.
- After building the graph: ``MaterialEditingLibrary.recompile_material(mat)``, then
  ``mat.modify(True)`` (or ``mat.modify()``) so the outer package is dirty.
- If ``EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)`` returns False, still try:
  ``EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=False)``,
  ``EditorAssetLibrary.save_directory("<content_root>Materials", only_if_is_dirty=False)`` (if the API exists),
  and ``EditorLoadingAndSavingUtils.save_dirty_packages(save_map_packages=False, save_content_packages=True)``.
  On TypeError, try positional ``save_dirty_packages(False, True)``.
- ``EditorLoadingAndSavingUtils.save_packages(packages_to_save=[pkg], only_dirty=False)`` can return False
  if the package is read-only, not writable, or the editor blocked the save — it is not always ``Package.is_dirty``:
  that method may not exist on ``Package`` in Python; do not depend on it.
- ``Paths.project_dir()`` / ``Paths.project_content_dir()`` often look like relative ``../../../FortniteGame/``.
  ``SystemLibrary.get_project_content_directory()`` may resolve to the **Fortnite install** tree, while your
  **island** still mounts ``/Game/...`` in the editor. Empty ``Content/Materials`` on disk next to a ``.uefnproject``
  does not prove failure: assets may live under Epic’s project layout until **Save All** / level save completes.
- Fallback: tell the user to **File > Save All** or save the level in UEFN after the script runs.
"""

HINTS_MATERIALS_RAINBOW = """
Rainbow / animated materials (Materials Store plugin + registry)
- Tools live on the Materials desktop plugin (nested under uefn-ducky). Prefer registry:
  ``list_uefn_material_expression_classes`` → ``create_material`` → graph tools → ``recompile_material``.
- Standard-node rainbow: Time → Multiply(speed) → Multiply(2π) → Sine per channel with phase offsets;
  use ``MaterialExpressionConstantBiasScale`` (bias/scale via set_editor_property).
- No Custom/HLSL in UEFN. Full recipe in Materials skill reference **Rainbow / animated color**.
- Connect emissive + base color for visible glow; finish with ``save_current_level()``.
"""

HINTS_FORT_DEVICE_SCAN = """
Fortnite devices / Fort* actors
- Use ``list_fort_actors`` with ``class_prefix="Fort"`` instead of dumping all actors.
- ``get_fort_actor_info`` for read-only inspection — most Fort* classes are not fully writable from Python.
- Verse device API: ``list_verse_devices`` / ``search_verse_digest`` — compact digest search vs full file dump.
- Gameplay wiring stays in Verse; Python is for editor discovery and layout.
"""

HINTS_LEVEL_DESIGN = """
Level design / spatial placement (registry tools)
- NEVER place blind. Loop: ``get_actor_bounds`` (know the size) -> ``check_area_clear`` or
  ``find_clear_area`` (know it fits) -> ``spawn_actor`` -> ``snap_actor_to_ground`` ->
  ``take_high_res_screenshot`` (verify visually).
- ``measure_distance`` gives the SURFACE gap between two actors' bounds — use it for
  "how much space is between X and Y", door widths, lane widths.
- Rows/grids: spawn one at a time, then ``align_actors`` + ``distribute_actors``
  (or compute each position and ``snap_actor_to_grid``). No batch spawn exists — by design.
- ``get_ground_z`` reports method "trace" or "aabb_fallback"; the fallback is bounds-top
  approximation — fine for flat floors, verify on slopes.
- Organize as you go: ``set_actor_label`` + ``set_actor_folder``; finish with ``save_current_level()``.
"""

HINTS_NIAGARA = """
Niagara VFX (registry tools; capability-guarded)
- ``niagara_capabilities`` FIRST — UEFN builds vary in what Niagara API Python sees.
- Find/inspect: ``list_niagara_systems`` then ``get_niagara_system_info`` (user params, emitters when exposed).
- Place in level: generic ``spawn_actor(asset_path=<NiagaraSystem path>)`` — yields a NiagaraActor.
- Tune: ``set_niagara_component_parameter`` (USER params; engine silently ignores unknown names) and
  ``control_niagara_actor`` (activate/deactivate/reset/reinitialize).
- Emitter/module graph editing is NOT exposed to Python — author the system in the editor, drive user params from tools.
- Publish/cook blockers: ``validate_uefn_asset(asset_path)`` is authoritative. ``get_dependencies`` is supporting
  evidence only (``/Script/NiagaraEditor`` alone does not prove Custom HLSL).
- If validation fails and graph APIs are unavailable: duplicate a **validated in-plugin** template
  (another project ``NS_*`` that already validates) onto the blocked path, warn about visual change,
  then re-validate. Do **not** copy ``/CRD_VFX_Spawner`` / ``/Game/Effects`` systems into the plugin —
  they often fail plugin reference validators after duplication. Do not claim you edited emitter graphs from Python.
"""

HINTS_DATA_TABLES = """
Data tables (registry tools; capability-guarded)
- ``data_table_capabilities`` FIRST, then ``get_data_table_info`` — row struct + row names (+ columns when exposed).
- ``get_data_table_rows`` needs explicit ``columns`` (row-struct field names) when discovery is unavailable.
- No per-row write API in Python: read rows -> edit JSON -> ``fill_data_table_from_json`` (REPLACES all rows).
- New table: ``create_data_table`` with an ``unreal`` struct name or a user-struct asset path.
"""

HINTS_SCENE_GRAPH = """
Scene Graph entities/components/prefabs (registry tools; capability-guarded)
- ``scene_graph_capabilities`` FIRST — needs SceneGraphScriptSubsystem (newer UEFN builds).
- Entities live under the level's LevelEntity; ``list_entities`` / ``get_entity_info`` to inspect.
- Build up: ``create_entity`` -> ``add_entity_component`` (alias like ``mesh_component`` +
  PROJECT ``asset_path`` for meshes/particles/sounds) -> ``set_entity_component_property``.
- Transforms are SpatialMath: translation/scale = [forward, left, up], rotation = quaternion [x,y,z,w]
  (left = -Y in Unreal terms). Use ``set_entity_transform``; there is no actor transform here.
- Component property names are the case-sensitive Verse digest names (``Visible``, ``Collidable``);
  check with ``get_verse_api(<component class>)``. Mangled ``__verse_0x...`` names are handled internally.
- Prefabs: ``create_empty_prefab`` makes a blank EntityPrefab under the project mount;
  ``create_prefab_from_entities`` packages level entities into a new prefab (sources become
  an instance). ``instantiate_prefab`` places via ``spawn_actor_from_object`` (same as
  Content Browser drag → EntityProxyActor); pass SpatialMath ``translation`` to place.
  Verse class lands in Assets.digest after the next Verse build. Use Verse ``P_*`` spawn
  only for runtime dynamic spawn/despawn — static always-on instances stay in the level.
- Verse-side work (custom components, runtime spawning) is code: query ``get_verse_api`` for
  entity/component/mesh_component signatures, then write .verse files via workspace tools.
- Finish with ``save_current_level()`` — nothing auto-saves.
"""

HINTS_PCG_REGENERATE = """
PCG regenerate workflow
- Registry: ``pcg_get_graph_info`` then ``pcg_generate`` with ``force=true`` to cleanup + regenerate.
- Actor must have a ``PCGComponent``; find actors via ``list_fort_actors`` or ``get_all_actors`` with class filter.
- PCG graph editing is not exposed — only generate/inspect metadata.
"""

HINTS_WORLDGEN = """
Worldgen terrain + foliage (capability-guarded)
- ``worldgen_capabilities`` FIRST — returns ``terrain_backend`` (mesh|unavailable) and
  ``foliage_backend`` (instanced_foliage|hism|unavailable).
- Terrain: ``terrain_generate`` builds ONE heightfield StaticMesh actor via GeometryScript
  (safe in UEFN). Creating blank Landscape actors from Python is unsafe; do not attempt it.
  Use stamps=[{type:hill|valley|flatten,x,y,radius,strength,height}] to form the land.
- Foliage: ``foliage_list_sources`` then ``foliage_scatter`` — instances, never one actor per tree.
  Still needs source meshes/FoliageTypes; clearing uses ``foliage_clear_generated``.
- Regenerate = clear/remove generated → generate/scatter again. Tag/folder: Generated/WorldgenDemo.
- Always ``save_current_level`` after worldgen writes. Verify with camera + screenshot.
"""


HINTS_UMG = """
UMG / Widget Blueprints (registry tools; capability-guarded)
- ``umg_capabilities`` FIRST — never call ToolsetRegistry.get_all_toolset_json_schemas /
  get_toolset_json_schema from execute_python (dumps crash UEFN with ACCESS_VIOLATION).
- Create: ``create_widget_blueprint(asset_name="UW_MyHud", folder="")`` (auto-pins
  ``{content_root}UI`` — never invent ``/Game/UI``).
- Inspect: ``list_widget_blueprints`` then ``get_widget_blueprint_info`` (member vars =
  Verse fields once authored; tree via UMGToolSet.GetWidgets; MVVM bindings).
- Scaffold tree: ``add_widget_to_tree`` / ``set_widget_property`` / ``remove_widget_from_tree``.
  Property names vary — set_widget_property lists them first. Polish in the designer via
  ``open_asset_in_uefn``.
- Bindings: ``list_widget_bindings`` / ``add_widget_binding`` / ``remove_widget_binding``
  (MVVMEditorSubsystem; complex binds finish in View Bindings panel).
- Runtime Verse: after the UW_* type appears in Assets digest, ``var W : UW_X = UW_X{}`` then
  ``GetPlayerUI[Player].AddWidget(W, player_ui_slot{…})``. Drive with Verse fields (38.00+)
  and Verse field events (39.40+). Load skill_read_subskill("verse", "umg_widgets").
- WidgetTree/RootWidget editor properties are PROTECTED — do not get_editor_property them.
"""


def hints_for_topic(topic: str) -> str:
    t = (topic or "all").strip().lower()
    parts: list[str] = []
    if t in ("all", "general", ""):
        parts.append(HINTS_GENERAL.strip())
    if t in ("all", "materials", "material"):
        parts.append(HINTS_MATERIALS.strip())
        parts.append(HINTS_MATERIAL_END_TO_END.strip())
    if t in ("all", "materials_rainbow", "rainbow"):
        parts.append(HINTS_MATERIALS_RAINBOW.strip())
    if t in ("all", "actors", "actor"):
        parts.append(HINTS_ACTORS.strip())
    if t in ("all", "save", "saving", "disk", "uasset", "persist", "assets"):
        parts.append(HINTS_SAVE_ASSETS.strip())
    if t in ("all", "fort_device_scan", "fort", "verse", "devices"):
        parts.append(HINTS_FORT_DEVICE_SCAN.strip())
    if t in ("all", "pcg_regenerate", "pcg"):
        parts.append(HINTS_PCG_REGENERATE.strip())
    if t in ("all", "level_design", "layout", "placement", "spatial"):
        parts.append(HINTS_LEVEL_DESIGN.strip())
    if t in ("all", "niagara", "vfx", "particles"):
        parts.append(HINTS_NIAGARA.strip())
    if t in ("all", "data_tables", "datatable", "datatables", "table"):
        parts.append(HINTS_DATA_TABLES.strip())
    if t in ("all", "scene_graph", "scenegraph", "entity", "entities", "prefab", "prefabs"):
        parts.append(HINTS_SCENE_GRAPH.strip())
    if t in ("all", "worldgen", "landscape", "terrain", "foliage", "vegetation", "biome", "forest"):
        parts.append(HINTS_WORLDGEN.strip())
    if t in ("all", "umg", "widget", "widgets", "userwidget", "viewmodel", "view_binding", "viewbinding"):
        parts.append(HINTS_UMG.strip())
    if not parts:
        parts.append(HINTS_GENERAL.strip())
        parts.append(HINTS_MATERIALS.strip())
        parts.append(HINTS_ACTORS.strip())
        parts.append(HINTS_SAVE_ASSETS.strip())
    return "\n\n".join(parts)
