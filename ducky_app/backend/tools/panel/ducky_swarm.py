"""ducky_swarm_create: one call builds a whole swarm from a goal and its jobs.

Ask once (agent count, size limits, a model per job) → route each job to a template and
model → preview the tree → build it plan-first (sub plans and steps, then groups and seats
with their lanes, then assign). Every step before ``confirm=true`` is pure and builds
nothing, so the same arguments always give the same preview.

The preview lives in ``swarm_preview`` (payload fields listed there), the build in ``swarm_build``;
this module keeps the tool entry point, job routing and the big-plan suggestion.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.server import mcp
from backend.tools.panel.swarm_build import PanelSwarmApi, build_swarm  # noqa: F401
from backend.tools.panel import swarm_preview as _sp
from backend.tools.panel.swarm_preview import (  # noqa: F401
    DETAILS, SUGGESTED_DETAIL, SUGGESTED_MAX_AGENTS, SUGGESTED_PER_GROUP, _parse_agents, _steps,
    plan_tree, questions_for, walk_groups, walk_seats,
)
from backend.util.json_util import tool_json


# ---- jobs and routing ---------------------------------------------------------------------

def _routing_context() -> tuple[Any, Any]:
    """(settings, profiles) for swarm_routing.route; (None, None) when they can't load."""
    try:
        from frontend.agent_profiles import list_agent_profiles_available
        from frontend.settings import PanelSettings

        return PanelSettings.load(), list_agent_profiles_available()
    except Exception:
        return None, None


def normalize_jobs(jobs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Jobs with their route (kind, profile, suggested model) filled in."""
    from frontend.swarm_routing import route

    settings, profiles = _routing_context()
    out = []
    for raw in jobs or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        purpose = str(raw.get("purpose") or "").strip()
        task = str(raw.get("task") or "").strip()
        routed = route(f"{name}: {purpose} {task}", str(raw.get("kind") or ""), settings=settings, profiles=profiles)
        out.append({
            "name": name,
            "purpose": purpose,
            "task": task,
            # A sub plan without steps can't be assigned: a job with none gets itself as its step.
            "steps": _steps(raw.get("steps")) or _steps([{"content": name, "body_markdown": task}]),
            "paths": [str(p).strip() for p in raw.get("paths") or [] if str(p).strip()],
            "kind": routed["kind"],
            "profile": routed["profile_id"],
            "suggested_model": routed["model"],
            "model": str(raw.get("model") or "").strip(),
        })
    return out


# ---- card edits swarm_preview doesn't apply -----------------------------------------------

_PERMISSIONS = ("may_commit", "may_release", "allow_terminal", "trust_drafts")
# Every key a card or agent edit may carry: swarm_preview's own, then the ones applied here.
_EDIT_KEYS = frozenset({
    "drop", "rename", "to", "seat", "model", "add_step", "drop_step", "rename_step", "mute", "unmute",
    "group", "muted", "interfaces",
    "paths", "profile", *_PERMISSIONS, "run_at_once", "move_step", "to_seat", "start_after",
})


def apply_card_edits(preview: dict[str, Any], edits: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Apply the card's edits on top of a built preview: ``{seat, paths}`` sets the lane, permission
    keys set ``permissions`` on a seat or group, ``{run_at_once}`` the swarm's concurrency cap,
    ``{seat, move_step, to_seat}`` moves a step, ``{group, start_after}`` makes the group wait.
    Unknown keys are listed in ``warnings``; a missing seat, group or step raises ValueError."""
    tree = preview["tree"]
    seats = {s["name"]: s for s in walk_seats(tree)}
    groups = {g["group"]: g for g in walk_groups(tree)}
    warnings: list[str] = []

    def seat_of(name: Any) -> dict[str, Any]:
        hit = seats.get(str(name or "").strip())
        if hit is None:
            raise ValueError(f"No seat called {name!r}; seats: {', '.join(seats)}")
        return hit

    for e in edits or []:
        if not isinstance(e, dict):
            continue
        unknown = sorted(set(e) - _EDIT_KEYS)
        if unknown:
            warnings.append(f"Ignored edit keys {', '.join(unknown)} in {e}")
        if "run_at_once" in e:
            try:
                preview["run_at_once"] = max(1, int(e["run_at_once"]))
            except (TypeError, ValueError):
                warnings.append(f"run_at_once needs a number, got {e['run_at_once']!r}")
        if "move_step" in e:
            src, dst = seat_of(e.get("seat")), seat_of(e.get("to_seat"))
            steps = src["sub_plan"]["steps"]
            hit = next((s for s in steps if s["content"] == str(e["move_step"]).strip()), None)
            if hit is None:
                raise ValueError(f"{src['name']} has no step {e['move_step']!r}")
            if len(steps) == 1:
                raise ValueError(f"{src['name']}'s sub plan needs at least one step: drop the job instead.")
            steps.remove(hit)
            dst["sub_plan"]["steps"].append(hit)
            for s in (src, dst):
                s["sub_plan"]["total"] = len(s["sub_plan"]["steps"])
        elif "seat" in e and "paths" in e:
            seat = seat_of(e["seat"])
            seat["paths"] = [str(p).strip() for p in e["paths"] or [] if str(p).strip()]
        if "seat" in e and str(e.get("profile") or "").strip():
            seat_of(e["seat"])["profile"] = str(e["profile"]).strip()
        perms = {k: bool(e[k]) for k in _PERMISSIONS if k in e}
        if perms:
            target = seat_of(e["seat"]) if "seat" in e else groups.get(str(e.get("group") or "").strip())
            if target is None:
                raise ValueError(f"No group called {e.get('group')!r}; groups: {', '.join(groups) or 'none'}")
            target.setdefault("permissions", {}).update(perms)
        if "start_after" in e:
            group = groups.get(str(e.get("group") or "").strip())
            if group is None:
                raise ValueError(f"No group called {e.get('group')!r}; groups: {', '.join(groups) or 'none'}")
            group["start_after"] = str(e["start_after"] or "").strip()
            group["after"] = [group["start_after"]] if group["start_after"] else []

    # Keep the derived fields in step with the edited tree.
    preview["lanes"] = {s["name"]: s["paths"] for s in seats.values()}
    by_seat = {sec["seat"]: sec for sec in preview["plan"]["sections"]}
    for name, s in seats.items():
        if name in by_seat:
            by_seat[name]["steps"] = s["sub_plan"]["steps"]
        if name in preview.get("jobs", {}):
            preview["jobs"][name] = {**preview["jobs"][name], "paths": s["paths"]}
    if "estimate" in preview:
        preview["estimate"] = _sp.estimate(_sp.preview_seats(tree))
    if warnings:
        preview["warnings"] = warnings
    return preview


def build_preview(goal: str, jobs: list[dict[str, Any]], agents: Any, max_agents: int, per_group: int,
                  edits: list[dict[str, Any]] | None = None, detail: int = SUGGESTED_DETAIL,
                  lead_model: str = "") -> dict[str, Any]:
    """swarm_preview.build_preview plus the card edits it doesn't apply (``apply_card_edits``)."""
    return apply_card_edits(_sp.build_preview(goal, jobs, agents, max_agents, per_group, edits, detail, lead_model),
                            edits)


# ---- the card payload ---------------------------------------------------------------------

def _flat_seats(nodes: list[dict[str, Any]], group: str = "") -> list[dict[str, Any]]:
    """The card's flat seat list: each seat with its nearest group's name."""
    out = []
    for node in nodes:
        if "group" in node:
            out += _flat_seats(node["seats"], node["group"]) + _flat_seats(node["groups"], node["group"])
        else:
            granted = [k for k, v in (node.get("permissions") or {}).items() if v]
            out.append({"name": node["name"], "group": group, "profile": node["profile"], "model": node["model"],
                        "lane": node["paths"], "steps": node["sub_plan"]["total"],
                        "flags": ([] if node["paths"] else ["no_lane"]) + granted})
    return out


def card(status: str, preview: dict[str, Any]) -> dict[str, Any]:
    """The ``ui: swarm_builder`` fields (see web/src/swarm/fixtures/swarm_builder.json) for *preview*."""
    public = {k: v for k, v in preview.items() if k != "jobs"}
    return {"ui": "swarm_builder", "status": status, "goal": preview["goal"], "preview": public,
            "seats": _flat_seats(public["tree"])}


# ---- from the lead's own plan -------------------------------------------------------------

def plan_preview(plan: dict[str, Any]) -> dict[str, Any]:
    """The preview of building *plan* as it is (``swarm_build.seats_from_plan``): one seat per
    section whose children are steps, one group per section of sections, nothing added. Seats
    carry their section id (``sub_plan.id``) and step ids; groups their ``id``."""
    from backend.tools.panel.swarm_build import seats_from_plan

    tree = seats_from_plan(plan)
    seats = list(walk_seats(tree))
    return {
        "goal": str(plan.get("title") or "").strip() or "Swarm",
        "from_plan": True,
        "plan_id": str(plan.get("id") or ""),
        "agents": len(seats),
        "tree": tree,
        "muted": [g["group"] for g in walk_groups(tree) if g["muted"]],
        "lanes": {s["name"]: s["paths"] for s in seats},
        "plan": {"title": str(plan.get("title") or ""),
                 "sections": [{"seat": s["name"], "id": s["sub_plan"]["id"], "title": s["sub_plan"]["title"],
                               "steps": s["sub_plan"]["steps"]} for s in seats]},
    }


def _show_seat_models(preview: dict[str, Any], api: Any, lead: str) -> None:
    """Fill each seat with no Model line with the model the build gives it (the lead's, unless
    its profile names one), so the card never shows ``model: ""``."""
    from backend.tools.panel.swarm_build import _Api

    wrapped = _Api(api)
    for seat in walk_seats(preview["tree"]):
        if not seat.get("model"):
            try:
                seat["model"] = wrapped.seat_model(lead, seat["profile"]) or ""
            except Exception:  # noqa: BLE001 - a preview never fails over a model lookup
                pass


class PlanStore:
    """The plan edits a from-plan Change makes, through the plan tools (events and checks included)."""

    def update(self, chat_id: str, node_id: str, **fields: Any) -> None:
        from backend.tools.panel.ducky_panel import ducky_plan_update_node
        from backend.tools.panel.swarm_build import _loads

        _loads(ducky_plan_update_node(node_id=node_id, chat_id=chat_id, **fields))

    def delete(self, chat_id: str, node_id: str) -> None:
        from backend.tools.panel.ducky_panel import ducky_plan_delete_node
        from backend.tools.panel.swarm_build import _loads

        _loads(ducky_plan_delete_node(node_id=node_id, chat_id=chat_id))

    def move(self, chat_id: str, node_id: str, parent_id: str, index: int) -> None:
        from backend.tools.panel.ducky_panel import ducky_plan_move_node
        from backend.tools.panel.swarm_build import _loads

        _loads(ducky_plan_move_node(node_id=node_id, parent_id=parent_id, index=index, chat_id=chat_id))

    def swap_seat(self, chat_id: str, seat: dict[str, Any], old_conv_id: str, model: str) -> str:
        """Seat a new chat on *model* in the old member's group with the same lane, and hand it the
        section. Returns the new chat id (the old one when the group can't be found)."""
        from backend.tools.panel.ducky_panel import _project_root, ducky_group_invite, load_conversation
        from backend.tools.panel.swarm_build import _loads

        old = load_conversation(old_conv_id, project_root=_project_root())
        group_id = str(getattr(old, "parent_conv_id", "") or "").strip()
        if not group_id:
            return old_conv_id
        res = _loads(ducky_group_invite(group_id=group_id, ducky=seat["profile"], write_allowed=seat["paths"] or None,
                                        name=str(getattr(old, "title", "") or seat["name"]), model=model))
        new = str((res.get("member") or {}).get("member_conv_id") or "") or old_conv_id
        if new != old_conv_id:
            self.update(chat_id, seat["sub_plan"]["id"], assignee=new)
        return new


def _set_line(body: str, label: str, value: str) -> str:
    """*body* with its ``Label: value`` line replaced (or added at the end)."""
    line = f"{label}: {value}"
    pat = re.compile(rf"^\s*(?:\*\*)?{label}(?:\*\*)?\s*:(?:\*\*)?.*$", re.IGNORECASE | re.MULTILINE)
    # A function, so a Windows path's backslashes are never read as group references.
    return pat.sub(lambda _m: line, body, count=1) if pat.search(body) else (body.rstrip() + "\n" + line).lstrip()


PLAN_EDIT_SHAPES = (
    "{seat, profile?, paths?, model?} (seat = section title or id), {seat, rename_step|drop_step, to?}, "
    "{seat, to_group}, {rename, to}, {mute|unmute: group}"
)


def _is_plan_edit(e: dict[str, Any]) -> bool:
    if "seat" in e:
        return any(k in e for k in ("rename_step", "drop_step", "model", "profile", "paths", "to_group"))
    return "rename" in e or "mute" in e or "unmute" in e


def apply_plan_edits(chat_id: str, plan: dict[str, Any], edits: list[dict[str, Any]], store: Any) -> list[str]:
    """Card Change edits on the real plan: ``{seat, rename_step, to}`` / ``{seat, drop_step}`` (a
    step's content or id), ``{seat, model}`` (the section's Model: line), ``{rename, to}`` (a seat
    or group), ``{mute|unmute: group}`` (the group's Muted: line), ``{seat, to_group}`` (move the
    section; '' = top level), ``{seat, profile}`` (its Ducky: line), ``{seat, paths}`` (its Files:
    line, the seat's lane). A seat is named by its section title or id. An edit of any other shape
    is refused (ValueError naming ``PLAN_EDIT_SHAPES``) before anything changes."""
    bad = [e for e in edits if isinstance(e, dict) and not _is_plan_edit(e)]
    if bad:
        raise ValueError(f"Not a plan edit: {bad[0]}. Accepted: {PLAN_EDIT_SHAPES}.")
    preview = plan_preview(plan)
    seats = {s["name"]: s for s in walk_seats(preview["tree"])}
    groups = {g["group"]: g for g in walk_groups(preview["tree"])}
    bodies: dict[str, str] = {}

    def walk(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            bodies[str(n.get("id") or "")] = str(n.get("body_markdown") or "")
            walk(n.get("children") or [])

    walk(plan.get("nodes") or [])
    warnings: list[str] = []

    def seat_of(name: Any) -> dict[str, Any]:
        key = str(name or "").strip()
        hit = seats.get(key) or next((s for s in seats.values() if s["sub_plan"]["id"] == key), None)
        if hit is None:
            raise ValueError(f"No seat called {name!r}; seats: {', '.join(seats)}")
        return hit

    for e in edits:
        if not isinstance(e, dict):
            continue
        if "seat" in e and ("profile" in e or "paths" in e):
            sid = seat_of(e["seat"])["sub_plan"]["id"]
            if str(e.get("profile") or "").strip():
                bodies[sid] = _set_line(bodies.get(sid, ""), "Ducky", str(e["profile"]).strip())
            if "paths" in e:
                paths = [str(p).strip().replace("\\", "/") for p in e["paths"] or [] if str(p).strip()]
                bodies[sid] = _set_line(bodies.get(sid, ""), "Files", ", ".join(f"`{p}`" for p in paths))
            store.update(chat_id, sid, body_markdown=bodies[sid])
            if not e.get("model"):
                continue
        if "rename_step" in e or "drop_step" in e:
            seat = seat_of(e.get("seat"))
            key = str(e.get("rename_step") or e.get("drop_step") or "").strip()
            step = next((s for s in seat["sub_plan"]["steps"] if key in (s["id"], s["content"])), None)
            if step is None:
                raise ValueError(f"{seat['name']} has no step {key!r}")
            if "drop_step" in e:
                store.delete(chat_id, step["id"])
            else:
                store.update(chat_id, step["id"], content=str(e.get("to") or "").strip() or step["content"])
        elif "seat" in e and e.get("model"):
            seat = seat_of(e["seat"])
            sid, model = seat["sub_plan"]["id"], str(e["model"]).strip()
            bodies[sid] = _set_line(bodies.get(sid, ""), "Model", model)
            store.update(chat_id, sid, body_markdown=bodies[sid])
            old = str(seat.get("assignee") or "").strip()
            if old:  # a live seat: its chat runs the old model, so swap it and retire the old chat
                from backend.tools.panel import ducky_finish_team

                new = store.swap_seat(chat_id, seat, old, model)
                if new and new != old:
                    ducky_finish_team.retire_swapped_seat(old, new)
        elif "seat" in e and "to_group" in e:
            sid = seat_of(e["seat"])["sub_plan"]["id"]
            target = str(e["to_group"] or "").strip()
            if target and target not in groups:
                raise ValueError(f"No group called {target!r}; groups: {', '.join(groups) or 'none'}")
            parent = groups[target]["id"] if target else ""
            store.move(chat_id, sid, parent, 10_000)  # ponytail: a big index = append at the end
        elif "rename" in e:
            name = str(e["rename"]).strip()
            node_id = seats[name]["sub_plan"]["id"] if name in seats else (groups[name]["id"] if name in groups else "")
            if not node_id:
                raise ValueError(f"No seat or group called {name!r}")
            store.update(chat_id, node_id, content=str(e.get("to") or "").strip() or name)
        elif "mute" in e or "unmute" in e:
            name = str(e.get("mute") or e.get("unmute")).strip()
            if name not in groups:
                raise ValueError(f"No group called {name!r}; groups: {', '.join(groups) or 'none'}")
            gid = groups[name]["id"]
            bodies[gid] = _set_line(bodies.get(gid, ""), "Muted", "yes" if "mute" in e else "no")
            store.update(chat_id, gid, body_markdown=bodies[gid])
    return warnings


def _swarm_from_plan(confirm: bool, api: Any, edits: list[dict[str, Any]] | None = None,
                     store: Any = None) -> dict[str, Any]:
    api = api or PanelSwarmApi()
    lead = api.caller()
    plan = api.load_plan(lead) if lead else None
    if not plan or not plan.get("nodes"):
        return {"status": "error", "error": "from_plan needs a plan in the calling chat (ducky_create_plan first)."}
    warnings: list[str] = []
    # Edits land on the plan once, at preview; confirm builds what the plan now says, so a card
    # that resends its earlier edits with confirm=true doesn't apply them twice.
    if edits and not confirm:
        try:
            warnings = apply_plan_edits(lead, plan, edits, store or PlanStore())
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
        plan = api.load_plan(lead) or plan  # re-preview what the plan now says
    elif edits:
        # A build sent straight with seat edits (no preview first) still seats them: profile and
        # paths rewrite their lines (the same again is harmless); a model the plan already says
        # is skipped, so a resent card never swaps a live seat twice.
        models = {s["name"]: s.get("model") for s in walk_seats(plan_preview(plan)["tree"])}
        new = []
        for e in edits:
            if not isinstance(e, dict) or not e.get("seat"):
                continue
            row = dict(e)
            if models.get(str(e["seat"]).strip(), object()) == str(e.get("model") or "").strip():
                row.pop("model", None)
            if row.get("model") or "profile" in row or "paths" in row:
                new.append(row)
        try:
            if new:
                apply_plan_edits(lead, plan, new, store or PlanStore())
                plan = api.load_plan(lead) or plan
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
    preview = plan_preview(plan)
    _show_seat_models(preview, api, lead)
    if warnings:
        preview["warnings"] = warnings
    if not preview["agents"]:
        return {"status": "error", "goal": preview["goal"], "error": "Nothing to seat: no section has steps."}
    if not confirm:
        return {**card("needs_confirm", preview),
                "next": ("Show the person this preview of the plan's sections as seats. Build: same call with "
                         "confirm=true. Change: same call with only the NEW edits; they change the plan itself.")}
    try:
        built = build_swarm(api=api, from_plan=plan)
    except Exception as exc:  # SwarmBuildError archives what it made; say so on the card
        return {"status": "error", "goal": preview["goal"], "error": str(exc), "tree": preview["tree"]}
    return {"status": "done", "goal": preview["goal"], "agents": preview["agents"], **built}


# ---- the tool -----------------------------------------------------------------------------

def swarm_create(goal: str, jobs: list[dict[str, Any]] | None, agents: Any = "", max_agents: int = 0,
                 per_group: int = 0, edits: list[dict[str, Any]] | None = None, confirm: bool = False,
                 api: Any = None, detail: int = 0, from_plan: bool = False, store: Any = None) -> dict[str, Any]:
    if from_plan:
        return _swarm_from_plan(confirm, api, edits, store)
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("goal is required")
    routed = normalize_jobs(jobs)
    if not routed:
        raise ValueError("jobs is required: [{name, purpose, task, steps[], paths[], kind?, model?}]")
    detail = int(detail or 0)
    questions = questions_for(routed, agents, int(max_agents or 0), int(per_group or 0), detail)
    if questions:
        # The card shows the suggested answers' preview; the agent still asks the questions.
        draft = build_preview(goal, routed, agents if _parse_agents(agents) != "" else "auto",
                              int(max_agents or 0) or SUGGESTED_MAX_AGENTS, int(per_group or 0) or SUGGESTED_PER_GROUP,
                              edits, detail if detail in DETAILS else SUGGESTED_DETAIL)
        return {
            **card("needs_answers", draft),
            "questions": questions,
            "next": ("Ask these with ONE ducky_ask_user(questions=...), then call ducky_swarm_create again "
                     "with detail (1-4), agents='auto' or a number, max_agents and per_group, and each job's "
                     "chosen model in jobs[i].model. Nothing was built."),
            "routes": [{"job": j["name"], "kind": j["kind"], "profile": j["profile"],
                        "suggested_model": j["suggested_model"]} for j in routed],
        }
    preview = build_preview(goal, routed, agents, int(max_agents), int(per_group), edits, detail)
    if not confirm:
        return {
            **card("needs_confirm", preview),
            "next": ("Show the person this preview. Build: same call with confirm=true. Change: same call "
                     "with another detail (1-4) or edits=[{drop}, {rename, to}, {seat, model}, {mute}|{unmute}] "
                     "(keep earlier edits)."),
        }
    try:
        built = build_swarm(preview, api or PanelSwarmApi())
    except Exception as exc:
        # Chats made before the failure stay: say which so nobody seats them twice.
        return {"status": "error", "goal": goal, "error": str(exc), "tree": preview["tree"]}
    return {"status": "done", "goal": goal, "agents": preview["agents"], **built}


@mcp.tool()
def ducky_swarm_create(
    goal: str = "",
    jobs: list[dict[str, Any]] | None = None,
    agents: str = "",
    max_agents: int = 0,
    per_group: int = 0,
    edits: list[dict[str, Any]] | None = None,
    confirm: bool = False,
    detail: int = 0,
    from_plan: bool = False,
    pretty: bool = False,
) -> str:
    """Make a swarm: write every job from the request ({name, purpose, task, steps[], paths[]}), call
    WITHOUT detail; needs_answers: ask its questions (detail too) in ONE ducky_ask_user, call again;
    needs_confirm: show the preview (tree, steps, detail slider), confirm=true builds exactly it.
    from_plan=true (no goal/jobs): seat your own plan as it is, one seat per sub plan; each seat's
    profile is its section's Ducky: line, else its planned_assignee, else Developer for code Files,
    and its model the section's Model: line, else yours. from_plan edits change the plan itself:
    {seat, profile?, paths?, model?} (seat = section title or id; paths = its Files/lane),
    {seat, rename_step|drop_step, to?}, {seat, to_group}, {rename, to}, {mute|unmute: group}.
    Any other edit is refused with that list.
    """
    return tool_json(swarm_create(goal, jobs, agents, max_agents, per_group, edits, confirm, detail=detail,
                                  from_plan=from_plan), pretty=pretty)


# ---- suggest a swarm for a big plan -------------------------------------------------------

# "Files: a", "**Files**: a" and "**Files:** a".
_FILES = re.compile(r"^\s*(?:\*\*)?files(?:\*\*)?\s*:(?:\*\*)?\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    kids = [c for c in node.get("children") or [] if isinstance(c, dict)]
    return [x for c in kids for x in _leaves(c)] if kids else [node]


def _files(node: dict[str, Any]) -> set[str]:
    m = _FILES.search(str(node.get("body_markdown") or ""))
    return {p.strip(" `.") for p in re.split(r"[,;]", m.group(1)) if p.strip(" `.")} if m else set()


def suggest_swarm(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """A swarm suggestion for a plan with 3+ sections or 10+ steps whose sections' Files lists
    don't overlap; None for small plans."""
    from frontend.swarm_routing import route

    nodes = [n for n in (plan or {}).get("nodes") or [] if isinstance(n, dict)]
    sections = [n for n in nodes if n.get("children")]
    steps = sum(len(_leaves(n)) for n in nodes)
    if not sections or (len(sections) < 3 and steps < 10):
        return None
    seen: set[str] = set()
    for sec in sections:
        files = _files(sec)
        if files & seen:
            return None
        seen |= files
    jobs = []
    for sec in sections:
        name = str(sec.get("content") or "").strip()
        r = route(f"{name}: {sec.get('body_markdown') or ''}")
        jobs.append({"name": name, "kind": r["kind"], "profile": r["profile_id"], "model": r["model"],
                     "steps": [str(x.get("content") or "") for x in _leaves(sec)], "paths": sorted(_files(sec))})
    goal = str((plan or {}).get("title") or "").strip() or "Plan"
    call_jobs = [{"name": j["name"], "kind": j["kind"], "steps": j["steps"], "paths": j["paths"]} for j in jobs]
    return {
        "agents": len(jobs),
        "jobs": [j["name"] for j in jobs],
        "routes": [{"job": j["name"], "profile": j["profile"], "model": j["model"]} for j in jobs],
        "ask": f"Run this as a swarm of {len(jobs)}?",
        "call": f"ducky_swarm_create(goal={json.dumps(goal)}, jobs={json.dumps(call_jobs)})",
    }


def _sub_plans(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sections whose children are all steps, at any depth (one member's sub plan each)."""
    out = []
    for node in nodes:
        kids = [c for c in node.get("children") or [] if isinstance(c, dict)]
        if kids and all(not c.get("children") for c in kids):
            out.append(node)
        elif kids:
            out += _sub_plans(kids)
    return out


def suggest_from_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """A ``swarm_builder`` card drafted from *plan*: one seat per unassigned sub plan, its Files as
    the lane. None unless there are 2+ such sub plans and no file is in two of them."""
    sections = [s for s in _sub_plans([n for n in (plan or {}).get("nodes") or [] if isinstance(n, dict)])
                if not str(s.get("assignee") or "").strip()]
    if len(sections) < 2:
        return None
    seen: set[str] = set()
    for sec in sections:
        if _files(sec) & seen:
            return None
        seen |= _files(sec)
    jobs = normalize_jobs([{
        "name": str(sec.get("content") or "").strip(),
        "purpose": str(sec.get("body_markdown") or "").strip()[:200],
        "steps": [{"content": str(x.get("content") or ""), "body_markdown": str(x.get("body_markdown") or "")}
                  for x in sec["children"]],
        "paths": sorted(_files(sec)),
    } for sec in sections])
    goal = str((plan or {}).get("title") or "").strip() or "Plan"
    # ponytail: one seat per sub plan (detail 2, no cap); the card's sliders reshape it via swarm_preview.
    return card("needs_confirm", build_preview(goal, jobs, "auto", len(jobs), SUGGESTED_PER_GROUP, None,
                                               SUGGESTED_DETAIL))
