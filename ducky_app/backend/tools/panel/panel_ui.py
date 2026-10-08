"""Guided-UI tools: navigate the panel, show one thing, run coachmark walkthroughs, ask the user.

Agents point at one part of the app with :func:`ducky_ui_show`: it takes the user there,
highlights it and explains it in a popup that only its close button closes, and the chat
keeps a Show me button that plays it again.

Agents teach the user with :func:`ducky_walkthrough_run` (Next / Back / Skip +
require_click) — the same product walkthrough overlay as first-run tours.

Agents clarify mid-task with :func:`ducky_ask_user` — a Cursor-style stacked
multi-choice questionnaire docked above the composer in that chat (not a modal)
that blocks until the user answers.

All work is delegated to the panel over loopback via :func:`backend.panel.rpc.panel_rpc`;
nothing here touches the UEFN listener, so every tool works while UEFN is offline.
When no panel window is open the tools return ``{"error": "panel not open"}``.
"""

from __future__ import annotations

from typing import Any

from backend.util.json_util import tool_json
from backend.panel.rpc import panel_rpc
from backend.server import mcp

# Routes ducky_ui_navigate accepts. Kept in sync with the React navigate handler.
_ROUTES = (
    "settings",
    "settings.store",
    "settings.general",
    "settings.llms",
    "settings.mcp",
    "settings.mcp_plugins",
    "settings.skills",
    "settings.appearance",
    "settings.audio",
    "settings.duckies",
    "settings.plans",
    "settings.memory",
    "settings.languages",
    "settings.log_errors",
    "settings.app_data",
    "settings.permissions",
    "settings.tab",
    "chat",
    "changes",
    "workflows",
    "skills_studio",
    "terminals",
    "plans",
    "project_picker",
    "files",
    "chats",
)

# User-paced UI budget (Skip / Got it / answers end earlier).
_MAX_WALKTHROUGH_WAIT_S = 300.0
# Show me answers once it is on screen (the view may need to open first).
_SHOW_WAIT_S = 30.0
# wait=true: until the user presses the popup's close button.
_SHOW_CLOSE_WAIT_S = 900.0
_MAX_SHOW_TARGETS = 8
_MAX_SHOW_STEPS = 12
_TARGET_KEYS = ("id", "role", "name", "text", "within", "nth")
# Asks NEVER time out: the agent suspends until the user answers (or Stop /
# panel close). A timed-out ask left the questionnaire on screen while the
# agent "proceeded anyway" and the eventual answer resolved into nothing.
_MAX_ASK_USER_WAIT_S = float("inf")
_MAX_ASK_USER_DETAIL_CHARS = 4000
_MAX_ASK_USER_QUESTIONS = 8


def _navigate(route: str, item_id: str = "") -> dict[str, Any]:
    return panel_rpc("navigate", {"route": route, "item_id": item_id})


def _list_targets(route: str = "", query: str = "", visible_only: bool = False) -> dict[str, Any]:
    return panel_rpc("list_targets", {"route": route, "query": query, "visible_only": visible_only})


def _clean_target(raw: Any) -> str | dict[str, Any] | None:
    """An id, or {role, name, within, text, nth} for things with no id."""
    if isinstance(raw, str):
        return raw.strip() or None
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key in _TARGET_KEYS:
        value = raw.get(key)
        if key == "within":
            inner = _clean_target(value)
            if inner:
                out[key] = inner
        elif key == "nth":
            if isinstance(value, int) and value >= 0:
                out[key] = value
        elif isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out if any(out.get(k) for k in ("id", "role", "name", "text")) else None


def _clean_action(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
        return None
    args = raw.get("args")
    return {"id": str(raw["id"]).strip(), "args": args if isinstance(args, dict) else {}}


def _normalize_walkthrough_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]] | dict[str, Any]:
    cleaned: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            return {"error": "each step must be an object"}
        target = _clean_target(step.get("target"))
        if not target:
            return {"error": "each step needs target"}
        tid = target if isinstance(target, str) else str(target.get("id") or target.get("name") or target.get("text") or "")
        title = str(step.get("title") or "").strip()
        body = str(step.get("body") or step.get("label") or "").strip()
        advance = (
            "require_click"
            if step.get("advance") == "require_click" or step.get("require_click")
            else "next"
        )
        mode = str(step.get("mode") or "rect").strip().lower()
        if mode not in ("circle", "rect"):
            mode = "rect"
        row: dict[str, Any] = {
            "target": target,
            "title": title or body[:48] or tid,
            "body": body or title or tid,
            "advance": advance,
            "mode": mode,
        }
        nav = str(step.get("navigate") or "").strip()
        if nav:
            row["navigate"] = nav
            item = str(step.get("item_id") or "").strip()
            if item:
                row["item_id"] = item
        action = _clean_action(step.get("action"))
        if action:
            row["action"] = action
        cleaned.append(row)
    if not cleaned:
        return {"error": "steps must be a non-empty list"}
    return cleaned


def _normalize_ask_user_questions(
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]] | dict[str, Any]:
    if not isinstance(questions, list) or not questions:
        return {"error": "questions must be a non-empty list"}
    if len(questions) > _MAX_ASK_USER_QUESTIONS:
        return {"error": f"at most {_MAX_ASK_USER_QUESTIONS} questions per call"}
    cleaned: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for question in questions:
        if not isinstance(question, dict):
            return {"error": "each question must be an object"}
        qid = str(question.get("id") or "").strip()
        prompt = str(question.get("prompt") or "").strip()
        if not qid:
            return {"error": "each question needs id"}
        if qid in seen_ids:
            return {"error": f"duplicate question id: {qid}"}
        if not prompt:
            return {"error": f"question {qid} needs prompt"}
        seen_ids.add(qid)
        options_raw = question.get("options")
        options: list[dict[str, str]] = []
        if options_raw is None:
            options_raw = []
        if not isinstance(options_raw, list):
            return {"error": f"question {qid}: options must be a list"}
        seen_opt: set[str] = set()
        for opt in options_raw:
            if not isinstance(opt, dict):
                return {"error": f"question {qid}: each option must be an object"}
            oid = str(opt.get("id") or "").strip()
            label = str(opt.get("label") or "").strip()
            if not oid or not label:
                return {"error": f"question {qid}: each option needs id and label"}
            if oid in seen_opt:
                return {"error": f"question {qid}: duplicate option id: {oid}"}
            seen_opt.add(oid)
            options.append(
                {
                    "id": oid,
                    "label": label,
                    "description": str(opt.get("description") or "").strip(),
                }
            )
        row: dict[str, Any] = {
            "id": qid,
            "prompt": prompt,
            "options": options,
            "allow_multiple": bool(question.get("allow_multiple")),
            "allow_free_text": bool(question.get("allow_free_text", True)),
            "required": bool(question.get("required", True)),
        }
        # Optional: verbatim text shown in a code block (an approval card's command)
        # and a caution line above the options.
        detail = str(question.get("detail") or "").rstrip()
        if detail:
            row["detail"] = detail[:_MAX_ASK_USER_DETAIL_CHARS]
        warning = str(question.get("warning") or "").strip()
        if warning:
            row["warning"] = warning[:500]
        cleaned.append(row)
    return cleaned


@mcp.tool()
def ducky_ui_navigate(route: str, item_id: str = "", pretty: bool = False) -> str:
    """Open a panel route so the user doesn't have to hunt for it.

    route: one of settings, settings.general, settings.store, settings.llms,
    settings.mcp_plugins, settings.skills, settings.appearance, settings.audio,
    settings.duckies, settings.plans, settings.memory, settings.languages,
    settings.log_errors, settings.app_data, settings.tab, chat, chats, files, changes,
    workflows, skills_studio, terminals, plans, project_picker. `changes` opens the
    project-wide ledger. `workflows` opens the Workflows editor.
    `item_id`: settings.store → a Store slug opens that plugin's page; settings.tab →
    any Settings tab by name (a plugin's own tab like "Meshy", or "Audio");
    settings.llms → a provider id; chat → a chat id; files → a project file path.
    Returns {ok, route}. Needs an open panel; UEFN may be offline.
    Example: ducky_ui_navigate("settings.mcp_plugins").
    """
    r = (route or "").strip()
    if r not in _ROUTES:
        return tool_json({"error": f"unknown route: {route}", "routes": list(_ROUTES)}, pretty=pretty)
    return tool_json(_navigate(r, (item_id or "").strip()), pretty=pretty)


@mcp.tool()
def ducky_ui_list_targets(route: str = "", query: str = "", visible_only: bool = False, pretty: bool = False) -> str:
    """List spotlightable panel controls with stable semantic ids, and the view's UI actions.

    Returns {targets:[{id,label,route,rect:{x,y,w,h},visible,enabled,kind}],
    actions:[{id,label,route}]}. kind: tab | button | input | toggle | dropdown | chat |
    settings_field | plugin_row | skill_row. `route` narrows to a view ("workflows",
    "settings"); `query` searches ids and labels ("test run"); visible_only=true keeps
    what is on screen now. Feed ids into ducky_ui_show / ducky_walkthrough_run, and
    actions into their `action`. Needs an open panel.
    """
    return tool_json(_list_targets((route or "").strip(), (query or "").strip(), bool(visible_only)), pretty=pretty)


def _show_step(raw: dict[str, Any]) -> dict[str, Any] | str:
    """One Show me step from flat fields (target/role/within/also/navigate/…), or an error.

    A ``box`` makes it a desktop spotlight on another program's window instead of
    a control inside the panel.
    """
    if raw.get("box") is not None:
        from frontend.ui_web.window_spotlight import clean_box

        box = clean_box(raw.get("box"))
        if not box:
            return "box needs x, y, w, h as fractions of the window (0 to 1)"
        title_s, body_s = str(raw.get("title") or "").strip(), str(raw.get("body") or "").strip()
        if not title_s and not body_s:
            return "say what it is: title (and body) are required"
        step: dict[str, Any] = {"box": box, "title": title_s or body_s[:60], "body": body_s}
        if raw.get("click") is True:
            step["click"] = True
        window = str(raw.get("window") or "").strip()
        if window:
            step["window"] = window
        return step
    name = str(raw.get("target") or "").strip() if not isinstance(raw.get("target"), dict) else ""
    kind = str(raw.get("role") or "").strip().lower()
    first: str | dict[str, Any] | None
    if isinstance(raw.get("target"), dict):
        first = _clean_target(raw.get("target"))
    elif kind == "text":
        first = {"text": name} if name else None
    elif kind:
        first = {"role": kind, "name": name} if name else None
    else:
        first = name or None
    within = str(raw.get("within") or "").strip()
    if isinstance(first, dict) and within and "within" not in first:
        first["within"] = within
    also = raw.get("also") if isinstance(raw.get("also"), list) else []
    targets = [t for t in [first, *[_clean_target(x) for x in also[: _MAX_SHOW_TARGETS - 1]]] if t]
    if not first or not targets:
        return "target is required: an id from ducky_ui_list_targets, or a name with role"
    title_s, body_s = str(raw.get("title") or "").strip(), str(raw.get("body") or "").strip()
    if not title_s and not body_s:
        return "say what it is: title (and body) are required"
    route = str(raw.get("navigate") or "").strip()
    if route and route not in _ROUTES:
        return f"unknown route: {route}"
    step = {
        "target": targets[0] if len(targets) == 1 else targets,
        "title": title_s or body_s[:60],
        "body": body_s,
    }
    workflow_id = str(raw.get("workflow_id") or "").strip()
    if workflow_id:
        step["workflow_id"] = workflow_id
    if route:
        step["navigate"] = route
        item_id = str(raw.get("item_id") or "").strip()
        if item_id:
            step["item_id"] = item_id
    action = raw.get("action")
    if isinstance(action, dict):
        cleaned = _clean_action(action)
        if cleaned:
            step["action"] = cleaned
    elif str(action or "").strip():
        args = raw.get("action_args")
        step["action"] = {"id": str(action).strip(), "args": args if isinstance(args, dict) else {}}
    if raw.get("click") is True:
        step["click"] = True
    return step


def _show_fields(
    *,
    target: str,
    title: str,
    body: str,
    workflow_id: str,
    navigate: str,
    item_id: str,
    action: str,
    action_args: dict[str, Any] | None,
    also: list[str] | None,
    role: str,
    within: str,
    window: str,
    box: dict[str, Any] | None,
    click: bool,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "target": target, "title": title, "body": body, "workflow_id": workflow_id,
        "navigate": navigate, "item_id": item_id, "action": action,
        "action_args": action_args, "also": also, "role": role, "within": within,
    }
    if box is not None:
        raw["box"] = box
        if window:
            raw["window"] = window
    if click:
        raw["click"] = True
    return raw


def _finish_show(out: dict[str, Any], count: int) -> dict[str, Any]:
    if not out.get("error"):
        return {**out, "ok": out.get("ok", True), "steps": count}
    return out


def _show_on_window(plan: list[dict[str, Any]], window: str, wait: bool) -> dict[str, Any]:
    """Desktop spotlight. ``plan`` steps all carry a fraction ``box``."""
    from frontend.ui_web.window_spotlight import prepare_window_show

    prepared = prepare_window_show(plan, (window or "uefn").strip() or "uefn")
    if prepared.get("error"):
        return {"error": str(prepared["error"])}
    rpc = dict(prepared.get("rpc") or {})
    rpc["wait"] = bool(wait)
    out = panel_rpc("window_show", rpc, timeout=_SHOW_CLOSE_WAIT_S if wait else _SHOW_WAIT_S)
    if not isinstance(out, dict):
        return {"error": "spotlight failed"}
    if out.get("error"):
        return out
    preview = prepared.get("preview") if isinstance(prepared.get("preview"), dict) else {}
    return _finish_show({**preview, **out}, len(plan))


def show(
    *,
    target: str = "",
    title: str = "",
    body: str = "",
    workflow_id: str = "",
    navigate: str = "",
    item_id: str = "",
    action: str = "",
    action_args: dict[str, Any] | None = None,
    also: list[str] | None = None,
    role: str = "",
    within: str = "",
    steps: list[dict[str, Any]] | None = None,
    wait: bool = False,
    window: str = "",
    box: dict[str, Any] | None = None,
    click: bool = False,
) -> dict[str, Any]:
    """Show me, in the app or on another program's window. Returns a result dict.

    The MCP tool, ``api.spotlight`` and the ``ui.spotlight`` workflow node all
    call this, so they behave the same.
    """
    plan: list[dict[str, Any]] = []
    if (target or "").strip() or box is not None or (role or "").strip():
        first = _show_step(_show_fields(
            target=target, title=title, body=body, workflow_id=workflow_id,
            navigate=navigate, item_id=item_id, action=action, action_args=action_args,
            also=also, role=role, within=within, window=window, box=box, click=click,
        ))
        if isinstance(first, str):
            err: dict[str, Any] = {"error": first}
            if first.startswith("unknown route"):
                err["routes"] = list(_ROUTES)
            return err
        plan.append(first)
    for i, raw in enumerate((steps or [])[:_MAX_SHOW_STEPS]):
        if not isinstance(raw, dict):
            return {"error": f"steps[{i}] must be an object"}
        step = _show_step(raw)
        if isinstance(step, str):
            err = {"error": f"steps[{i}]: {step}"}
            if step.startswith("unknown route"):
                err["routes"] = list(_ROUTES)
            return err
        plan.append(step)
    if not plan:
        return {"error": "target is required (or steps, or a box): an id from ducky_ui_list_targets, a name with role, or box {x, y, w, h}"}
    windowed = ["box" in step for step in plan]
    if any(windowed) and not all(windowed):
        return {"error": "a Show me is either inside the app or on a window, not both"}
    if all(windowed):
        return _show_on_window(plan, window, wait)
    params: dict[str, Any] = {"steps": plan} if len(plan) > 1 else dict(plan[0])
    params["wait"] = bool(wait)
    out = panel_rpc("show", params, timeout=_SHOW_CLOSE_WAIT_S if wait else _SHOW_WAIT_S)
    if not isinstance(out, dict):
        return {"error": "show failed"}
    return _finish_show(out, len(plan))


@mcp.tool()
def ducky_ui_show(
    target: str = "",
    title: str = "",
    body: str = "",
    workflow_id: str = "",
    navigate: str = "",
    item_id: str = "",
    action: str = "",
    action_args: dict[str, Any] | None = None,
    also: list[str] | None = None,
    role: str = "",
    within: str = "",
    steps: list[dict[str, Any]] | None = None,
    wait: bool = False,
    window: str = "",
    box: dict[str, Any] | None = None,
    click: bool = False,
    pretty: bool = False,
) -> Any:
    """Show the user one part of the app, or one control in UEFN or another program.

    Opens the view it is in (navigate / workflow_id / action), brings it into sight,
    dims everything else and puts a popup above it with `title` and `body` (short
    markdown). Only the popup's close button closes it, so the user stays on that UI.
    The chat keeps a **Show me** button that plays it again. Use it whenever you tell the
    user where something is or what to click — don't only describe it.

    target: an id from ducky_ui_list_targets ("workflows.toolbar.run",
      "workflows.node.<node_id>", "settings.tab.store"). With `role` it is a name
      instead: role="button", target="Save" (within="workflows.details" narrows where);
      role="text" finds that text on screen.
    also: more ids highlighted together with target (several workflow nodes).
    workflow_id: open this workflow first; "workflows.node.<id>" targets are selected.
    navigate: a route first (same as ducky_ui_navigate); item_id: a Store slug, a
      Settings tab, a chat id or a file path for that route.
    action: a UI action first ("workflows.add_menu"), with action_args ({"query": "if"});
      ducky_ui_list_targets lists a view's actions.
    steps: several things in order, one popup with Back / Next (Close on the last). Each
      step has the same fields: {"target", "title", "body", "navigate", "item_id",
      "workflow_id", "action", "action_args", "also", "role", "within", "click"};
      click=true moves on when the user clicks the highlight. Leave the top-level
      target empty when you pass steps (if you set it, it is step 1).
    On another program's window (UEFN, Blender, a browser): pass ``window`` and
    ``box``. ``window`` is ``"uefn"`` (the main editor), a title pattern, or an
    hwnd. ``box`` is ``{x, y, w, h}`` as fractions of that window (0 to 1), the
    same units as ``uefn_window_click``. Open the tab first (``open_asset_in_uefn``
    for a Blueprint, Widget or material; ``uefn_window_click`` for a menu), then
    ``uefn_window_capture``, then this. It darkens every monitor and blocks every
    click except the hole. ``click: true`` continues only when they press the
    hole. The result includes a screenshot with the box drawn — call again if it
    missed. A call is all in-app steps or all window steps, not a mix.

    wait: true returns only when the user closes the popup (window: also ``reason``
    of done, close or esc, and ``step``).
    Returns {ok, shown, missing, target, steps}; missing=true means the first step
    wasn't found on screen. A window show returns path (the annotated screenshot).
    """
    out = show(
        target=target, title=title, body=body, workflow_id=workflow_id, navigate=navigate,
        item_id=item_id, action=action, action_args=action_args, also=also, role=role,
        within=within, steps=steps, wait=wait, window=window, box=box, click=click,
    )
    text = tool_json(out, pretty=pretty)
    path = str(out.get("path") or "").strip()
    if not path:
        return text
    import os

    if not os.path.isfile(path):
        return text
    try:
        from mcp.server.fastmcp import Image

        return [text, Image(path=path)]
    except Exception:
        return text


@mcp.tool()
def ducky_walkthrough_run(steps: list[dict[str, Any]], pretty: bool = False) -> str:
    """Run a coachmark UI tour (Next / Back / Skip + require_click).

    Use this whenever the user asks how to do something in the panel — list
    targets first, then walk them through. The tour card stays in chat so they
    can replay it later. Does not persist as a first-run product tour.

    steps: ordered list of:
      {
        "target": "settings.tab.store",   # from ducky_ui_list_targets
        "title": "Open the Store",
        "body": "Install plugins here.",
        "advance": "next" | "require_click",
        "mode": "rect" | "circle",
        "navigate": "settings.store"      # optional: open route before the step
      }
    Returns {ok, completed, skipped, steps}. Needs an open panel.
    """
    if not isinstance(steps, list) or not steps:
        return tool_json({"error": "steps must be a non-empty list"}, pretty=pretty)
    cleaned = _normalize_walkthrough_steps(steps)
    if isinstance(cleaned, dict) and cleaned.get("error"):
        return tool_json(cleaned, pretty=pretty)
    assert isinstance(cleaned, list)
    out = panel_rpc("walkthrough_run", {"steps": cleaned}, timeout=_MAX_WALKTHROUGH_WAIT_S)
    if isinstance(out, dict) and not out.get("error"):
        out = {**out, "steps": cleaned}
    return tool_json(out, pretty=pretty)


def _resolve_ask_user_conv_id() -> str:
    """Chat this tool call belongs to.

    A coding agent on the shared bridge binds its chat on the request. That
    id wins, so web search and the permission question land in that chat.
    Otherwise the open embedded chat, then DUCKY_CONV_ID on a dedicated bridge.
    """
    try:
        from backend.workspace import identity as run_identity

        ctx = run_identity.current()
        bound = (ctx.conv_id if ctx is not None else "") or ""
        if bound.strip():
            return bound.strip()
    except Exception:
        pass
    try:
        from frontend.ui_web.agent_modes import get_active_conv_id

        active = get_active_conv_id()
        if active:
            return str(active).strip()
    except Exception:
        pass
    import os

    return (os.environ.get("DUCKY_CONV_ID") or "").strip()


@mcp.tool()
def ducky_ask_user(
    questions: list[dict[str, Any]],
    title: str = "",
    pretty: bool = False,
) -> str:
    """Pause mid-task and ask the user one or more clarifying questions in this chat.

    HARD: use this instead of writing "Your call", "A — … B — …", numbered path options,
    or wait-vs-proceed choices in plain chat text. Ending a turn with prose A/B/C is wrong —
    call this tool so an inline questionnaire docks above the composer until answered.

    Use when a choice would change architecture, delete data, spend money, fork the
    implementation, or you are blocked (e.g. need Verse build before continuing) —
    including mid-turn after partial work. Also use when the same approach fails twice
    and one alternative also fails (or you have no safe alternative left).

    Batch related questions in one call (up to 8). Each question:
      {
        "id": "next_path",
        "prompt": "How should I proceed?",
        "options": [
          {"id": "build_verse", "label": "Build Verse, then finish Scene Graph", "description": "…"},
          {"id": "level_seq", "label": "Level Sequence (no Verse)", "description": "…"},
          {"id": "blind", "label": "Build entities blind now", "description": "…"}
        ],
        "allow_multiple": false,
        "allow_free_text": true,
        "required": true
      }
    Omit options for free-text only. Returns
    {ok, answers:{id:{selected:[…], text, skipped}}, skipped_all, questions}.
    Needs an open panel; UEFN may be offline.
    """
    cleaned = _normalize_ask_user_questions(questions if isinstance(questions, list) else [])
    if isinstance(cleaned, dict) and cleaned.get("error"):
        return tool_json(cleaned, pretty=pretty)
    assert isinstance(cleaned, list)
    payload: dict[str, Any] = {"questions": cleaned}
    header = str(title or "").strip()
    if header:
        payload["title"] = header
    conv_id = _resolve_ask_user_conv_id()
    if conv_id:
        payload["conv_id"] = conv_id
        try:
            from frontend.ui_web.group_orchestrator import lookup_member_hub

            hub = lookup_member_hub(conv_id)
            if hub:
                group_ids, author = hub
                if group_ids:
                    payload["group_ids"] = group_ids
                if author:
                    payload["author"] = author
        except Exception:
            pass
    out = panel_rpc("ask_user", payload, timeout=_MAX_ASK_USER_WAIT_S)
    if isinstance(out, dict) and not out.get("error"):
        out = {**out, "questions": cleaned}
        if conv_id:
            from backend.tools.core.web_lookup import remember_web_permission

            remember_web_permission(conv_id, out)
    return tool_json(out, pretty=pretty)
