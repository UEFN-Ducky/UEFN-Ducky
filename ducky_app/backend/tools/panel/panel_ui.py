"""Guided-UI tools: navigate the panel, run coachmark walkthroughs, ask the user.

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
    "settings.duckies",
    "settings.plans",
    "settings.memory",
    "settings.languages",
    "settings.log_errors",
    "chat",
    "skills_studio",
    "terminals",
    "plans",
    "project_picker",
)

# User-paced UI budget (Skip / Got it / answers end earlier).
_MAX_WALKTHROUGH_WAIT_S = 300.0
# Asks NEVER time out: the agent suspends until the user answers (or Stop /
# panel close). A timed-out ask left the questionnaire on screen while the
# agent "proceeded anyway" and the eventual answer resolved into nothing.
_MAX_ASK_USER_WAIT_S = float("inf")
_MAX_ASK_USER_QUESTIONS = 8


def _navigate(route: str, item_id: str = "") -> dict[str, Any]:
    return panel_rpc("navigate", {"route": route, "item_id": item_id})


def _list_targets(route: str = "") -> dict[str, Any]:
    return panel_rpc("list_targets", {"route": route})


def _normalize_walkthrough_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]] | dict[str, Any]:
    cleaned: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            return {"error": "each step must be an object"}
        tid = str(step.get("target") or "").strip()
        if not tid:
            return {"error": "each step needs target"}
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
            "target": tid,
            "title": title or body[:48] or tid,
            "body": body or title or tid,
            "advance": advance,
            "mode": mode,
        }
        nav = str(step.get("navigate") or "").strip()
        if nav:
            row["navigate"] = nav
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
        cleaned.append(
            {
                "id": qid,
                "prompt": prompt,
                "options": options,
                "allow_multiple": bool(question.get("allow_multiple")),
                "allow_free_text": bool(question.get("allow_free_text", True)),
                "required": bool(question.get("required", True)),
            }
        )
    return cleaned


@mcp.tool()
def ducky_ui_navigate(route: str, item_id: str = "", pretty: bool = False) -> str:
    """Open a panel route so the user doesn't have to hunt for it.

    route: one of settings, settings.general, settings.llms, settings.mcp_plugins,
    settings.skills, settings.appearance, settings.duckies, settings.plans,
    settings.memory, settings.languages, settings.log_errors, chat, skills_studio,
    terminals, plans, project_picker. `item_id` targets a row (e.g. a chat/conv id).
    Returns {ok, route}. Needs an open panel; UEFN may be offline.
    Example: ducky_ui_navigate("settings.mcp_plugins").
    """
    r = (route or "").strip()
    if r not in _ROUTES:
        return tool_json({"error": f"unknown route: {route}", "routes": list(_ROUTES)}, pretty=pretty)
    return tool_json(_navigate(r, (item_id or "").strip()), pretty=pretty)


@mcp.tool()
def ducky_ui_list_targets(route: str = "", pretty: bool = False) -> str:
    """List spotlightable panel controls with stable semantic ids.

    Returns {targets:[{id,label,route,rect:{x,y,w,h},visible,enabled,kind}]}.
    kind: tab | button | input | toggle | dropdown | chat | settings_field |
    plugin_row | skill_row. Pass `route` to hint which view to enumerate.
    Feed ids into ducky_walkthrough_run. Needs an open panel.
    """
    return tool_json(_list_targets((route or "").strip()), pretty=pretty)


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
    """Active embedded chat, else DUCKY_CONV_ID (coding-agent MCP child)."""
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
    out = panel_rpc("ask_user", payload, timeout=_MAX_ASK_USER_WAIT_S)
    if isinstance(out, dict) and not out.get("error"):
        out = {**out, "questions": cleaned}
    return tool_json(out, pretty=pretty)
