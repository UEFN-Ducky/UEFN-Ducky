---
description: "Workflows — build, change, explain and run them with the workflow tools, and show the user on the canvas as you go"
metadata:
  order: 6
  label: "Workflows"
  default_enabled: false
  load_condition: "User asks about Workflows, or to build, change, explain, run, organize or lock a workflow"
---

## Workflows

A workflow is a graph of nodes that runs on this PC: a start (Manual, Chat input,
Cron, a plugin trigger, or Inputs for a reusable one), steps (tools, Duckies,
agents, waits, branches, loops, UEFN play tests, other workflows) and an end.
Each belongs to **Local** (this PC) or a **team** (synced to every member).
Do the work with the tools below; never tell the user to open the tab — the
tools open it and show them what you did.

### The editor (what the user sees)

- **Workflows list** (left): folders per owner, a green/red light for on/off and
  an icon for what starts it (▶ Manual, 💬 Chat, clock = schedule, ⚡ trigger,
  puzzle = reusable). Right-click a workflow: Open, Rename, Duplicate, On,
  Move out of folder, Delete. Ctrl + click / Shift + click picks several: right-click
  them for Duplicate, Turn on / off, Move out of their folders, Delete; drag them
  into a folder; Delete key deletes. Right-click a folder: New workflow here, New
  folder inside, Rename, Remove folder (keeps its workflows). Every delete asks first.
  The header folds the list into a small pill; its edge drags to resize.
- **Top bar**: the name on its own on the left; the buttons in a pill on the right,
  right to left: Play (test run; Stop while it runs), the on/off light,
  Run on this PC (team schedules), Save, Duplicate, Move or copy, History, Redo,
  Undo, Delete. While a workflow runs the step running now glows, the wire it came
  along runs thick and bright, and the run log fills in step by step.
- **Canvas** (Unreal style): Select tool `V` boxes nodes, Hand `H` (or hold
  Space) moves around; both click nodes and wires. Wheel zooms 5%–400%; far
  out the cards thin to their title. `F` (or the Fit button) fits the selection:
  one node zooms in on it, a group comes with its frame, nothing selected fits
  everything. Right-click or press-and-hold the canvas, or `+`, to add a node;
  dragging from a pin into empty space adds a node already wired. `Delete`
  removes, `Ctrl+G` groups, `Ctrl+Shift+G` ungroups, `Ctrl+Z` / `Ctrl+Y`.
  Bottom toolbar: Select, Hand, Run log, +, Fit, the zoom % menu (zoom, the grid:
  squares, dots or nothing, and the Snap to grid switch) and Outline (every group
  and node as a tree; pick one to jump to it). Dragging a node moves it without
  selecting it; a plain click selects it. Hovering a workflow in the list shows a
  card with the graph in miniature; click it to open. Ctrl + scroll over any panel
  (list, top bar, details, bottom toolbar, run log) zooms just that panel.
- **Details panel** (slides over the right side, nothing moves): one tab per
  selected node or group. Header icons: group the selection, lock (lit while
  locked), delete (red), close; the pen beside the lock edits: the name and
  description become editable in place, and the color swatches and icon picker
  show (click the big icon for another emoji). Then Settings: every tool input has its own field, and
  `{{field}}` uses a value from an earlier step. Groups: Ungroup, Make reusable
  (moves the group into its own workflow and calls it from here).
- **Run log** (bottom bar): steps of the last run; Copy log, Clear log.
- Settings → Appearance → Workflows: every canvas color and size.

### Graph shape

```
graph = {
  nodes:  [{id, type, x, y, config, label?, description?, color?, icon?, locked?}],
  edges:  [{source, target, kind}                     # white run wire: what runs next
           | {source, target, kind:"data", source_pin, target_pin}],  # value wire
  groups: [{id, name, node_ids, parent_id?, color?, icon?, locked?}]
}
```

- Cards are 240 wide at `x, y` (82 tall, plus 24 per pin row); keep ~300 between columns, ~120 between rows (the
  editor snaps dragged nodes to its 16px grid).
- `kind`: `main`; `true` / `false` after `flow.branch`, `logic.if` and
  `fortnite.servers`; `each` / `done` after `flow.foreach` and `flow.repeat`.
  Starts have no inputs; ends have no outputs.
- `color`: red | amber | green | blue | purple (none = by kind). `icon`: one emoji.
- Groups are boxes; a node sits in at most one group (its innermost);
  `parent_id` nests a group in another.
- Node types and their settings: `list_workflow_nodes`. Common ones: `tool.call`
  `{name, arguments:{…}}`, `flow.wait {seconds}`, `flow.branch {mode, field, op,
  equals|contains}`, `flow.foreach {field}`, `pipeline.agent {ducky, prompt}`,
  `pipeline.finish {message}`, `workflow.call {workflow_id, args, share?}`,
  `flow.input {inputs:[{name, default}]}`, `flow.output {outputs:[{name, value}]}`,
  `start.cron {interval_seconds | cron}` (`cron` is 5-field local time, e.g.
  `0 8 * * *` every day at 8 AM), `uefn.*` play-test steps.
  Inputs/Return rows take `type` (a pin type, below) so the pins are typed.
- Values: `"{{field.path}}"` alone keeps the value's type; inside text it
  becomes text.

### Pins and data wires (Atlas / Blueprint style)

Nodes can have several typed inputs on the left and outputs on the right; the
card grows a row per pin, shows each value, and image nodes show their picture.
Two kinds of node:

- **Step nodes** (white run pins) run in order along `main`/`true`/`false` wires:
  starts, `tool.call`, `pipeline.*`, `logic.if`, `workflow.call`, `flow.*`, `uefn.*`.
- **Value nodes** (no run pins, `exec: false` in `list_workflow_nodes`) run when
  something needs what they make: every `input.*`, `logic.expression`,
  `logic.compare`, `text.template`, `llm.*`, `util.*`, `image.*`, `mesh.*`,
  `blender.*`, `uefn.import`, `list.*`, `pdf.*`. A value node nothing pulls from
  (Preview, Send to UEFN, Save file) is an end: it runs on its own, pulling its
  whole chain. So a pipeline needs no start node at all.

A data edge: `{source, target, kind:"data", source_pin, target_pin}`. One wire
per input pin (a new one replaces the old); an output can feed many. Types:
`text number boolean json any image images audio video mesh pdf svg file`.
`any` and `json` take anything; text takes numbers and yes/no; `file` takes any
file kind; `images` also takes one `image`. A wrong type or unknown pin makes
`save_workflow` refuse with `wires: [...]` naming each problem. Pin ids are in
`list_workflow_nodes` (`inputs` / `outputs`); use those exact ids.

Unwired inputs use the value set in details: `config.inputs = {pin: value}`
(`{{field}}` works). Files travel as refs `{kind, path, name}` (generators add
`provider`, `task_id`, `remote`). Each step's outputs are also fields for later
steps (`{{nodes.<node_id>.<pin>}}`), and a run returns `node_outputs`.

| Node | Pins in → out | Settings |
| --- | --- | --- |
| `input.text` / `input.number` | → `text` / `number` | `value` |
| `input.boolean` / `input.json` | → `value` | `value` |
| `input.image / audio / video / mesh / pdf / svg / file` | → `image` / `audio` / … / `file` | `value`: the file picked |
| `input.images` | → `images` | `value`: files picked |
| `logic.if` (step) | `names` → `result`; run wires `true` / `false` | `expression` (the condition), `names` (default `["value"]`) |
| `logic.expression` | `names` (default `a`, `b`) → `result` | `expression` |
| `logic.compare` | `a`, `b` → `result` | `op`: `equals not_equals greater less contains starts matches empty` |
| `llm.ask` | `prompt`, `context` → `text` | `model` (any model in the app's picker; blank = default), `system` |
| `llm.vision` Ask about an image | `image` (one or many), `prompt` → `text` | `model` (needs vision), `system` |
| `llm.extract` Extract data | `text` → `data` + one output per field | `names` (the fields), `model`, `system` |
| `llm.translate` | `text`, `language` → `text` | `language`, `model` |
| `llm.pick` Find by description | `list`, `prompt` → `list`, `item`, `count` | `model` |
| `text.template` | `names` → `text` | `template` with `{{name}}` |
| `util.preview` | `value` → | shows text or the picture on the card |
| `util.save_file` | `file` (one or a list), `folder` → `file`, `path` | `folder`, `name`, `overwrite` |
| `workflow.call` (step) | its Inputs rows → its Return rows | `workflow_id`, `share` |
| `tool.call` (step) | → `result` (json), `text` | `name`, `arguments` |
| `pipeline.agent` (step) | `context` → `text`, `files` | `ducky`, `prompt` (`{{field}}` works); wired `context` is added under the prompt |
| `flow.repeat` Repeat until (step) | `until` → `attempt`, `passed`; run wires `each` / `done` | `max` (1-10, default 3), `expression` (when `until` isn't wired) |
| `fortnite.servers` (step) | → `up`, `status`; run wires `true` (up) / `false` | `wait_minutes` (keep checking while down, max 240), `every_seconds` |
| `notify.message` Message me (step) | `message` → `sent`, `chat_id` | `on_fail` (also message if the run fails before here), `chat` |

**Expressions** (`logic.if`, `logic.expression`, `list.filter`, `list.map`):
JavaScript-like and safe. Names are the node's input pins (`item` / `index` in
list nodes), then run fields. `+ - * / %`, `== != < <= > >=`, `&& || !`,
`a ? b : c`, `a.b`, `a[0]`, `"text"`, `[1, 2]`, methods `.length .includes()
.startsWith() .endsWith() .toLowerCase() .toUpperCase() .trim() .split()
.slice() .replace() .indexOf() .join() .keys()`, functions `len number text
bool round floor ceil abs min max contains matches json lower upper`. No
assignments, loops or calls out. Example: `score >= 10 && name.includes("duck")`.

### Images, 3D, characters (paid backends)

Generators call a plugin tool; pick it with `config.backend` (ids and costs are
in `list_workflow_nodes` → `backends`, with `available` and why not). **They only
spend with `config.spend: true`.** Never set `spend` yourself: first ask the user
with `ducky_ask_user` (yes/no, naming each paid node and its ~credits). Without
it the run stops at that node and says which switch to turn on. Templates ship
with spend off.

| Node | Pins in → out | Backends (credits, first = default) |
| --- | --- | --- |
| `image.generate` Text to Image | `prompt` → `image`, `files` | `gemini25flash` (5), `gemini31flash` (7), `gemini3pro` (10), `seedream` (10), `meshy_text_to_image` (5) |
| `image.edit` | `image`, `prompt` → `image` | `meshy_image_to_image` (5) |
| `image.remove_bg` / `image.upscale` | `image` → `image` | `studio3d` (5 / 20) |
| `mesh.generate` Text to 3D | `prompt` → `mesh`, `files` | `meshy_text_to_3d` (25), `tripo` (60), `tencent_rapid` (35), `tencent_pro` (80), `tripo_p1` (100) |
| `mesh.from_image` Image to 3D | `image`, `prompt` (texture hint) → `mesh` | `meshy_image_to_3d` (25), `trellis` (30), `tripo`, `tencent_rapid`, `tencent_pro`; `fallback: true` tries the next |
| `mesh.multi_view` | `images` (2–4 views) → `mesh` | `meshy_multi_image_to_3d` (25) |
| `mesh.retexture` | `mesh`, `prompt` and/or `style_image` → `mesh` | `meshy_retexture` (10) |
| `mesh.remesh` Optimize | `mesh`, `polycount` → `mesh` | `meshy_remesh` (5), `studio3d_optimize` (10); `topology` |
| `mesh.uv_unwrap` | `mesh` → `mesh` | `meshy_uv_unwrap` (5) |
| `mesh.convert` | `mesh` → `mesh` in `format` | `meshy_convert` (1: fbx glb obj usdz stl), `studio3d_convert` (10: fbx obj stl ply) |
| `mesh.repair` / `mesh.render` / `mesh.bake` (`high`, `low`) | → `mesh` / `image` / `mesh` | `studio3d` (75 / 15 / 5) |
| `mesh.rig` Rig Humanoid | `mesh`, `height` (m) → `mesh` (walk and run included) | `meshy_rig` (5) |
| `mesh.animate` | `mesh` (from Rig) , `actions` → `mesh`, `meshes` | `meshy_animate` (3 each); `actions` "11, 28, 59" (Idle 1, Big Wave Hello, Victory Cheer; `meshy_list_animations` lists ids; 1–10 of them) |

Meshy edits reuse the Meshy task that made the model; 3D AI Studio tools need a
model a generator made (it has a web link), not a file only on this PC.

Free, on this PC: `image.resize` (`width`, `height`, `mode` fit/fill/stretch),
`image.crop` (`x y width height`), `image.convert` (`format` png/jpg/webp),
`image.split_alpha` → `color`, `alpha`; `image.combine_alpha`;
`image.split_channels` → `red green blue alpha`; `image.combine_channels`;
`image.concat` (`images` → `image`, `direction` row/column/grid);
`image.text` (`text` → `image`). GLB only: `mesh.info` → `width height depth
triangles info`; `mesh.fit_box` (`width height depth`, `stretch`);
`mesh.set_origin` (`x y z`: min/center/max/mass/keep, Y is up);
`mesh.origin_text` (`instruction`, a model picks the origin); `mesh.auto_scale`
(`description`: a model sets its real-world height, origin at the bottom); `mesh.rotate`
(`x y z` degrees); `mesh.textures_extract` → `base_color roughness metallic
normal occlusion emissive`; `mesh.textures_apply` (those as inputs).
Lists: `list.make` (`names`), `list.get` (`index`, -1 = last), `list.count`,
`list.join` (`separator`), `list.filter` / `list.map` (`expression`).
Documents: `pdf.text` (`pages` like "1-3, 5") → `text`, `page_texts`;
`pdf.images` → `images`. Blender (plugin + its add-on open): `blender.open`,
`blender.render` (`mesh` → `image`), `blender.export` (scene → `mesh`).
`uefn.import` Send to UEFN: `file` (one or a list), `folder` → `asset`,
`assets` (UEFN must be running).

`run_workflow_node(workflow_id, node_id)` runs one node now, reusing what fed it
last run (no paid repeats): use it to retry or tune one step.

Example: a pipeline with no start node: Prompt → picture → cut-out → 3D → UEFN:

```json
{"nodes": [
  {"id": "q", "type": "input.text", "x": 0, "y": 0, "config": {"value": "A wooden barrel, stylized, plain background"}},
  {"id": "gen", "type": "image.generate", "x": 300, "y": 0, "config": {"backend": "gemini25flash"}},
  {"id": "cut", "type": "image.remove_bg", "x": 600, "y": 0, "config": {}},
  {"id": "m", "type": "mesh.from_image", "x": 900, "y": 0, "config": {"backend": "meshy_image_to_3d", "fallback": true}},
  {"id": "u", "type": "uefn.import", "x": 1200, "y": 0, "config": {"folder": "Ducky/Props"}}],
 "edges": [
  {"source": "q", "target": "gen", "kind": "data", "source_pin": "text", "target_pin": "prompt"},
  {"source": "gen", "target": "cut", "kind": "data", "source_pin": "image", "target_pin": "image"},
  {"source": "cut", "target": "m", "kind": "data", "source_pin": "image", "target_pin": "image"},
  {"source": "m", "target": "u", "kind": "data", "source_pin": "mesh", "target_pin": "file"}]}
```

Ready-made: `list_workflow_templates` (each has `category`, `requires_plugins`,
`missing_plugins`). Shelves: Images, 3D, Characters (prompt / picture →
character → rig → animations → UEFN), Text & AI, Documents, Play tests, UEFN.
Start from one by copying its `graph` into `save_workflow`.

### Locks

`locked: true` on a node or group — or being inside a locked group — means the
user froze it: no moving, rewiring, editing, regrouping or deleting. Keep locked
items exactly as they are. `save_workflow` refuses a save that changes one and
names them; ask the user, and only after they agree pass
`allow_locked_changes=true` (or unlock it: drop `locked` with their OK).

### Tools

| Tool | Use |
| --- | --- |
| `list_workflows` | every workflow (id, name, on/off, folder, what starts it) + owners |
| `get_workflow` | one workflow's graph, owner and last run log |
| `list_workflow_nodes` | node types, their settings and plugin nodes |
| `save_workflow` | create, or update by `workflow_id` (left-out graph/name/description/enabled stay) |
| `show_workflow` | point at nodes or a group with a caption; nothing saved |
| `run_workflow` | run now (prompt, files, payload for a reusable one) |
| `run_workflow_node` | run one node now, reusing what fed it last run |
| `stop_workflow` | stop every run of it on this PC right away |
| `set_workflow_folder` / `move_workflow_folder` | file a workflow / rename, move or remove a folder |
| `copy_workflow` | copy or move to Local or a team |
| `add_workflow_folder` | make a folder that holds nothing yet (a team's syncs to members) |
| `copy_workflow_folder` | copy or move a whole folder tree to Local or a team; Run workflow steps inside it keep working |
| `export_workflow_folder` / `import_workflow_bundle` | a folder tree as one bundle / make a copy of a bundle anywhere (new ids, calls re-pointed) |
| `delete_workflow` | delete (for everyone, if it is a team's) |
| `list_workflow_versions` / `restore_workflow_version` | History: saved versions, bring one back |
| `clear_workflow_runs` | Clear log |
| `emit_workflow_trigger` | fire a plugin trigger to test listeners |
| `list_workflow_templates` / `save_workflow_template` (with `category`) / `delete_workflow_template` | New workflow picker |
| `get_workflow_node_code` / `edit_workflow_node_code` / `test_workflow_node` / `workflow_code_api` | a node's JavaScript (Custom code, below) |

### Code nodes (Custom code)

Every node's details have a Code tab with the JavaScript it runs. Built-ins keep
running their own step; editing a node's code turns it into a **Custom code** node
(`code.js`) in that workflow only. Local workflows only (a team's refuses it).

- **When**: only when no node or setting does it — a small transform, a few tool
  calls in a row, a check with its own message. A Custom code node is one step: it
  never picks a route (keep If / Branch / loops as nodes).
- **API**: call `workflow_code_api` (with `tools=[...]` for the tools you will call)
  instead of writing it from memory: the `ducky` object, the `node` declaration
  (pins, settings, `tools`, `builtins` it may use) and examples.
- **Later steps** read its outputs as `{{nodes.<id>.<pin>}}` or by wiring its pins;
  its result never lands in the run's own fields.
- **Order**: `get_workflow_node_code` → `edit_workflow_node_code` with `edits`
  (`[{old, new}]`, each matching once) and `expected_sha` = the `code_sha` you read →
  `test_workflow_node` (dry run: tool calls are listed, not made) → tell the user the
  node needs their Review before it runs for real. `revert=true` brings back the
  built-in it came from. Wires to pins that are gone are dropped and listed in
  `wires_dropped`; a value node (`kind: "value"`) loses its white wires.
- Code you save doesn't run for real (`dry_run=false`, `run_workflow_node`,
  `run_workflow`, a schedule) until the user presses Review on the node or runs it
  once, unless this chat allows everything. Until then those calls come back with
  `needs_review: true` and a `hint` naming the node: pass it on to the user and stop
  retrying. Locked nodes are refused like `save_workflow`.
- `get_workflow` leaves code out (`code_sha`, `code_lines`); saving that graph back
  keeps each node's code while its `code_sha` matches.

### Showing the user (they watch it happen)

- Every `save_workflow` opens the editor, glides to the nodes it added or
  changed and lights them up. Build in a few saves (start + first steps, then
  the rest) so they can follow along.
- To point at one part, `show_workflow(workflow_id, node_ids=[…] | group_id=…,
  title="Fortnite servers up?", body="what it does")`: the nodes are highlighted
  with a popup above them (only its close button closes it) and the chat keeps a
  **Show me** button. Without `title` it only glides there with `note` as a caption.
- To walk a whole workflow: `tour_workflow(workflow_id, auto=true)` (built from the
  graph in run order) or `tour_workflow(workflow_id, steps=[{node_ids, title,
  body}, …])`. Next / Back; the chat keeps it with Replay.
- Anything else on screen: `ducky_ui_show` (see `skill_read_subskill("ducky",
  "guided_ui")`). Pins, wires and fields have ids too:
  `workflows.pin.<node>.<in|out>.<pin>`, `workflows.wire.<from>><to>:<route>`,
  `workflows.details.field.<setting id>`, `workflows.log.step.<node>`.
- For a Next/Back tour of the editor itself use `ducky_walkthrough_run` with
  `navigate: "workflows"` and these targets (`ducky_ui_list_targets("workflows")`
  lists what is on screen): `workflows.node.<id>`, `workflows.group.<id>`,
  `workflows.list`, `workflows.list.row.<id>`,
  `workflows.toolbar.{name,delete,undo,redo,history,move,duplicate,save,onoff,run}`,
  `workflows.canvas`, `workflows.tool.{select,hand}`, `workflows.log`,
  `workflows.add`, `workflows.fit`, `workflows.zoom`, `workflows.outline`,
  `workflows.details`,
  `workflows.details.{edit,lock,delete,close}`. A node or group target glides
  into view by itself. Open the workflow first (`show_workflow`).

### Loops, schedules and reports

- **Repeat until** runs its `each` wire, then checks `until` (wire a yes/no in,
  e.g. an If's `result`) or its `expression`; again until yes or `max` tries,
  then `done` with `passed` and `attempt` (the try count). Steps in the loop
  start fresh each try; `{{attempt}}` is the try number; `{{nodes.<id>.<pin>}}`
  keeps the latest value after the loop. A try whose step fails counts as not
  passed and the next try starts; Stop ends the run. An Agent in a loop stays
  the same ducky every try, so it remembers what it did.
- **Test → fix → test again:** `flow.repeat` → `each` → tester `pipeline.agent`
  (prompt ends "last line RESULT: PASS or RESULT: FAIL") → `logic.if`
  `report.includes("RESULT: PASS")` with the tester's `text` wired into
  `report` → `false` → fixer `pipeline.agent` (tester `text` → `context`) →
  `uefn.game.stop` → `uefn.game.start`; wire the If's `result` → `until`.
- **Message me** posts into the chat that ran the workflow, else into the
  "Workflow reports" chat (the phone panel shows it too). Build the report with
  `text.template` and wire its `text` → `message`; set `on_fail` on the last one
  so a step that breaks is reported too.
- **Scheduled runs** (`start.cron`) go in the background while UEFN Ducky is
  open; one still running isn't started again. Templates start switched on.
- **Daily island check** (UEFN plugin template, Play tests shelf): 8 AM →
  `fortnite.servers` → open UEFN → start the session → test/fix loop (3 tries)
  → private version + memory calculation → report. Make it with
  `create_workflow_from_template("plugin:uefn:daily-island-check")`.

### Recipes

- **Build:** `list_workflow_nodes` → `save_workflow` (start → steps → end, wired
  with `main`) → `run_workflow` → read the log in the result (or `get_workflow`)
  → fix and save again with the same `workflow_id`.
- **Change one part:** `get_workflow`, edit only those nodes in the graph you got
  back, save it whole with `workflow_id`. Leave locked items alone.
- **Reuse steps:** put them in their own workflow `flow.input → … → flow.output`
  (give the rows a `type`) and call it with `workflow.call`: its inputs and
  returns become that node's pins. Or have the user group them and press
  Make reusable.
- **Pipeline of values:** `input.*` → value nodes → an end (Preview, Send to
  UEFN, Save file), wired by pins; read `node_outputs` in the run result to check
  each pin. Ask before turning on `spend` for paid nodes.
- **Organize:** `set_workflow_folder(id, "Play tests/Tycoon")`; groups, colors
  and icons on nodes make big graphs readable. A set of workflows that call each other
  lives in one folder; share it with `copy_workflow_folder` (not one
  `copy_workflow` per workflow), so the tree and its calls arrive whole.
