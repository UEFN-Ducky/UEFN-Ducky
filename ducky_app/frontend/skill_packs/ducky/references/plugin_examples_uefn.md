---
description: "Worked plugin examples for UEFN: Verse templates, tools backed by listener handlers inside UEFN, and Blender and Meshy pipelines"
metadata:
  order: 5
  label: "Plugin examples: Verse, UEFN, Blender, Meshy"
  default_enabled: false
  load_condition: "Building a plugin that ships Verse templates, runs code inside the UEFN editor, or drives Blender, Meshy or other 3D pipelines"
---

# Plugin examples: Verse, UEFN, Blender, Meshy

Every plugin still needs `agent.tools`, a tool per action, a workflow node plus a
template that uses it, and `skills/<id>/SKILL.md` (`ai_plugins`). Search first:
`ducky_find_tools("verse template")`, `ducky_find_tools("meshy")`,
`ducky_find_tools("blender")`; most of the work is calling tools that already exist.

## 1. Verse templates

A plugin can add Verse files and multi-file packs to New file → templates. Prefix
template ids with the plugin id: ids are shared by every plugin.

```json
"contributes": {
  "verse.templates": [
    { "id": "score_kit.counter", "name": "Score counter", "icon": "🔢", "order": 50,
      "description": "A device that counts points per player.",
      "file": "verse/score_counter.verse" },
    { "id": "score_kit.pack", "name": "Score Kit", "icon": "🏆", "folder": "ScoreKit",
      "description": "Score API, leaderboard and HUD.", "connects": ["score_kit.counter"],
      "files": [
        { "path": "score_api.verse", "file": "verse/pack/score_api.verse" },
        { "path": "leaderboard.verse", "file": "verse/pack/leaderboard.verse" },
        { "path": "hud/score_hud.verse", "file": "verse/pack/score_hud.verse" }
      ] }
  ]
}
```

- `file` / `files[].file` point at files in the plugin (or give `content` inline);
  each file up to 200 KB. A pack's `folder` is one folder under `Content/Verse` when
  applied; `files[].path` is relative to it.
- Keep the `.verse` files outside `backend/` (a `verse/` folder at the plugin root).
- Verify them with UEFN open: `verse_template_verify("score_kit.counter,score_kit.pack")`
  builds them in the project and maps each compile error to its template. Fix until
  clean; check APIs with `search_verse_digest` instead of guessing.

The plugin's tool applies them through the Verse plugin's tool when it is on:

```python
def register(api) -> None:
    TEMPLATES = ["score_kit.counter", "score_kit.pack"]

    def apply(template_id: str = "score_kit.pack", parent: str = "Content/Verse") -> dict:
        if template_id not in TEMPLATES:
            return {"ok": False, "error": f"Pick one of {', '.join(TEMPLATES)}."}
        try:
            return api.call_tool("verse_template_apply", {"template_id": template_id, "parent_relative": parent})
        except ValueError as exc:
            return {"ok": False, "error": f"{exc}. Turn on the Verse plugin in the Store, or use New file → templates."}

    @api.tool(listener=False)
    def score_kit_apply(template_id: str = "score_kit.pack", parent: str = "Content/Verse") -> dict:
        """Add a Score Kit Verse template to the project (score_kit.counter or score_kit.pack)."""
        return apply(template_id, parent)

    @api.register_pipeline_node("score_kit.apply")
    def node_apply(ctx: dict) -> dict:
        cfg = ctx.get("config") or {}
        return apply(str(cfg.get("template_id") or "score_kit.pack"), str(cfg.get("parent") or "Content/Verse"))
```

For one-off reusable scaffolds the user keeps for themselves (no plugin), use
`ducky_verse_template_*` (`verse_templates` reference).

## 2. UEFN-connected tools (listener handlers)

Code that must run **inside the UEFN editor** (Unreal Python) goes in the plugin's
`listener/` folder. When the plugin is enabled with UEFN open, Ducky copies it into
the editor's listener as `listener.plugins.<plugin_id>` and reloads it. Your backend
calls it with `api.listener(command, params)`.

Use this only when no existing tool does the job: `ducky_find_tools` first (actors,
assets, devices, materials, Verse, widgets and more already exist), and prefer
`api.call_tool` on those.

**`listener/__init__.py`**

```python
"""Prop Census handlers, run inside UEFN."""

from __future__ import annotations

from . import census as census  # noqa: F401  (importing registers the commands)
```

**`listener/census.py`**

```python
from __future__ import annotations

import unreal

from listener.dispatch import register


@register("prop_census_count")
def prop_census_count(class_name: str = "StaticMeshActor") -> dict:
    """Count actors of one class in the open level."""
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = [a for a in subsystem.get_all_level_actors() if a.get_class().get_name() == class_name]
    return {"ok": True, "class_name": class_name, "count": len(actors)}
```

**`backend/__init__.py`**

```python
from __future__ import annotations


def register(api) -> None:
    def count(class_name: str = "StaticMeshActor") -> dict:
        try:
            return api.listener("prop_census_count", {"class_name": class_name or "StaticMeshActor"}, timeout=30)
        except (ConnectionError, RuntimeError, TimeoutError) as exc:
            return {"ok": False, "error": f"UEFN didn't answer: {exc}"}

    # Talks to UEFN: keep the listener gate (no listener=False).
    @api.tool()
    def prop_census_count(class_name: str = "StaticMeshActor") -> dict:
        """Count the actors of one class in the open UEFN level."""
        return count(class_name)

    @api.register_pipeline_node("prop_census.count")
    def node_count(ctx: dict) -> dict:
        return count(str((ctx.get("config") or {}).get("class_name") or "StaticMeshActor"))
```

- Command names are global across plugins: prefix them with the plugin id.
- Handlers run on UEFN's main thread with keyword arguments (unknown params are
  dropped). Return a JSON-friendly dict; raise for failures (the backend gets a
  `RuntimeError` with the message).
- `listener/` is plain Python for UEFN: `import unreal`, `from listener… import …`,
  no imports from Ducky's app. It ships as source (it isn't compiled).
- An editor change your handler makes should be recorded: return enough to undo it
  and call `api.changeset.record(…, slot="uefn://…")` from the backend, or use the
  app's own tools, which already record.

## 3. Blender and Meshy pipelines

**Start with a template of built-in nodes.** Images, 3D, rigging, Blender and UEFN
import are already workflow nodes (`workflows` reference): most pipeline plugins
need a template and a little glue, not new generators.

```json
"templates": [{
  "id": "prop-forge-barrel", "label": "Prompt to UEFN prop", "category": "Prop Forge",
  "graph": {
    "nodes": [
      { "id": "q", "type": "input.text", "x": 0, "y": 0, "config": { "value": "A wooden barrel, stylized, plain background" } },
      { "id": "gen", "type": "image.generate", "x": 300, "y": 0, "config": {} },
      { "id": "cut", "type": "image.remove_bg", "x": 600, "y": 0, "config": {} },
      { "id": "m", "type": "mesh.from_image", "x": 900, "y": 0, "config": { "fallback": true } },
      { "id": "fit", "type": "prop_forge.tidy", "x": 1200, "y": 0, "config": {} },
      { "id": "u", "type": "uefn.import", "x": 1500, "y": 0, "config": { "folder": "PropForge" } }
    ],
    "edges": [
      { "source": "q", "target": "gen", "kind": "data", "source_pin": "text", "target_pin": "prompt" },
      { "source": "gen", "target": "cut", "kind": "data", "source_pin": "image", "target_pin": "image" },
      { "source": "cut", "target": "m", "kind": "data", "source_pin": "image", "target_pin": "image" },
      { "source": "m", "target": "fit", "kind": "data", "source_pin": "mesh", "target_pin": "mesh" },
      { "source": "fit", "target": "u", "kind": "data", "source_pin": "mesh", "target_pin": "file" }
    ]
  }
}]
```

Paid nodes only spend when `config.spend` is on; ship templates with spend **off**
and ask the user (`ducky_ask_user`, naming each paid step and the cost its backend
shows) before turning it on.

**Your own step**, here a value node (`"exec": false`) that tidies the model in Blender:

```json
{ "id": "prop_forge.tidy", "label": "Tidy in Blender", "group": "Prop Forge", "exec": false,
  "inputs": [{ "id": "mesh", "label": "Model", "type": "mesh", "required": true }],
  "outputs": [{ "id": "mesh", "label": "Model", "type": "mesh" }] }
```

```python
from __future__ import annotations

import json
from pathlib import Path

# Runs inside Blender (blender_execute_blender_code): kept as text, never a .py file you read.
# It imports, exports and removes only its own objects: the user's scene is left as it was.
TIDY = """
import bpy
src, out = {src!r}, {out!r}
before = set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=src)
new = [ob for ob in bpy.data.objects if ob not in before]
bpy.ops.object.select_all(action="DESELECT")
for ob in new:
    ob.select_set(True)
    if ob.type == "MESH":
        for poly in ob.data.polygons:
            poly.use_smooth = True
bpy.ops.export_scene.gltf(filepath=out, use_selection=True)
bpy.data.batch_remove(new)
result = {{"out": out}}
"""


def register(api) -> None:
    def tidy(src: str, folder: str) -> dict:
        if not src.lower().endswith(".glb") or not Path(src).is_file():
            return {"ok": False, "error": "Wire in a .glb model."}
        out = str(Path(folder) / (Path(src).stem + "_tidy.glb"))
        try:
            api.call_tool("blender_execute_blender_code", {"code": TIDY.format(src=src, out=out)})
        except ValueError as exc:
            return {"ok": False, "error": f"Blender: {exc}. Is Blender open with the Ducky add-on?"}
        return {"ok": True, "mesh": {"kind": "mesh", "path": out, "name": Path(out).name}}

    @api.tool(listener=False)
    def prop_forge_tidy(path: str = "", folder: str = "") -> dict:
        """Smooth-shade a .glb model in Blender and save a tidy copy next to it (or in folder)."""
        return tidy(path, folder or str(Path(path).parent))

    @api.register_pipeline_node("prop_forge.tidy")
    def node_tidy(ctx: dict) -> dict:
        mesh = (ctx.get("inputs") or {}).get("mesh") or {}
        src = mesh.get("path", "") if isinstance(mesh, dict) else str(mesh)
        return tidy(src, str(ctx.get("artifact_dir") or Path(src).parent))
```

- Blender code goes to `blender_execute_blender_code` as text; it assigns a
  JSON-friendly `result`. `blender_status` says whether Blender is connected.
- Meshy from a tool: `meshy_text_to_3d(prompt, confirm_spend=…, wait=True,
  output_dir=…)`, `meshy_import_to_uefn(url_or_path, destination_path)`,
  `meshy_import_to_blender(url_or_path)`. A tool that spends takes
  `confirm_spend: bool = False`, refuses without it (saying the cost), and is listed
  in `agent.tools.destructive_tools`.
- Output files of a node go in its `artifact_dir`; the next node gets them as file
  refs `{kind, path, name}`.
