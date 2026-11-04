"""Swarm build: turn a confirmed ``swarm_preview.build_preview`` (or the lead's own plan) into
groups and seats.

Order: validate everything, then create; a failed create archives what was made (nothing
has started yet). Each seat gets a ping turn before its sub plan is assigned. Seats or
groups with ``start_after`` wait until that step completes (``on_step_completed``). The
swarm record lives at ``.ducky/swarms/<id>/swarm.json`` beside the ``outputs/`` folder every
seat writes its deliverables to, and holds the ``Hands over:`` table (``handovers_for``).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from backend.tools.panel.swarm_preview import DONE_LINE, walk_groups, walk_seats

PING_TIMEOUT_S = 30.0
# A cold seat's tool server takes 35-40 s before the agent says anything: the 30 s answer
# clock starts at its first output; a seat silent for this long is counted as not started.
STARTUP_MAX_S = 180.0
SWARMS_DIR = ".ducky/swarms"


class SwarmBuildError(ValueError):
    """A build that made nothing (or archived what it made). ``result`` = {ok, error, created}."""

    def __init__(self, error: str, created: int = 0) -> None:
        super().__init__(error)
        self.result = {"ok": False, "error": error, "created": created}


def _loads(text: str) -> dict[str, Any]:
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("result"), str):
        data = json.loads(data["result"])
    if not isinstance(data, dict) or data.get("ok") is False:
        raise ValueError(str((data or {}).get("error") if isinstance(data, dict) else data))
    return data


class PanelSwarmApi:
    """The live calls a build makes, over the existing group and plan tools."""

    def caller(self) -> str:
        from backend.tools.panel.ducky_panel import _resolve_plan_chat_id

        return _resolve_plan_chat_id("")

    def project_root(self) -> str:
        from backend.tools.panel.ducky_panel import _project_root

        return str(_project_root() or "")

    def group_create(self, name: str, parent_folder_id: str = "") -> dict[str, Any]:
        from backend.tools.panel.ducky_panel import ducky_group_create

        res = _loads(ducky_group_create(name=name, parent_folder_id=parent_folder_id))
        return {"id": res.get("id") or "", "folder_id": res.get("folder_id") or "", "reused": bool(res.get("reused"))}

    def load_plan(self, chat_id: str) -> dict[str, Any] | None:
        from backend.agent.coding_agents.plans import load_plan

        return load_plan(chat_id, self.project_root())

    def is_chat(self, conv_id: str) -> bool:
        from frontend.ui_web.chats.conversations import load_conversation

        return load_conversation(conv_id, self.project_root()) is not None

    def add_member(self, group_id: str, conv_id: str) -> None:
        from backend.tools.panel.ducky_panel import ducky_group_add_member

        _loads(ducky_group_add_member(group_id=group_id, conv_id=conv_id))

    def set_lane(self, group_id: str, conv_id: str, paths: list[str]) -> None:
        from backend.tools.panel.ducky_panel import ducky_group_set_lane

        _loads(ducky_group_set_lane(group_id=group_id, member_conv_id=conv_id, write_allowed=paths))

    def chat_home(self, conv_id: str) -> dict[str, str]:
        from frontend.ui_web.chats.conversations import load_conversation

        conv = load_conversation(conv_id, self.project_root())
        return {"folder_id": str(getattr(conv, "folder_id", "") or ""),
                "parent_conv_id": str(getattr(conv, "parent_conv_id", "") or "")}

    def restore_home(self, conv_id: str, home: dict[str, str]) -> None:
        """Put the lead back where it was, so archiving the root group doesn't take it along."""
        from frontend.ui_web.chats.conversations import load_conversation, move_conversation
        from frontend.ui_web.chats.saves import save_conversation

        root = self.project_root()
        move_conversation(conv_id, home.get("folder_id") or "", root)
        conv = load_conversation(conv_id, root)
        if conv is not None:
            conv.parent_conv_id = home.get("parent_conv_id") or ""
            save_conversation(conv, root)

    def add_leader(self, group_id: str, conv_id: str) -> None:
        from backend.tools.panel.ducky_panel import ducky_group_add_member

        _loads(ducky_group_add_member(group_id=group_id, conv_id=conv_id, as_leader=True, move_folder=True))

    def rename_chat(self, conv_id: str, title: str) -> None:
        from backend.tools.panel.ducky_panel import ducky_rename_chat

        _loads(ducky_rename_chat(conv_id=conv_id, title=title))

    def ensure_plan(self, chat_id: str, goal: str) -> None:
        from backend.agent.coding_agents.plans import load_plan_view
        from backend.tools.panel.ducky_panel import _project_root, ducky_create_plan

        try:
            plan = load_plan_view(chat_id, project_root=_project_root())
        except ValueError:
            plan = None
        if not plan:
            _loads(ducky_create_plan(title=goal, overview=f"Swarm: {goal}", nodes=[], chat_id=chat_id))

    def plan_add_node(self, chat_id: str, content: str, parent_id: str = "", kind: str = "step",
                      body_markdown: str = "") -> str:
        from backend.tools.panel.ducky_panel import ducky_plan_add_node

        res = _loads(ducky_plan_add_node(content=content, parent_id=parent_id, chat_id=chat_id, kind=kind,
                                         body_markdown=body_markdown))
        return str((res.get("node") or {}).get("id") or "")

    def plan_delete_node(self, chat_id: str, node_id: str) -> None:
        from backend.tools.panel.ducky_panel import ducky_plan_delete_node

        _loads(ducky_plan_delete_node(node_id=node_id, chat_id=chat_id))

    def invite(self, group_id: str, profile: str, name: str, model: str,
               write_allowed: Optional[list[str]]) -> str:
        from backend.tools.panel.ducky_panel import ducky_group_invite

        res = _loads(ducky_group_invite(group_id=group_id, ducky=profile, write_allowed=write_allowed,
                                        name=name, model=model))
        return str((res.get("member") or {}).get("member_conv_id") or "")

    def seat_model(self, lead: str, profile: str) -> str:
        """The lead's own agent and model (``claude_code:claude-opus-5-5``) for a seat whose
        profile names no model; '' when the profile has one (it wins) or the lead has none."""
        from backend.tools.panel.ducky_panel import _resolve_ducky_profile
        from frontend.favorite_models import first_favorite, qualify
        from frontend.ui_web.chats.conversations import load_conversation

        if first_favorite((_resolve_ducky_profile(profile) or {}).get("favorite_models")):
            return ""
        conv = load_conversation(lead, self.project_root())
        model = str(getattr(conv, "model", "") or "").strip()
        agent = str(getattr(conv, "coding_agent", "") or "").strip()
        backend = agent if agent and agent != "ducky" else str(getattr(conv, "provider", "") or "").strip()
        return qualify(backend, model) if model and backend else ""

    def set_muted(self, conv_id: str, muted: bool) -> None:
        from backend.tools.panel.ducky_panel import ducky_set_muted

        _loads(ducky_set_muted(conv_id=conv_id, muted=muted))

    def ping(self, conv_id: str, text: str, timeout_s: float, later: Any = None) -> dict[str, Any]:
        """One tiny turn through K3's seat queue (see ``queued_ping``)."""
        from frontend.ui_web import seat_queue
        from frontend.ui_web.agent_turns.routing import run_message_and_wait

        def ask(cid: str, msg: str, secs: float) -> dict[str, Any]:
            # Never cancel: a seat that answers late is still assigned (``later``).
            return run_message_and_wait(cid, msg, "agent", timeout_sec=secs, queue_if_busy=True,
                                        cancel_on_timeout=False)

        def first_output(cid: str) -> bool:
            from frontend.ui_web.agent_turns.state import last_output_age_s

            # In-process only: in the tool server this stays False and the reply itself ends the wait.
            return last_output_age_s(cid) is not None

        return queued_ping(seat_queue.request, ask, conv_id, text, timeout_s, later, first_output)

    def trust_drafts(self, group_ids: list[str]) -> None:
        from backend.uefn_plugins.store import set_swarm_draft_trust

        set_swarm_draft_trust(group_ids, True)

    def assign(self, chat_id: str, node_id: str, assignee: str) -> None:
        from backend.tools.panel.ducky_panel import ducky_plan_update_node

        _loads(ducky_plan_update_node(node_id=node_id, chat_id=chat_id, assignee=assignee))

    def archive(self, conv_id: str) -> None:
        """The ducky_archive_chats path without its ask: nothing in a half-made swarm has started."""
        from backend.tools.panel.ducky_panel import _panel_api
        from frontend.archive_folder import ARCHIVE_FOLDER_ID

        _panel_api().move_conversation(conv_id, ARCHIVE_FOLDER_ID)

    def save_file(self, rel: str, text: str) -> None:
        from backend.workspace.runtime import get_writer

        get_writer().write_text(rel, text, tool="ducky_swarm_create")

    def read_records(self) -> list[dict[str, Any]]:
        return read_records(self.project_root())


class _Defaults:
    """Calls an older api (e.g. a test's) may lack: no files, the lead stays put, every ping answers."""

    def project_root(self) -> str:
        return ""

    def chat_home(self, conv_id: str) -> dict[str, str]:
        return {}

    def restore_home(self, conv_id: str, home: dict[str, str]) -> None:
        pass

    def ping(self, conv_id: str, text: str, timeout_s: float, later: Any = None) -> dict[str, Any]:
        return {"ok": True}

    def is_chat(self, conv_id: str) -> bool:
        return False

    def seat_model(self, lead: str, profile: str) -> str:
        return ""

    def trust_drafts(self, group_ids: list[str]) -> None:
        pass

    def plan_delete_node(self, chat_id: str, node_id: str) -> None:
        pass

    def save_file(self, rel: str, text: str) -> None:
        pass

    def read_records(self) -> list[dict[str, Any]]:
        return []


class _Api:
    def __init__(self, api: Any) -> None:
        self._api = api

    def __getattr__(self, name: str) -> Any:
        return getattr(self._api, name, None) or getattr(_Defaults(), name)


def read_records(project_root: str) -> list[dict[str, Any]]:
    base = os.path.join(project_root or "", *SWARMS_DIR.split("/"))
    out = []
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        try:
            with open(os.path.join(base, name, "swarm.json"), encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            continue
    return out


# ---- the lead's plan → tree ---------------------------------------------------------------

def _line(body: str, label: str) -> str:
    """The text after ``label:`` on its line ('**Files:**', 'Files**:' too), '' when absent."""
    m = re.search(rf"^\s*(?:\*\*)?{label}(?:\*\*)?\s*:(?:\*\*)?\s*(.+)$", body or "", re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


# A lane is a path or a glob: a slash or a '*', or a file name with an extension. Backticked
# operation names (`model3d.start`) and prose are not. ponytail: bare names need a 1-4 letter
# extension (or .verse); widen the list when a real lane file is missed.
_PATHISH = re.compile(r"[/\\*]|\.(?:[A-Za-z][A-Za-z0-9]{0,3}|verse)$")


def _paths(text: str) -> list[str]:
    """Paths from a Files/Hands over line: the backticked ones (spaces kept) and the bare words
    around them, each only when it reads as a path or glob."""
    ticked = re.findall(r"`([^`]+)`", text)
    rest = re.split(r"[,;\s()]+", re.sub(r"`[^`]*`", " ", text))
    out: list[str] = []
    for p in (x.strip(" .`") for x in [*ticked, *rest]):
        if p and _PATHISH.search(p) and p not in out:
            out.append(p)
    return out


# The ducky a section's files call for; code and anything mixed goes to Developer.
_PROFILE_BY_EXT = {".verse": "verse-coder", ".blend": "3d-modeler", ".fbx": "3d-modeler",
                   ".obj": "3d-modeler", ".glb": "3d-modeler", ".gltf": "3d-modeler"}


def _profile_for_files(paths: list[str]) -> str:
    """A seat's profile from its Files, never from words in its title (Nov 3 2026: code sections
    titled "... 3D endpoint" and "... tests" were seated as 3d-modeler and tester)."""
    kinds = {_PROFILE_BY_EXT.get(os.path.splitext(p.rstrip("/*"))[1].lower(), "developer") for p in paths}
    return kinds.pop() if len(kinds) == 1 else "developer"


def _plain(text: str) -> str:
    return text.replace("*", "").replace("`", "").strip()


def _kids(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in node.get("children") or [] if isinstance(c, dict)]


def seats_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """The swarm tree of *plan*: a section whose children are steps = a seat (its sub plan), a
    section whose children are sections = a group, at any depth. Top-level steps stay with the
    lead. Seats read ``Files:``, ``Hands over:``, ``Ducky:``, ``Model:`` and ``Start after:``
    from their body. The profile is the Ducky line, else ``planned_assignee``, else what the
    Files call for (``_profile_for_files``); no Model line = the lead's model at build."""

    def seat(sec: dict[str, Any]) -> dict[str, Any]:
        body = str(sec.get("body_markdown") or "")
        name = str(sec.get("content") or "").strip()
        planned = str(sec.get("planned_assignee") or "").strip()
        paths = _paths(_line(body, "Files"))
        profile = _plain(_line(body, "Ducky")) or planned or _profile_for_files(paths)
        model, kind = _plain(_line(body, "Model")), ""
        steps = [{"id": str(s.get("id") or ""), "content": str(s.get("content") or ""),
                  "body_markdown": str(s.get("body_markdown") or "")} for s in _kids(sec)]
        hands = _line(body, "Hands over")
        handover = None
        if hands:
            parts = re.split(r"\s*(?:→|->)\s*|\s+to\s+(?=[^`]*$)", hands, maxsplit=1)
            if len(parts) == 2:
                handover = {"paths": _paths(parts[0]), "to": _plain(parts[1]).strip(" .")}
        return {"name": name, "job": name, "kind": kind, "profile": profile, "model": model,
                "planned_assignee": planned, "assignee": str(sec.get("assignee") or "").strip(),
                "paths": paths, "handover": handover,
                "start_after": _plain(_line(body, "Start after")),
                "sub_plan": {"id": str(sec.get("id") or ""), "title": name, "done": 0,
                             "total": len(steps), "steps": steps}}

    def node(sec: dict[str, Any]) -> dict[str, Any]:
        kids = _kids(sec)
        if not any(_kids(k) for k in kids):
            return seat(sec)
        sub = [node(k) for k in kids if _kids(k)]  # loose steps beside sub-sections stay with the lead
        body = str(sec.get("body_markdown") or "")
        muted = _plain(_line(body, "Muted")).lower()  # the card writes "Muted: yes|no"; missing = muted
        return {"group": str(sec.get("content") or "").strip(), "id": str(sec.get("id") or ""), "purpose": "",
                "muted": muted not in ("no", "false", "off"), "start_after": _plain(_line(body, "Start after")),
                "seats": [s for s in sub if "group" not in s], "groups": [g for g in sub if "group" in g]}

    return [node(sec) for sec in (plan.get("nodes") or []) if isinstance(sec, dict) and _kids(sec)]


# ---- validate -----------------------------------------------------------------------------

def _relative_lane(path: str, root: str) -> str:
    """*path* normalized: relative to the open project when inside it, kept full when inside
    another of the person's Ducky projects (lanes enforce full paths there, so seating a team
    never needs a project switch); raises ValueError in no known project."""
    from backend.workspace.lanes import LaneGlobError, _is_abs, normalize_glob

    p = path.strip().replace("\\", "/")
    if _is_abs(p):
        r = root.replace("\\", "/").rstrip("/")
        if r and (p.lower() == r.lower() or p.lower().startswith(r.lower() + "/")):
            p = p[len(r):].lstrip("/") or "**"
        else:
            try:
                return normalize_glob(p)  # refuses a full path in no Ducky project
            except LaneGlobError:
                raise ValueError("lane outside project") from None
    return normalize_glob(p)


def validate(tree: list[dict[str, Any]], root: str) -> list[str]:
    """Every reason the build would fail halfway, checked before anything is made. Lanes are
    rewritten project-relative in place."""
    from backend.workspace.lanes import check_lane_set

    errors: list[str] = []
    # group_create reuses a same-name group anywhere on the island, so names are unique tree-wide.
    names = [g["group"] for g in walk_groups(tree)]
    for name in sorted({n for n in names if names.count(n) > 1}):
        errors.append(f"two groups called {name!r} (the second would reuse the first)")

    def check_groups(nodes: list[dict[str, Any]], where: str) -> None:
        for g in (n for n in nodes if "group" in n):
            if not str(g.get("group") or "").strip():
                errors.append(f"{where}: a group has no name")
            if not list(walk_seats([g])):
                errors.append(f"group {g['group']!r}: no seats")
            check_groups(list(g.get("seats") or []) + list(g.get("groups") or []), f"group {g['group']!r}")

    check_groups(tree, "root")
    seats = list(walk_seats(tree))
    seen_names: set[str] = set()
    owner: dict[str, str] = {}
    for i, s in enumerate(seats, 1):
        label = f"seat {i}"
        if s["name"] in seen_names:
            errors.append(f"{label}: two seats called {s['name']!r}")
        seen_names.add(s["name"])
        if not str(s.get("profile") or "").strip():
            errors.append(f"{label}: no ducky profile")
        fixed = []
        for p in s.get("paths") or []:
            try:
                fixed.append(_relative_lane(p, root))
            except ValueError as exc:
                errors.append(f"{label}: {exc}" + ("" if str(exc) == "lane outside project" else f" ({p})"))
        s["paths"] = sorted(set(fixed))
        sp = s.get("sub_plan") or {}
        for sid in [sp.get("id"), *(st.get("id") for st in sp.get("steps") or [])]:
            if sid and owner.setdefault(sid, s["name"]) != s["name"]:
                errors.append(f"{label}: step {sid} is already {owner[sid]}'s")
    if not errors:
        errors += check_lane_set({s["name"]: s["paths"] for s in seats})["errors"]
    return errors


# ---- build --------------------------------------------------------------------------------

def _slug(i: int, name: str) -> str:
    return f"{i}-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "seat")


def _ping_text(swarm_id: str, seat: dict[str, Any]) -> str:
    return (f"Seat check for the swarm: reply with one word, ok. Your sub plan comes next. Put every "
            f"deliverable in {seat['outputs']}/ (it outlives your sandbox and this chat).")


def _answer(out: dict[str, Any]) -> dict[str, Any]:
    # A finished turn means the seat started, even when its reply text wasn't read back yet
    # (Nov 3 2026: seats that answered "ok" came back done with no assistant_text and were
    # reported "didn't start (done)"). A turn still running is alive too.
    if out.get("status") in ("done", "running"):
        return {"ok": True}
    return {"ok": False, "error": str(out.get("error") or out.get("status") or "no reply")}


def queued_ping(request: Any, ask: Any, conv_id: str, text: str, timeout_s: float,
                later: Any = None, first_output: Any = None) -> dict[str, Any]:
    """Ping *conv_id* through the seat queue: ``request(conv_id, start)`` runs ``start`` when a
    slot is free, and ``ask(conv_id, text, secs)`` is the turn (run_message_and_wait's reply,
    never cancelled). ``first_output(conv_id)`` says the agent has sent anything yet: the
    ``timeout_s`` for an answer count from then (from the start without it), and a seat
    with no output in STARTUP_MAX_S didn't start. Returns {ok: True}, {ok: False, error}
    (no answer; its turn keeps running) or {ok: None, queued: True} (no slot yet).
    ``later(result)`` gets an answer that comes after this returned."""
    lock = threading.Lock()
    started, done = threading.Event(), threading.Event()
    out: dict[str, Any] = {}
    waiting = [True]

    def start() -> None:
        started.set()
        res = _answer(ask(conv_id, text, STARTUP_MAX_S + timeout_s + 60))
        with lock:
            out.update(res)
            done.set()
            late = not waiting[0]
        if late and later is not None:
            later(res)

    if request(conv_id, start) == "busy":
        return {"ok": True}  # already running a turn or waiting in line: it's alive
    started.wait(timeout_s)
    with lock:
        if not started.is_set():
            waiting[0] = False
            return {"ok": None, "queued": True}
    error = "no reply"
    if first_output is not None:
        until = time.monotonic() + STARTUP_MAX_S
        while not done.is_set() and not _said_anything(first_output, conv_id) and time.monotonic() < until:
            done.wait(0.5)
        if not done.is_set() and not _said_anything(first_output, conv_id):
            error = f"no output in {STARTUP_MAX_S:g} s"
    if error == "no reply":
        done.wait(timeout_s)
    with lock:
        if not done.is_set():
            waiting[0] = False  # its turn keeps running: a late answer still seats it
            return {"ok": False, "error": error}
        return dict(out)


def _said_anything(first_output: Any, conv_id: str) -> bool:
    try:
        return bool(first_output(conv_id))
    except Exception:  # noqa: BLE001 - no reading: the reply itself ends the wait
        return False


def _settle(seat: dict[str, Any], res: dict[str, Any], api: Any, lead: str) -> None:
    if res.get("ok"):
        api.assign(lead, seat["sub_plan"]["id"], seat["conv_id"])
        seat["status"] = "assigned"
        seat.pop("error", None)
    elif res.get("queued"):
        if seat.get("status") not in ("assigned", "seat_failed"):  # its late answer may have come first
            seat["status"] = "queued"  # waiting for a free slot: not silent, assigned once it answers
    else:
        seat["status"] = "seat_failed"
        seat["error"] = (f"{seat['name']} ({seat.get('model') or seat['profile']}): didn't start "
                         f"({res.get('error') or 'no reply'}) · Swap model")


def _start_seats(seats: list[dict[str, Any]], api: Any, record: dict[str, Any]) -> None:
    """Ping each seat through the seat queue, then assign the ones that answered; one that
    started and stayed silent is 'seat_failed', one still waiting for a slot is 'queued'."""
    lead = record["lead"]

    def one(seat: dict[str, Any]) -> dict[str, Any]:
        try:
            return api.ping(seat["conv_id"], _ping_text(record["id"], seat), PING_TIMEOUT_S,
                            later=lambda res, s=seat: _settle(s, res, api, lead))
        except Exception as exc:  # noqa: BLE001 - a failed seat is reported on the card, not raised
            return {"ok": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=max(1, min(8, len(seats)))) as pool:
        results = list(pool.map(one, seats))
    for seat, res in zip(seats, results):
        _settle(seat, res, api, lead)


def _save(record: dict[str, Any], api: Any) -> None:
    api.save_file(f"{record['dir']}/swarm.json", json.dumps(record, indent=1))


def build_swarm(preview: dict[str, Any] | None = None, api: Any = None,
                from_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build *preview* (sub plans added to the lead's plan) or *from_plan* (the lead's plan as
    it is: its sections are assigned, nothing is added). Validates first; any create that
    fails archives what was made and raises SwarmBuildError."""
    api = _Api(api or PanelSwarmApi())
    lead = api.caller()
    if not lead:
        raise SwarmBuildError("No calling chat: ducky_swarm_create must run in the lead's chat.")
    if from_plan is not None:
        tree = seats_from_plan(from_plan)
        return _build(str(from_plan.get("title") or "").strip() or "Swarm", tree, str(from_plan.get("id") or ""),
                      lead, api)
    return _build(preview["goal"], preview["tree"], "", lead, api, preview=preview)


def launch_plan(chat_id: str, api: Any = None) -> dict[str, Any]:
    """Seat every sub plan of *chat_id*'s plan that has a ``planned_assignee`` and no
    ``assignee`` (any depth): groups nested as the plan (reused when they exist), the planned
    ducky invited (or the planned chat moved in) with the section's Files lane, then pinged
    and assigned. Returns {seated: [names], failed, ...}; a second launch seats nothing."""
    api = _Api(api or PanelSwarmApi())
    plan = api.load_plan(chat_id) or {}
    tree = seats_from_plan(plan)
    known = list(walk_seats(tree))

    def keep(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for n in nodes:
            if "group" in n:
                n["seats"], n["groups"] = keep(n["seats"]), keep(n["groups"])
                if n["seats"] or n["groups"]:
                    out.append(n)
            elif n.get("planned_assignee") and not n.get("assignee"):
                if api.is_chat(n["planned_assignee"]):
                    n["existing"] = n["planned_assignee"]
                else:
                    n["profile"] = n["planned_assignee"]
                out.append(n)
        return out

    tree = keep(tree)
    if not tree:
        return {"ok": True, "seated": [], "failed": []}
    built = _build(str(plan.get("title") or "").strip() or "Swarm", tree, str(plan.get("id") or ""),
                   chat_id, api, known=known)
    # The Build card's 'Trust AI plugin drafts' grant: members' ducky_plugin_test never stops on needs_trust.
    api.trust_drafts([built["root_group_id"], *(g["group_id"] for g in walk_groups(tree) if g.get("group_id"))])
    return {"ok": True, "seated": [s["name"] for s in walk_seats(tree) if s.get("status") == "assigned"], **built}


def _build(goal: str, tree: list[dict[str, Any]], plan_id: str, lead: str, api: Any,
           preview: dict[str, Any] | None = None, known: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not list(walk_seats(tree)):
        raise SwarmBuildError("Nothing to seat: no section has steps.")
    errors = validate(tree, api.project_root())
    if errors:
        raise SwarmBuildError("; ".join(errors))
    for s in walk_seats(tree):
        if not s.get("model") and not s.get("existing"):
            try:
                s["model"] = api.seat_model(lead, s["profile"]) or ""
            except Exception:  # noqa: BLE001 - no lead model: the profile/default rules apply
                s["model"] = ""
    sections = {s["name"]: s for s in walk_seats(tree)}
    targets = {s["name"]: s for s in known or sections.values()}
    handovers = []
    for s in sections.values():
        h = s.get("handover")
        if h:
            to = targets.get(h["to"]) or next((x for x in targets.values() if x["sub_plan"].get("id") == h["to"]), None)
            if to is None:
                raise SwarmBuildError(f"{s['name']}: hands over to {h['to']!r}, which is no seat")
            handovers.append({"from_section": s["sub_plan"]["id"], "to_section": to["sub_plan"]["id"],
                              "paths": h["paths"]})

    made: list[str] = []      # chats and groups this build made (never a reused group or a moved-in chat)
    nodes: list[str] = []     # plan nodes a preview build added
    home = api.chat_home(lead)
    led = False
    try:
        root = api.group_create(goal)
        if not root.get("reused"):
            made.append(root["id"])
            api.add_leader(root["id"], lead)  # before any lane: only a leader may set them
            led = True
            api.rename_chat(lead, f"Lead: {goal}")
        out_dir = f"{SWARMS_DIR}/{root['id']}"
        for i, s in enumerate(sections.values(), 1):
            s["outputs"] = f"{out_dir}/outputs/{_slug(i, s['name'])}"
            if s["paths"] and api.project_root():
                # One sub-folder per seat: every seat may write outputs, no two own a file.
                s["paths"] = [*s["paths"], f"{s['outputs']}/**"]
        if preview is not None:
            api.ensure_plan(lead, goal)
            for seat in walk_seats(tree):
                job = preview["jobs"][seat["name"]]
                body = "\n".join(x for x in (job["purpose"], job["task"],
                                             f"Files: {', '.join(job['paths'])}" if job["paths"] else "",
                                             f"Outputs: {seat['outputs']}/", DONE_LINE) if x)
                sid = api.plan_add_node(lead, seat["sub_plan"]["title"], kind="subplan", body_markdown=body)
                nodes.append(sid)
                for step in seat["sub_plan"]["steps"]:  # exactly the previewed steps, edits included
                    nodes.append(api.plan_add_node(lead, step["content"], parent_id=sid,
                                                   body_markdown=step["body_markdown"]))
                seat["sub_plan"]["id"] = sid
        waiting: list[dict[str, Any]] = []
        ready: list[dict[str, Any]] = []

        def seat_in(group_id: str, seat: dict[str, Any]) -> None:
            if seat.get("start_after"):
                seat["status"] = "waiting"
                waiting.append({"after": seat["start_after"], "group_id": group_id, "seat": seat})
                return
            if seat.get("existing"):
                seat["conv_id"] = seat["existing"]
                api.add_member(group_id, seat["conv_id"])
                if seat["paths"]:
                    api.set_lane(group_id, seat["conv_id"], seat["paths"])
            else:
                seat["conv_id"] = api.invite(group_id, seat["profile"], seat["name"], seat["model"],
                                             seat["paths"] or None)
                made.append(seat["conv_id"])
            seat["created"] = True
            ready.append(seat)

        def make(node: dict[str, Any], parent_folder: str) -> None:
            if node.get("start_after"):
                for s in walk_seats([node]):
                    s["status"] = "waiting"
                waiting.append({"after": node["start_after"], "parent_folder": parent_folder, "group": node})
                return
            group = api.group_create(node["group"], parent_folder)
            if not group.get("reused"):
                made.append(group["id"])
            node["group_id"], node["folder_id"], node["created"] = group["id"], group["folder_id"], True
            for seat in node["seats"]:
                seat_in(group["id"], seat)
            for sub in node["groups"]:
                make(sub, group["folder_id"])

        for node in tree:
            if "group" in node:
                make(node, root["folder_id"])
            else:
                seat_in(root["id"], node)
        for group in walk_groups(tree):
            if group["muted"] and group.get("group_id"):
                api.set_muted(group["group_id"], True)
        old = next((r for r in api.read_records() if r.get("id") == root["id"]), {})
        record = {"id": root["id"], "dir": out_dir, "goal": goal, "lead": lead, "plan_id": plan_id or lead,
                  "root_group_id": root["id"], "root_folder_id": root["folder_id"], "outputs": f"{out_dir}/outputs",
                  "handovers": [*(old.get("handovers") or []), *handovers],
                  "waiting": [*(old.get("waiting") or []), *waiting]}
        api.save_file(f"{out_dir}/outputs/README.md",
                      f"# {goal}\n\nDeliverables of this swarm, one folder per seat. Kept when the group is archived.\n")
        _save(record, api)
    except Exception as exc:
        left = _roll_back(api, lead, home if led else None, made, nodes)
        raise SwarmBuildError(f"{exc}" + (f" (could not archive: {', '.join(left)})" if left else ""),
                              created=len(left)) from exc
    _start_seats(ready, api, record)
    return {"root_group_id": root["id"], "lead": lead, "tree": tree, "swarm_id": record["id"],
            "outputs": record["outputs"], "handovers": handovers,
            "failed": [s["error"] for s in ready if s.get("status") == "seat_failed"],
            "waiting": [w["after"] for w in waiting]}

def _roll_back(api: Any, lead: str, home: dict[str, str] | None, made: list[str], nodes: list[str]) -> list[str]:
    """Archive everything *made*, newest first, lead put back first. Returns what stayed."""
    left = []
    if home is not None:
        try:
            api.restore_home(lead, home)
        except Exception:  # noqa: BLE001 - archive the rest anyway
            pass
    for cid in reversed(made):
        try:
            api.archive(cid)
        except Exception:  # noqa: BLE001
            left.append(cid)
    for nid in reversed(nodes):
        try:
            api.plan_delete_node(lead, nid)
        except Exception:  # noqa: BLE001 - a leftover plan node is visible and harmless
            pass
    return left


# ---- after the build ----------------------------------------------------------------------

def on_step_completed(plan_id: str, step_id: str, api: Any = None) -> list[str]:
    """Seat the groups and seats waiting on *step_id* in *plan_id*'s swarms: invite, ping,
    assign. Returns the names of the seats started."""
    api = _Api(api or PanelSwarmApi())
    started: list[str] = []
    for record in api.read_records():
        if record.get("plan_id") != plan_id:
            continue
        due = [w for w in record.get("waiting") or [] if w.get("after") == step_id]
        if not due:
            continue
        record["waiting"] = [w for w in record["waiting"] if w.get("after") != step_id]
        ready: list[dict[str, Any]] = []

        def invite(group_id: str, seat: dict[str, Any]) -> None:
            seat["conv_id"] = api.invite(group_id, seat["profile"], seat["name"], seat["model"], seat["paths"] or None)
            seat["created"], seat["start_after"] = True, ""
            ready.append(seat)

        def make(node: dict[str, Any], parent_folder: str) -> None:
            group = api.group_create(node["group"], parent_folder)
            node["group_id"], node["start_after"] = group["id"], ""
            for seat in node["seats"]:
                invite(group["id"], seat)
            for sub in node["groups"]:
                make(sub, group["folder_id"])
            if node.get("muted"):
                api.set_muted(group["id"], True)

        for w in due:
            if "group" in w:
                make(w["group"], w["parent_folder"])
            else:
                invite(w["group_id"], w["seat"])
        _start_seats(ready, api, record)
        _save(record, api)
        started += [s["name"] for s in ready if s.get("status") == "assigned"]
    return started


def handovers_for(section_id: str, api: Any = None) -> list[dict[str, Any]]:
    """The ``Hands over:`` rows whose giver is *section_id*: [{from_section, to_section, paths}]."""
    api = _Api(api or PanelSwarmApi())
    return [h for r in api.read_records() for h in r.get("handovers") or [] if h.get("from_section") == section_id]
