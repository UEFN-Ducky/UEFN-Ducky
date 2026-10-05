"""Builtin + enabled-plugin workflow node catalog (one palette for every workflow).

Nodes declare typed ``inputs`` / ``outputs`` pins (see ``pins.py``); ``exec: False``
marks a data node (no white pins: it runs when a value it makes is needed).
"""

from __future__ import annotations

import hashlib
from typing import Any

from backend.automations.code_api import BLANK_CODE, BLANK_PINS, BLANK_USES
from backend.automations.node_library import LIBRARY
from backend.automations.pins import clean_pins


def _file_input(ntype: str, label: str, pin_type: str, description: str, accept: str, *, many: bool = False) -> dict[str, Any]:
    pin = "images" if pin_type == "images" else pin_type
    return {
        "type": ntype,
        "label": label,
        "group": "Inputs",
        "role": "input",
        "exec": False,
        "description": description,
        "outputs": [{"id": pin, "label": label.removeprefix("Input ").strip() or pin, "type": pin_type}],
        "config_fields": [{"id": "value", "label": "Files" if many else "File", "type": "files" if many else "file", "accept": accept}],
    }


# Data nodes: inputs you type or pick, logic, text and models (plan P1).
DATA_NODES: list[dict[str, Any]] = [
    {
        "type": "input.text", "label": "Input Text", "group": "Inputs", "role": "input", "exec": False,
        "description": "Text you type here, passed to whatever it is wired to.",
        "outputs": [{"id": "text", "label": "Text", "type": "text"}],
        "config_fields": [{"id": "value", "label": "Text", "type": "textarea"}],
    },
    {
        "type": "input.number", "label": "Input Number", "group": "Inputs", "role": "input", "exec": False,
        "description": "A number you set here.",
        "outputs": [{"id": "number", "label": "Number", "type": "number"}],
        "config_fields": [{"id": "value", "label": "Number", "type": "number"}],
    },
    {
        "type": "input.boolean", "label": "Input Yes/No", "group": "Inputs", "role": "input", "exec": False,
        "description": "Yes or no, set here.",
        "outputs": [{"id": "value", "label": "Yes/No", "type": "boolean"}],
        "config_fields": [{"id": "value", "label": "Value", "type": "boolean"}],
    },
    {
        "type": "input.json", "label": "Input JSON", "group": "Inputs", "role": "input", "exec": False,
        "description": "Structured data (a JSON object or list) written here.",
        "outputs": [{"id": "value", "label": "Data", "type": "json"}],
        "config_fields": [{"id": "value", "label": "JSON", "type": "textarea"}],
    },
    _file_input("input.image", "Input Image", "image", "An image file you pick on this PC.", "image"),
    _file_input("input.images", "Input Images", "images", "Several image files, passed on as a list.", "image", many=True),
    _file_input("input.audio", "Input Audio", "audio", "An audio file you pick on this PC.", "audio"),
    _file_input("input.video", "Input Video", "video", "A video file you pick on this PC.", "video"),
    _file_input("input.mesh", "Input 3D Model", "mesh", "A 3D model (FBX, GLB, OBJ, USD…) you pick on this PC.", "mesh"),
    _file_input("input.pdf", "Input PDF", "pdf", "A PDF document you pick on this PC.", "pdf"),
    _file_input("input.svg", "Input SVG", "svg", "An SVG vector file you pick on this PC.", "svg"),
    _file_input("input.file", "Input File", "file", "Any file you pick on this PC.", "any"),
    {
        "type": "logic.if", "label": "If", "group": "Logic", "role": "logic",
        "description": "Checks a condition you write, like score > 10 && name.includes(\"duck\"), on the values wired in. True and False lead different ways.",
        "outputs": [{"id": "result", "label": "Result", "type": "boolean"}],
        "config_fields": [
            {"id": "expression", "label": "Condition", "type": "expression"},
            {"id": "names", "label": "Inputs", "type": "names"},
        ],
    },
    {
        "type": "logic.expression", "label": "Expression", "group": "Logic", "role": "logic", "exec": False,
        "description": "Works out a value from the values wired in: maths, text, picking from lists. JS-like, e.g. a * 2 or name.toUpperCase().",
        "outputs": [{"id": "result", "label": "Result", "type": "any"}],
        "config_fields": [
            {"id": "expression", "label": "Expression", "type": "expression"},
            {"id": "names", "label": "Inputs", "type": "names"},
        ],
    },
    {
        "type": "logic.compare", "label": "Compare", "group": "Logic", "role": "logic", "exec": False,
        "description": "Yes or no from comparing two values, without writing code.",
        "inputs": [{"id": "a", "label": "A", "type": "any"}, {"id": "b", "label": "B", "type": "any"}],
        "outputs": [{"id": "result", "label": "Result", "type": "boolean"}],
        "config_fields": [{"id": "op", "label": "Compare", "type": "select", "options": [
            {"id": "equals", "label": "A equals B"}, {"id": "not_equals", "label": "A is not B"},
            {"id": "greater", "label": "A is greater than B"}, {"id": "less", "label": "A is less than B"},
            {"id": "contains", "label": "A contains B"}, {"id": "starts", "label": "A starts with B"},
            {"id": "matches", "label": "A matches the pattern B"}, {"id": "empty", "label": "A is empty"},
        ]}],
    },
    {
        "type": "llm.ask", "label": "Ask a model", "group": "Text & AI", "role": "agent", "exec": False,
        "description": "Sends the prompt (and any context) to the model you pick and passes its answer on.",
        "inputs": [
            {"id": "prompt", "label": "Prompt", "type": "text", "required": True},
            {"id": "context", "label": "Context", "type": "text"},
        ],
        "outputs": [{"id": "text", "label": "Answer", "type": "text"}],
        "config_fields": [
            {"id": "model", "label": "Model", "type": "model"},
            {"id": "system", "label": "Instructions", "type": "textarea"},
        ],
    },
    {
        "type": "text.template", "label": "Text template", "group": "Text & AI", "role": "logic", "exec": False,
        "description": "Joins the values wired in into one text: Hello {{a}}, you scored {{b}}.",
        "outputs": [{"id": "text", "label": "Text", "type": "text"}],
        "config_fields": [
            {"id": "template", "label": "Template", "type": "textarea"},
            {"id": "names", "label": "Inputs", "type": "names"},
        ],
    },
    {
        "type": "util.preview", "label": "Preview", "group": "Utility", "role": "end", "exec": False,
        "description": "Shows whatever is wired in on its card after a run: text, numbers, images, files.",
        "inputs": [{"id": "value", "label": "Value", "type": "any"}],
        "config_fields": [],
    },
]


BUILTIN_NODES: list[dict[str, Any]] = [
    {
        "type": "start.manual",
        "label": "Manual",
        "group": "Starting",
        "role": "starter",
        "description": "Run from the editor Test button or run_workflow.",
        "config_fields": [],
    },
    {
        "type": "start.cron",
        "label": "Cron / interval",
        "group": "Starting",
        "role": "starter",
        "description": "Fires while the panel is running. interval_seconds or 5-field cron.",
        "config_fields": [
            {"id": "interval_seconds", "label": "Interval (seconds)", "type": "number"},
            {"id": "cron", "label": "Cron (m h dom mon dow)", "type": "string"},
        ],
    },
    {
        "type": "start.chat",
        "label": "Chat input",
        "group": "Starting",
        "role": "starter",
        "description": "Pass the text and files sent with this workflow's chat reference into the next step. Optional: any unconnected input can start a workflow.",
        "config_fields": [],
    },
    {
        "type": "flow.input",
        "label": "Inputs",
        "group": "Functions",
        "role": "starter",
        "description": "Makes this workflow reusable. Lists the values a Run workflow node passes in; Test uses each default.",
        "config_fields": [{"id": "inputs", "label": "Inputs", "type": "params"}],
    },
    {
        "type": "flow.output",
        "label": "Return",
        "group": "Functions",
        "role": "end",
        "description": "End this path and hand values back to the workflow that ran this one. A blank value returns the field with the same name.",
        "config_fields": [{"id": "outputs", "label": "Return values", "type": "returns"}],
    },
    {
        "type": "workflow.call",
        "label": "Run workflow",
        "group": "Functions",
        "role": "action",
        "description": "Run another workflow like a function: pass its inputs, wait for it, then use what it returns in the next steps. If it has Return nodes and none is reached, this path stops.",
        "config_fields": [{"id": "workflow_id", "label": "Workflow", "type": "workflow"}],
    },
    {
        "type": "ducky.prompt",
        "label": "Prompt existing ducky",
        "group": "Duckies",
        "role": "action",
        "description": "Send a prompt to a chat id. Does not switch island.",
        "config_fields": [
            {"id": "conv_id", "label": "Chat id", "type": "string"},
            {"id": "prompt", "label": "Prompt", "type": "textarea"},
            {"id": "mode", "label": "Mode", "type": "string"},
            {"id": "model", "label": "Model", "type": "string"},
        ],
    },
    {
        "type": "ducky.spawn",
        "label": "Spawn ducky",
        "group": "Duckies",
        "role": "action",
        "description": "Create a chat on the current island and send a prompt.",
        "config_fields": [
            {"id": "title", "label": "Title", "type": "string"},
            {"id": "prompt", "label": "Prompt", "type": "textarea"},
            {"id": "ducky_style", "label": "Ducky style", "type": "string"},
            {"id": "mode", "label": "Mode", "type": "string"},
            {"id": "model", "label": "Model", "type": "string"},
        ],
    },
    {
        "type": "pipeline.agent",
        "label": "Agent",
        "group": "Agents",
        "role": "action",
        "description": "Assign a ducky or create one when this workflow runs. Wait for its result and pass files to the next step. Run again in a loop, it is the same ducky, so it remembers the last pass.",
        "inputs": [{"id": "context", "label": "Context", "type": "text", "description": "Text wired in (an earlier step's report) is added under the instructions."}],
        "outputs": [{"id": "text", "label": "Reply", "type": "text"}, {"id": "files", "label": "Files", "type": "any"}],
        "config_fields": [
            {"id": "ducky", "label": "Assign ducky", "type": "ducky"},
            {"id": "prompt", "label": "Instructions (optional; uses the workflow request)", "type": "textarea"},
        ],
    },
    {
        "type": "pipeline.finish",
        "label": "Return to user",
        "group": "End",
        "role": "end",
        "description": "End this path and send its result and files back to the user. In a test run, show the result in the run log.",
        "config_fields": [
            {"id": "message", "label": "Message (optional)", "type": "textarea"},
        ],
    },
    {
        "type": "spotlight.step",
        "label": "Spotlight step",
        "group": "Starting",
        "role": "starter",
        "description": "Runs when a desktop spotlight reaches a step (Next, or a click in the hole). Payload: step, total, clicked, window.",
        "config_fields": [
            {"id": "window", "label": "Only this window title (optional, exact)", "type": "string"},
        ],
    },
    {
        "type": "spotlight.closed",
        "label": "Spotlight closed",
        "group": "Starting",
        "role": "starter",
        "description": "Runs when a desktop spotlight ends. Payload: reason (done, close, esc), step, total, window.",
        "config_fields": [
            {"id": "window", "label": "Only this window title (optional, exact)", "type": "string"},
            {"id": "reason", "label": "Only this reason (done, close, esc)", "type": "string"},
        ],
    },
    {
        "type": "ui.spotlight",
        "label": "Spotlight",
        "group": "Utility",
        "role": "action",
        "description": (
            "Darkens the desktop and highlights one control in UEFN or another program. "
            "x, y, w, h are fractions of that window (0 to 1). Blocks every click except the hole. "
            "Waits until they finish unless Wait is off."
        ),
        "inputs": [
            {"id": "title", "label": "Title", "type": "text"},
            {"id": "body", "label": "Explanation", "type": "text"},
        ],
        "outputs": [
            {"id": "reason", "label": "Reason", "type": "text"},
            {"id": "step", "label": "Step", "type": "number"},
        ],
        "config_fields": [
            {"id": "window", "label": "Window (uefn, a title, or hwnd)", "type": "string"},
            {"id": "x", "label": "X (0 to 1)", "type": "number"},
            {"id": "y", "label": "Y (0 to 1)", "type": "number"},
            {"id": "w", "label": "Width (0 to 1)", "type": "number"},
            {"id": "h", "label": "Height (0 to 1)", "type": "number"},
            {"id": "title", "label": "Title", "type": "string"},
            {"id": "body", "label": "Explanation", "type": "textarea"},
            {"id": "click", "label": "Continue only when they click the highlight", "type": "boolean"},
            {"id": "wait", "label": "Wait until they close it", "type": "boolean"},
            {"id": "steps", "label": "Steps JSON (optional; replaces x/y/w/h)", "type": "textarea"},
        ],
    },
    {
        "type": "notify.message",
        "label": "Message me",
        "group": "Utility",
        "role": "action",
        "description": (
            "Posts a message to you: into the chat that ran the workflow, else into the Workflow reports chat "
            "(also on your phone). Wire a report in, or type it with {{placeholders}}."
        ),
        "inputs": [{"id": "message", "label": "Message", "type": "text", "required": True}],
        "outputs": [{"id": "sent", "label": "Sent", "type": "boolean"}, {"id": "chat_id", "label": "Chat", "type": "text"}],
        "config_fields": [
            {"id": "on_fail", "label": "Also if the run fails before here (says which step and why)", "type": "boolean"},
            {"id": "chat", "label": "Reports chat name (default Workflow reports)", "type": "string"},
        ],
    },
    {
        "type": "flow.end",
        "label": "End workflow",
        "group": "End",
        "role": "end",
        "description": "End this path without posting a reply.",
        "config_fields": [],
    },
    {
        "type": "flow.wait",
        "label": "Wait",
        "group": "Logic",
        "role": "action",
        "description": "Pause this run (capped at 120s).",
        "config_fields": [{"id": "seconds", "label": "Seconds", "type": "number"}],
    },
    {
        "type": "flow.foreach",
        "label": "For each",
        "group": "Logic",
        "role": "action",
        "description": "Run the each-wire once per item in a payload list, then follow done.",
        "config_fields": [{"id": "field", "label": "List field", "type": "string"}],
    },
    {
        "type": "flow.repeat",
        "label": "Repeat until",
        "group": "Logic",
        "role": "action",
        "description": (
            "Runs the Each try wire, then checks Until (wire a yes/no in, or write a condition). "
            "Goes again until it says yes or Max tries run out, then follows Done with Passed and the number of tries. "
            "Steps in the loop start fresh each try; an Agent in it stays the same ducky."
        ),
        "inputs": [{"id": "until", "label": "Until", "type": "boolean", "description": "Yes ends the loop. Checked after each try."}],
        "outputs": [{"id": "attempt", "label": "Try", "type": "number"}, {"id": "passed", "label": "Passed", "type": "boolean"}],
        "config_fields": [
            {"id": "max", "label": "Max tries (1-10)", "type": "number"},
            {"id": "expression", "label": "Until condition (when Until isn't wired)", "type": "expression"},
        ],
    },
    {
        "type": "flow.branch",
        "label": "Branch",
        "group": "Logic",
        "role": "action",
        "description": "True/false edges. Data: field equals / contains / exists. Agent: assigned profile decides.",
        "config_fields": [
            {"id": "mode", "label": "Mode (data or agent)", "type": "string"},
            {"id": "field", "label": "Payload field", "type": "string"},
            {"id": "op", "label": "Op (equals/contains/exists)", "type": "string"},
            {"id": "equals", "label": "Equals (optional)", "type": "string"},
            {"id": "contains", "label": "Contains (optional)", "type": "string"},
            {"id": "ducky", "label": "Judge ducky (agent mode)", "type": "ducky"},
            {"id": "prompt", "label": "Judge prompt (agent mode)", "type": "textarea"},
        ],
    },
    {
        "type": "tool.call",
        "label": "Call tool",
        "group": "Tools",
        "role": "action",
        "description": "Call a named host or plugin MCP tool.",
        "outputs": [{"id": "result", "label": "Result", "type": "json"}, {"id": "text", "label": "Text", "type": "text"}],
        "config_fields": [
            {"id": "name", "label": "Tool name", "type": "string"},
            {"id": "arguments_json", "label": "Arguments JSON", "type": "textarea"},
        ],
    },
    {
        "type": "code.js",
        "label": "Custom code",
        "group": "Code",
        "role": "action",
        "description": (
            "Runs the JavaScript in its Code tab: the pins, settings, tools and nodes it uses are declared at the top. "
            "Local workflows only; code an agent changed runs after you review it or run it once yourself."
        ),
        # Its pins live in config.pins (written from the code on save); a new one starts as the blank template.
        "config_fields": [],
        "default_config": {"code": BLANK_CODE, "code_sha": hashlib.sha256(BLANK_CODE.encode("utf-8")).hexdigest(), "pins": BLANK_PINS, "uses": BLANK_USES, "settings_spec": [], "problems": [],
                           "settings": {}, "inputs": {}, "spend": False},
    },
    {
        "type": "uefn.open_project",
        "label": "Open UEFN project",
        "group": "UEFN",
        "role": "action",
        "description": "Start UEFN if closed, select this workspace and wait for the chosen island before continuing.",
        "config_fields": [
            {"id": "project", "label": "UEFN project", "type": "project"},
            {"id": "timeout", "label": "Ready timeout (seconds, max 300)", "type": "number"},
        ],
    },
    {
        "type": "uefn.launch",
        "label": "Launch UEFN",
        "group": "UEFN",
        "role": "action",
        "description": "Start UEFN from closed and select this workspace. Follow with Wait for UEFN before editor actions.",
        "config_fields": [
            {"id": "project", "label": "UEFN project", "type": "project"},
        ],
    },
    {
        "type": "uefn.close",
        "label": "Close UEFN",
        "group": "UEFN",
        "role": "action",
        "description": "WM_CLOSE, press Save, taskkill only if UEFN is still up.",
        "config_fields": [],
    },
    {
        "type": "uefn.restart",
        "label": "Restart UEFN",
        "group": "UEFN",
        "role": "action",
        "description": "Close UEFN, reopen the project, wait until the listener matches.",
        "config_fields": [
            {"id": "project", "label": "UEFN project", "type": "project"},
            {"id": "timeout", "label": "Wait timeout (seconds)", "type": "number"},
        ],
    },
    {
        "type": "uefn.wait_ready",
        "label": "Wait for UEFN",
        "group": "UEFN",
        "role": "action",
        "description": "Poll until the listener is online and the open island matches (max 300s).",
        "config_fields": [
            {"id": "project", "label": "UEFN project", "type": "project"},
            {"id": "timeout", "label": "Timeout (seconds)", "type": "number"},
        ],
    },
    {
        "type": "uefn.wait_window",
        "label": "Wait for UEFN window",
        "group": "UEFN",
        "role": "action",
        "description": "Wait until a UEFN window title matches (max 300s).",
        "config_fields": [
            {"id": "title_regex", "label": "Title regex", "type": "string"},
            {"id": "timeout", "label": "Timeout (seconds)", "type": "number"},
        ],
    },
    {
        "type": "uefn.check",
        "label": "Check UEFN",
        "group": "Play test",
        "role": "action",
        "description": "Cheap status, never launches: running, listener_online, project_match, playing, has_player, ready. Branch on any of them.",
        "config_fields": [
            {"id": "project", "label": "UEFN project (optional)", "type": "project"},
        ],
    },
    {
        "type": "fortnite.servers",
        "label": "Fortnite servers up?",
        "group": "Play test",
        "role": "action",
        "description": (
            "Checks Epic's status page (Login, Game Services, Matchmaking). Down: checks again every minute "
            "for up to the wait you set, then True (up) or False (still down) leads the way."
        ),
        "outputs": [{"id": "up", "label": "Up", "type": "boolean"}, {"id": "status", "label": "Status", "type": "text"}],
        "config_fields": [
            {"id": "wait_minutes", "label": "Wait while down (minutes, max 240)", "type": "number"},
            {"id": "every_seconds", "label": "Check every (seconds)", "type": "number"},
        ],
    },
    {
        "type": "uefn.game.start",
        "label": "Start game",
        "group": "Play test",
        "role": "action",
        "description": "Start the play session (Epic StartGame, else the listener). Skips when a session is already playing. Needs UEFN online.",
        "config_fields": [
            {"id": "skip_if_playing", "label": "Skip if already playing", "type": "boolean"},
            {"id": "wait_player", "label": "Then wait for a player (seconds, 0 = no wait, max 120)", "type": "number"},
        ],
    },
    {
        "type": "uefn.game.stop",
        "label": "Stop game",
        "group": "Play test",
        "role": "action",
        "description": "End the play session. No-op when nothing is playing or UEFN is closed.",
        "config_fields": [],
    },
    {
        "type": "uefn.player.wait",
        "label": "Wait for player",
        "group": "Play test",
        "role": "action",
        "description": "Poll the session until a player is in the game (max 120s).",
        "config_fields": [{"id": "timeout", "label": "Timeout (seconds)", "type": "number"}],
    },
    {
        "type": "uefn.log.expect",
        "label": "Expect log line",
        "group": "Play test",
        "role": "action",
        "description": "Wait for an editor log line matching a regex (max 120s). Continues from the previous Expect log line's offset.",
        "config_fields": [
            {"id": "regex", "label": "Regex", "type": "string"},
            {"id": "timeout", "label": "Timeout (seconds)", "type": "number"},
            {"id": "from_start", "label": "Search from the start of the log", "type": "boolean"},
        ],
    },
]


def _contributions() -> tuple[dict[str, Any], set[str]]:
    try:
        from backend.uefn_plugins.host import get_ui_contributions
        from backend.uefn_plugins.store import get_enabled_plugin_ids

        return get_ui_contributions(), set(get_enabled_plugin_ids())
    except Exception:
        return {}, set()


def node_specs() -> dict[str, dict[str, Any]]:
    """Every node type the runner knows (no plugin artwork read): type → catalog row."""
    out = {n["type"]: n for n in BUILTIN_NODES + DATA_NODES + LIBRARY}
    contrib, enabled = _contributions()
    for key, role, group in (("automations_triggers", "starter", "Triggers"), ("automations_nodes", "action", "")):
        for row in contrib.get(key) or []:
            parsed = _plugin_node(row, enabled, role=role, default_group=group)
            if parsed:
                out.setdefault(parsed["type"], parsed)
    return out


def list_nodes() -> list[dict[str, Any]]:
    from backend.automations.media import BACKENDS, backends_for

    out = [dict(n) for n in BUILTIN_NODES + DATA_NODES + LIBRARY]
    for node in out:
        if node["type"] in BACKENDS:  # the details dropdown: each backend, its cost, ready or why not
            node["backends"] = backends_for(node["type"])
    contrib, enabled = _contributions()
    for row in contrib.get("automations_triggers") or []:
        parsed = _plugin_node(row, enabled, role="starter", default_group="Triggers")
        if parsed:
            out.append(parsed)
    for row in contrib.get("automations_nodes") or []:
        parsed = _plugin_node(row, enabled, role="action", default_group="")
        if parsed:
            out.append(parsed)
    # Read each plugin's own artwork once, even when it contributes many nodes.
    icons: dict[str, str] = {}
    for node in out:
        pid = str(node.get("plugin_id") or "")
        if not pid:
            continue
        if pid not in icons:
            try:
                from backend.uefn_plugins.store import plugin_icon_data_url

                icons[pid] = plugin_icon_data_url(pid) or ""
            except Exception:
                icons[pid] = ""
        node["icon"] = icons[pid] or node.get("icon") or ""
    return out


def starter_types() -> set[str]:
    return {n["type"] for n in BUILTIN_NODES if n.get("role") == "starter"} | trigger_types()


def trigger_types() -> set[str]:
    """Node types of enabled plugin triggers (no icons read: the list badge uses this)."""
    contrib, enabled = _contributions()
    return {parsed["type"] for row in contrib.get("automations_triggers") or []
            if (parsed := _plugin_node(row, enabled, role="starter", default_group="Triggers"))}


def _plugin_node(
    row: Any,
    enabled: set[str],
    *,
    role: str,
    default_group: str,
) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    pid = str(row.get("plugin_id") or "")
    if pid and pid not in enabled:
        return None
    ntype = str(row.get("id") or row.get("type") or "").strip()
    if not ntype:
        return None
    return {
        "type": ntype,
        "label": str(row.get("label") or ntype),
        "group": str(row.get("group") or default_group or pid or "Plugins"),
        "role": role,
        "description": str(row.get("description") or ""),
        "plugin_id": pid,
        "icon": str(row.get("icon") or ""),
        "config_fields": _fields(row.get("config_fields") or row.get("fields")),
        **({"exec": False} if row.get("exec") is False else {}),
        **({"inputs": clean_pins(row.get("inputs"))} if row.get("inputs") else {}),
        **({"outputs": clean_pins(row.get("outputs"))} if row.get("outputs") else {}),
    }


def _fields(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for f in raw:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or "").strip()
        if not fid:
            continue
        row: dict[str, Any] = {
            "id": fid,
            "label": str(f.get("label") or fid),
            "type": str(f.get("type") or "string"),
        }
        if f.get("provider"):
            row["provider"] = str(f.get("provider") or "")
        if f.get("accept"):
            row["accept"] = str(f.get("accept") or "")
        opts = f.get("options")
        if isinstance(opts, list):
            clean: list[dict[str, str]] = []
            for opt in opts:
                if isinstance(opt, dict) and (opt.get("id") or opt.get("value")):
                    oid = str(opt.get("id") or opt.get("value") or "")
                    clean.append({"id": oid, "label": str(opt.get("label") or oid)})
                elif isinstance(opt, str) and opt.strip():
                    clean.append({"id": opt, "label": opt})
            if clean:
                row["options"] = clean
        out.append(row)
    return out
