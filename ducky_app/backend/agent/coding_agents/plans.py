"""Plans — outline trees (main → subplans) for templates and project instances."""

from __future__ import annotations

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from pathlib import Path
from typing import Any

from backend.agent.coding_agents import plan_hooks
# Moved out of this file; re-exported so every `plans.<name>` import keeps working.
from backend.agent.coding_agents.plan_hooks import _push_assignment_invalidated, _section_audience  # noqa: F401
from backend.agent.coding_agents.plan_review import (  # noqa: F401
    _BIG_STEP_RE,
    _REVIEW_MAX,
    _TITLE_MAX,
    PLAN_PROTOCOL,
    plan_review,
    with_plan_review,
)
from backend.agent.coding_agents.plan_ticks import attach_next_tick, format_plan_tick_nudge, next_tick_dict  # noqa: F401
from frontend.atomic_json import write_json_atomic
from frontend.settings import PanelSettings, default_app_data_dir

_NODE_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})
_DONE = frozenset({"completed", "cancelled"})
_NODE_KINDS = frozenset({"step", "subplan"})
_STARTED_NODE = frozenset({"in_progress", "completed", "cancelled"})

# Agents sometimes dump sibling XML tool params into ``overview`` (wrong close
# tag / slurped ``<parameter name="nodes">…``). Recover the tree + scrub prose.
_OVERVIEW_TOOL_XML_RE = re.compile(
    r"(?:</overview>\s*)?"
    r"(?:<parameter\s+name=[\"']merge[\"']>.*?</parameter>\s*)?"
    r"<parameter\s+name=[\"']nodes[\"']>\s*(.*)\s*(?:</parameter>\s*)?\s*$",
    re.DOTALL | re.IGNORECASE,
)
_OVERVIEW_STRAY_CLOSE_RE = re.compile(r"</overview>\s*$", re.IGNORECASE)


def _safe_id(raw: str, *, fallback: str = "") -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (raw or "").strip())[:80]
    return safe or fallback


def _resolve_project_root(project_root: str | None = None) -> str:
    if project_root is None:
        return (PanelSettings.load().uefn_project_root or "").strip()
    return (project_root or "").strip()


_EDIT_LOCK_WAIT_S = 30.0


def _serialize_plan_edit(edit):
    """Serialize read/validate/modify/save, not just the final write: one plan's edits run one
    at a time across threads (keyed reentrant lock) and across processes (OS file lock).

    Every seat's Ducky tools run in their own bridge process: in a 4-seat live test (Oct 2026)
    ticks made at the same moment saved over each other and some were lost. This is not
    conflict detection for callers that save a whole stale document.
    """
    @wraps(edit)
    def locked(chat_id, *args, **kwargs):
        from backend.util.file_lock import held_file_lock
        from frontend.ui_web.project_chats import _conversation_lock

        template_id = (kwargs.get("template_id") or "").strip()
        if template_id:
            key = f"plan-template:{_templates_dir(create=False).resolve()}:{_safe_id(template_id)}"
        else:
            root = _resolve_project_root(kwargs.get("project_root"))
            # Resolve the default once so a project switch cannot split the key
            # from the document actually loaded/saved during this mutation.
            kwargs["project_root"] = root
            if edit.__name__ in _STEP_EDITS:
                # Before the lock, so the edit holds the key of the plan it really changes.
                step = kwargs.get("node_id") or (args[0] if args else "") or kwargs.get("parent_id") or ""
                chat_id = _edit_target(chat_id, str(step), root)
            key = f"plan:{_project_id(root)}:{_safe_id(chat_id)}"
        file_key = "plan-" + _safe_id(key.split(":", 1)[1].replace(":", "-"))[:120]
        with _conversation_lock(key), held_file_lock(file_key, _EDIT_LOCK_WAIT_S) as got:
            if not got:
                raise ValueError("another seat is saving this plan right now: send the same edit again")
            return edit(chat_id, *args, **kwargs)
    return locked


# Step edits a team member makes in its team's plan without naming the plan's chat.
_STEP_EDITS = frozenset({"add_node", "update_node", "delete_node", "move_node"})


def _edit_target(chat_id: str, step_id: str, project_root: str | None) -> str:
    """The chat whose plan a step edit lands in: its own plan, else its team's plan.

    Members ticked their steps without the lead's chat id, got "plan not found — call
    ducky_create_plan first", and then made private plans the team never saw.
    """
    cid = (chat_id or "").strip()
    if not cid:
        return cid
    own = load_plan(cid, project_root)
    sid = (step_id or "").strip()
    if own is not None and (not sid or _has_step(own, sid)):
        return cid
    try:
        team = team_plan_for(cid, project_root)
    except Exception:
        team = None
    if team is not None and (not sid or _has_step(team, sid)):
        return str(team.get("chat_id") or cid)
    return cid


def _has_step(plan: dict[str, Any], step_id: str) -> bool:
    return any(str(n.get("id")) == step_id for n in _flatten_nodes(plan.get("nodes")))


def _no_plan_error(chat_id: str, project_root: str | None) -> ValueError:
    """"No plan" said so a team member edits its team's plan instead of making its own."""
    try:
        team = team_plan_for(chat_id, project_root)
    except Exception:
        team = None
    if team is None:
        return ValueError("plan not found — call ducky_create_plan first")
    return ValueError(
        f"This chat has no plan of its own: it works in its team's plan “{team.get('title') or 'Plan'}” "
        f"(coordinator chat {team.get('chat_id')}). Tick or annotate your steps with "
        "ducky_plan_update_node(node_id=<step id>); ducky_get_plan lists them. Never make a second plan."
    )


def _plans_dir(project_root: str | None = None, *, create: bool = True) -> Path:
    """Project plans directory (requires a project root)."""
    root = _resolve_project_root(project_root)
    if not root:
        raise ValueError("project_root required for project plans")
    d = Path(root) / ".ducky" / "plans"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def plans_root(project_root: str | None = None) -> Path:
    return _plans_dir(project_root, create=True)


def _templates_dir(*, create: bool = True) -> Path:
    d = default_app_data_dir() / "plan_templates"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _use_db() -> bool:
    from backend.store.switch import use_db

    return use_db("plans")


def _repo(project_root: str | None):
    """The plans repo, after folding this project's legacy .ducky/plans once and moving
    chat plans into the one list (a ducky's plan follows the ducky, not a project)."""
    from backend.store.importers import phase4
    from backend.store.repos import plans as repo

    root = _resolve_project_root(project_root)
    imported = phase4.ensure_project(root) if root else None
    repo.merge_into_one_list(again=bool(imported))
    return repo


def _project_id(project_root: str | None) -> str:
    """Where chat plans are stored: the one list, whatever project is selected."""
    del project_root
    from backend.store.repos.plans import ONE_LIST_PROJECT

    return ONE_LIST_PROJECT


def _plan_path(chat_id: str, project_root: str | None = None) -> Path:
    safe = _safe_id(chat_id)
    if not safe:
        raise ValueError("chat_id required")
    return plans_root(project_root) / f"{safe}.json"


def _template_path(template_id: str) -> Path:
    safe = _safe_id(template_id)
    if not safe:
        raise ValueError("template_id required")
    return _templates_dir(create=True) / f"{safe}.json"


def _project_display_name(project_root: str) -> str:
    root = (project_root or "").strip()
    if not root:
        return "No project"
    try:
        return Path(root).name or root
    except OSError:
        return root


def _normalize_status(raw: Any) -> str:
    status = str(raw or "pending").strip().lower()
    return status if status in _NODE_STATUSES else "pending"


def _normalize_kind(raw: Any, *, children: list[dict[str, Any]]) -> str:
    kind = str(raw or "").strip().lower()
    if kind in _NODE_KINDS:
        return kind
    return "subplan" if children else "step"


def _normalize_node(raw: Any, *, fallback_id: str | None = None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    content = str(raw.get("content") or "").strip()
    if not content:
        return None
    nid = str(raw.get("id") or fallback_id or uuid.uuid4().hex[:10]).strip()
    children_raw = raw.get("children")
    children: list[dict[str, Any]] = []
    if isinstance(children_raw, list):
        for child in children_raw:
            n = _normalize_node(child)
            if n:
                children.append(n)
    body = str(raw.get("body_markdown") or "").strip()
    node = {
        "id": nid,
        "content": content[:500],
        "status": _normalize_status(raw.get("status")),
        "kind": _normalize_kind(raw.get("kind"), children=children),
        "body_markdown": body,
        "children": children,
    }
    assignee = str(raw.get("assignee") or "").strip()
    if assignee:
        node["assignee"] = assignee  # exact chat or group identity
    planned = str(raw.get("planned_assignee") or "").strip()
    if planned:
        node["planned_assignee"] = planned  # who launching the plan seats here; starts nobody
    after = _normalize_after(raw.get("after"), own_id=nid)
    if after:
        node["after"] = after  # steps that must finish before this one starts
    return node


_NOTES_MAX = 2048  # bytes of notes kept on a step; the whole text goes to a linked note file


def _cap_notes(doc: dict[str, Any], node: dict[str, Any], text: str | None, project_root: str | None) -> str:
    """A step's notes, at most ~2 KB: longer notes are saved whole as a note file and linked.

    A swarm plan reached 151K characters from logs pasted into step notes, and every read
    of the plan then cost the lead's context.
    """
    text = (text or "").strip()
    if len(text.encode("utf-8")) <= _NOTES_MAX or doc.get("kind") == "template":
        return text
    plan_dir = _safe_id(str(doc.get("id") or ""), fallback="plan")
    name = f"{_safe_id(str(node.get('id') or ''), fallback='step')}.md"
    root = _resolve_project_root(project_root)
    rel = Path(".ducky") / "plans" / plan_dir / "notes" / name
    path = Path(root) / rel if root else default_app_data_dir() / "plan_notes" / plan_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    link = f"\n\n…full notes: {rel.as_posix() if root else path.as_posix()}"
    head = text.encode("utf-8")[:_NOTES_MAX - len(link.encode("utf-8"))].decode("utf-8", "ignore")
    return head.rstrip() + link


def _normalize_after(raw: Any, *, own_id: str = "") -> list[str]:
    """Step ids this step waits for: a list (or one id / comma list), deduped, never itself."""
    if isinstance(raw, str):
        raw = [part for part in raw.split(",")]
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        sid = str(item or "").strip()
        if sid and sid != own_id and sid not in out:
            out.append(sid)
    return out[:20]


def waiting_on(plan: dict[str, Any] | None, node: dict[str, Any]) -> list[dict[str, Any]]:
    """The unfinished steps ``node`` waits for (its ``after`` ids), with their owners."""
    after = list(node.get("after") or [])
    if not after:
        return []
    by_id = {str(n.get("id")): n for n in _flatten_nodes((plan or {}).get("nodes"))}
    out = []
    for sid in after:
        pre = by_id.get(sid)
        if pre is None or str(pre.get("status") or "") in _DONE:
            continue
        out.append({"id": sid, "content": str(pre.get("content") or ""),
                    "status": str(pre.get("status") or ""), "assignee": str(pre.get("assignee") or "")})
    return out


def _parse_nodes_json(raw: Any) -> list[Any] | None:
    """Return a list if ``raw`` is a list or a JSON-encoded list string."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def _normalize_nodes(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    parsed = _parse_nodes_json(raw)
    if parsed is None:
        return out
    for item in parsed:
        node = _normalize_node(item)
        if node:
            out.append(node)
    return out


def scrub_overview_and_recover_nodes(
    overview: str | None,
    nodes: Any = None,
) -> tuple[str, Any, bool]:
    """Split mangled tool XML out of overview; recover ``nodes`` when possible.

    Returns ``(clean_overview, nodes_or_recovered, recovered)``.
    Prefer an explicit non-empty ``nodes`` list/JSON-string; otherwise parse
    ``<parameter name="nodes">…`` that was accidentally written into overview.
    """
    text = str(overview or "")
    coerced = _parse_nodes_json(nodes)
    has_nodes = isinstance(coerced, list) and len(coerced) > 0
    match = _OVERVIEW_TOOL_XML_RE.search(text)
    if match:
        clean = text[: match.start()].rstrip()
        clean = _OVERVIEW_STRAY_CLOSE_RE.sub("", clean).rstrip()
        if has_nodes:
            return clean, coerced, False
        recovered = _parse_nodes_json(match.group(1))
        if recovered is not None:
            return clean, recovered, True
        return clean, nodes, False
    # Soft scrub: stray </overview> with no parameter dump
    if _OVERVIEW_STRAY_CLOSE_RE.search(text):
        text = _OVERVIEW_STRAY_CLOSE_RE.sub("", text).rstrip()
    if coerced is not None:
        return text, coerced, False
    return text, nodes, False


def _todos_to_nodes(todos: Any) -> list[dict[str, Any]]:
    """Legacy flat todos → root nodes (no children)."""
    out: list[dict[str, Any]] = []
    if not isinstance(todos, list):
        return out
    for item in todos:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        out.append(
            {
                "id": str(item.get("id") or uuid.uuid4().hex[:10]).strip(),
                "content": content[:500],
                "status": _normalize_status(item.get("status")),
                "kind": "step",
                "body_markdown": "",
                "children": [],
            }
        )
    return out


def _reset_node_statuses(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in nodes:
        kids = _reset_node_statuses(list(n.get("children") or []))
        out.append(
            {
                "id": str(n.get("id") or uuid.uuid4().hex[:10]),
                "content": str(n.get("content") or "")[:500],
                "status": "pending",
                "kind": _normalize_kind(n.get("kind"), children=kids),
                "body_markdown": str(n.get("body_markdown") or "").strip(),
                "children": kids,
            }
        )
    return out


def plan_is_paused(plan: dict[str, Any] | None) -> bool:
    return isinstance(plan, dict) and str(plan.get("status") or "").strip().lower() == "paused"


def plan_structure_locked(plan: dict[str, Any] | None) -> bool:
    """True when human structure/content edits are blocked (playing after start, or finished).

    Pause unlocks unfinished work; completed nodes stay frozen separately.
    """
    if not isinstance(plan, dict):
        return False
    st = str(plan.get("status") or "").strip().lower()
    if st == "finished":
        return True
    if st == "paused":
        return False
    for n in _flatten_nodes(plan.get("nodes")):
        if str(n.get("status") or "") in _STARTED_NODE:
            return True
    return False


def _refuse_if_structure_locked(plan: dict[str, Any]) -> None:
    if plan_structure_locked(plan):
        raise ValueError("plan is playing — pause it to edit, move or delete unfinished steps "
                         "(adding a step needs no pause)")


def _unstarted(node: dict[str, Any]) -> bool:
    """``node`` and everything inside it are still pending."""
    return all(str(n.get("status") or "pending") == "pending"
               for n in [node, *_flatten_nodes(node.get("children") or [])])


def _is_done_node(node: dict[str, Any] | None) -> bool:
    return isinstance(node, dict) and str(node.get("status") or "") in _DONE


def _refuse_if_done_node(node: dict[str, Any], *, action: str = "edit") -> None:
    if _is_done_node(node):
        raise ValueError(f"completed steps can't be {action}ed")


def _assert_completed_tree_preserved(
    old_nodes: list[dict[str, Any]] | None,
    new_nodes: list[dict[str, Any]] | None,
) -> None:
    """Completed nodes must keep content/status/kind/body and child id order."""
    old_flat = {str(n.get("id")): n for n in _flatten_nodes(old_nodes)}
    new_flat = {str(n.get("id")): n for n in _flatten_nodes(new_nodes)}
    for oid, on in old_flat.items():
        if not _is_done_node(on):
            continue
        nn = new_flat.get(oid)
        if nn is None:
            raise ValueError("completed steps can't be removed")
        for key in ("content", "status", "kind", "body_markdown"):
            if str(on.get(key) or "") != str(nn.get(key) or ""):
                raise ValueError("completed steps can't be edited")
        old_kids = [str(c.get("id")) for c in (on.get("children") or []) if isinstance(c, dict)]
        new_kids = [str(c.get("id")) for c in (nn.get("children") or []) if isinstance(c, dict)]
        if old_kids != new_kids:
            raise ValueError("completed steps can't be restructured")


def _descendants_done(node: dict[str, Any]) -> bool:
    for child in node.get("children") or []:
        if not isinstance(child, dict):
            continue
        if str(child.get("status")) not in _DONE:
            return False
        if not _descendants_done(child):
            return False
    return True


def _rollup_completed_parents(nodes: list[dict[str, Any]] | None) -> None:
    """Mark a subplan completed when every child (and descendant) is done.

    Empty subplans stay pending. Already-cancelled parents are left alone.
    """

    def walk(n: dict[str, Any]) -> None:
        kids = [c for c in (n.get("children") or []) if isinstance(c, dict)]
        for child in kids:
            walk(child)
        if not kids or str(n.get("status")) in _DONE:
            return
        if all(str(c.get("status")) in _DONE and _descendants_done(c) for c in kids):
            n["status"] = "completed"

    for node in nodes or []:
        if isinstance(node, dict):
            walk(node)


# A Verify step starts with the word (after any numbering or markdown). A build step
# that merely mentions "verification state" in its done-when text is not one.
_VERIFY_STEP = re.compile(r"^[\W\d_]*(?:verify|verification)\b", re.IGNORECASE)


def is_verify_leaf(node: dict[str, Any] | None) -> bool:
    """True for a leaf whose id or content names a Verify step."""
    if not isinstance(node, dict):
        return False
    kids = node.get("children") or []
    if isinstance(kids, list) and kids:
        return False
    if "verify" in str(node.get("id") or "").lower():
        return True
    return bool(_VERIFY_STEP.match(str(node.get("content") or "")))


def _apply_status_gate(node: dict[str, Any], status: str, chat_id: str = "", *, by_user: bool = False,
                       owners: set[str] | None = None, project_root: str | None = None) -> str:
    """Refuse completed unless descendants are done and Verify leaves have evidence.

    *by_user*: the user ticked it in the plan view; their word is the evidence.
    *owners*: the assignees on the step and the sections above it; a chat among them (or in
    one of those groups) ticks with its own checks, though the plan belongs to its lead."""
    st = _normalize_status(status)
    if st == "completed" and not _descendants_done(node):
        raise ValueError("cannot complete a subplan while nested subplans are unfinished")
    if st == "completed" and not by_user and is_verify_leaf(node):
        from backend.agent.verify_evidence import has_evidence

        if not has_evidence(chat_id) and not _owner_has_evidence(owners or set(), project_root):
            raise ValueError(
                "cannot complete a Verify leaf until a check tool returned ok this turn: run one now, "
                "then tick it in the same turn (ducky_plugin_validate or ducky_plugin_test for a plugin, "
                "ducky_terminal_run with passing tests, workspace_compile_verse for Verse; with UEFN "
                "closed, workspace_list_verse_errors)"
            )
    return st


def _owner_has_evidence(owners: set[str], project_root: str | None) -> bool:
    """The ticking chat owns this step (itself or through its group) and ran a check this turn."""
    from backend.agent.verify_evidence import current_conversation, has_evidence

    actor = current_conversation()
    if not actor or not owners:
        return False
    return bool(owners & set(_chat_and_group_ids(actor, project_root))) and has_evidence(actor)


def _path_to(roots: list[dict[str, Any]], node_id: str) -> list[dict[str, Any]]:
    """``node_id`` and every section above it, outermost first ([] when absent)."""
    def path(items: Any, trail: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            here = [*trail, item]
            if str(item.get("id")) == node_id:
                return here
            found = path(item.get("children"), here)
            if found is not None:
                return found
        return None

    return path(roots, []) or []


def _owners_of(roots: list[dict[str, Any]], node_id: str) -> set[str]:
    """Assignees on ``node_id`` and on every section above it."""
    return {str(n.get("assignee")) for n in _path_to(roots, node_id) if n.get("assignee")}


def _walk_find(
    nodes: list[dict[str, Any]], node_id: str
) -> tuple[list[dict[str, Any]] | None, int, dict[str, Any] | None]:
    """Return (sibling_list, index, node) or (None, -1, None). Mutates via live lists."""
    nid = (node_id or "").strip()
    for i, n in enumerate(nodes):
        if str(n.get("id")) == nid:
            return nodes, i, n
        if not isinstance(n.get("children"), list):
            n["children"] = []
        found_list, found_i, found = _walk_find(n["children"], nid)
        if found is not None:
            return found_list, found_i, found
    return None, -1, None


def _contains_id(nodes: list[dict[str, Any]], node_id: str) -> bool:
    _, _, found = _walk_find(nodes, node_id)
    return found is not None


def _flatten_nodes(nodes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Depth-first flat list for progress / legacy todos view."""
    out: list[dict[str, Any]] = []

    def walk(items: list[dict[str, Any]]) -> None:
        for n in items:
            out.append(n)
            kids = n.get("children") or []
            if kids:
                walk(list(kids))

    walk(list(nodes or []))
    return out


def outline_numbers(nodes: list[dict[str, Any]] | None) -> list[tuple[str, dict[str, Any]]]:
    """Return (outline_label, node) pairs in tree order, e.g. 1, 1.1, 1.1.1, 2."""
    out: list[tuple[str, dict[str, Any]]] = []

    def walk(items: list[dict[str, Any]], prefix: str) -> None:
        for i, n in enumerate(items, start=1):
            label = f"{prefix}{i}" if not prefix else f"{prefix}.{i}"
            out.append((label, n))
            kids = list(n.get("children") or [])
            if kids:
                walk(kids, label)

    walk(list(nodes or []), "")
    return out


def _normalize_plan_doc(data: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Migrate legacy todos/parent_chat_id into nodes tree.

    Also heals plans where agents dumped ``nodes`` XML into ``overview``.
    """
    doc = dict(data)
    doc.pop("parent_chat_id", None)
    overview, nodes_raw, recovered = scrub_overview_and_recover_nodes(
        doc.get("overview"), doc.get("nodes")
    )
    doc["overview"] = overview
    existing = doc.get("nodes")
    has_tree = isinstance(existing, list) and bool(existing)
    if recovered or (not has_tree and nodes_raw is not None):
        nodes = _normalize_nodes(nodes_raw)
    elif not has_tree and doc.get("todos"):
        nodes = _todos_to_nodes(doc.get("todos"))
    else:
        nodes = _normalize_nodes(existing if isinstance(existing, list) else nodes_raw)
    _rollup_completed_parents(nodes)
    doc["nodes"] = nodes
    # Compat shim: flat mirror of all outline nodes for older UI/tool paths.
    doc["todos"] = [
        {"id": n["id"], "content": n["content"], "status": n["status"]} for n in _flatten_nodes(nodes)
    ]
    doc["kind"] = kind
    doc["template_id"] = str(doc.get("template_id") or "").strip() or None
    doc["title"] = str(doc.get("title") or "Plan").strip()[:200] or "Plan"
    doc["body_markdown"] = str(doc.get("body_markdown") or "").strip()
    doc["status"] = str(doc.get("status") or "open").strip()[:40] or "open"
    return doc


def _roll_plan_status(plan: dict[str, Any]) -> None:
    nodes = list(plan.get("nodes") or [])
    if not nodes:
        return
    if all(str(n.get("status")) in _DONE and _descendants_done(n) for n in nodes):
        plan["status"] = "finished"
    elif str(plan.get("status") or "") == "finished":
        plan["status"] = "open"


def load_plan(chat_id: str, project_root: str | None = None) -> dict[str, Any] | None:
    if _use_db():
        return _load_plan_row(chat_id, project_root)
    try:
        path = _plan_path(chat_id, project_root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        before_ov = str(data.get("overview") or "")
        before_nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
        doc = _normalize_plan_doc(data, kind="project")
        healed = (not before_nodes and bool(doc.get("nodes"))) or (
            "<parameter" in before_ov.lower() and "<parameter" not in str(doc.get("overview") or "").lower()
        )
        if healed:
            try:
                return save_plan(doc, project_root)
            except ValueError:
                return doc
        _rollup_completed_parents(doc.get("nodes"))
        return doc
    except (OSError, json.JSONDecodeError):
        return None


def _load_plan_row(chat_id: str, project_root: str | None) -> dict[str, Any] | None:
    safe = _safe_id(chat_id)
    if not safe:
        return None
    try:
        project_id = _project_id(project_root)
    except ValueError:
        return None
    data = _repo(project_root).plan_get(project_id, safe)
    if not isinstance(data, dict):
        return None
    before_ov = str(data.get("overview") or "")
    before_nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    doc = _normalize_plan_doc(data, kind="project")
    healed = (not before_nodes and bool(doc.get("nodes"))) or (
        "<parameter" in before_ov.lower() and "<parameter" not in str(doc.get("overview") or "").lower()
    )
    if healed:
        try:
            return save_plan(doc, project_root)
        except ValueError:
            return doc
    _rollup_completed_parents(doc.get("nodes"))
    return doc


def save_plan(plan: dict[str, Any], project_root: str | None = None) -> dict[str, Any]:
    plan = _normalize_plan_doc(dict(plan), kind="project")
    chat_id = str(plan.get("chat_id") or "").strip()
    if not chat_id:
        raise ValueError("chat_id required")
    if not _use_db() and not _resolve_project_root(project_root):
        raise ValueError("project_root required for project plans")
    from frontend.ui_web.project_chats import load_conversation_meta

    # Read without load_plan's repair-and-save path: saving a healed document
    # must not recursively try to heal the previous stored version again.
    if _use_db():
        previous = _repo(project_root).plan_get(_project_id(project_root), _safe_id(chat_id)) or {}
    else:
        try:
            previous = json.loads(_plan_path(chat_id, project_root).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    previous = _normalize_plan_doc(previous if isinstance(previous, dict) else {}, kind="project")
    previous_assignments = {n["id"]: n.get("assignee") for n in _flatten_nodes(previous.get("nodes"))}
    for node in _flatten_nodes(plan.get("nodes")):
        assignee = node.get("assignee")
        if assignee and previous_assignments.get(node["id"]) != assignee:
            # A sub plan only starts once its steps exist; a plain step is assignable as is.
            if node.get("kind") == "subplan" and not node.get("children"):
                raise ValueError(f"sub plan `{node['id']}` has no steps. Add its steps first, then assign it.")
            if load_conversation_meta(assignee, project_root=project_root) is None:
                raise ValueError("assignee must identify an existing chat or group")
    plan["updated_at"] = time.time()
    _rollup_completed_parents(plan.get("nodes"))
    _roll_plan_status(plan)
    reports = plan_hooks.completion_reports(previous, plan)
    previous_nodes = {n["id"]: n for n in _flatten_nodes(previous.get("nodes"))}
    started = any(n.get("status") == "in_progress" and
                  previous_nodes.get(n["id"], {}).get("status") != "in_progress"
                  for n in _flatten_nodes(plan.get("nodes")))
    pending = [] if started else list(previous.get("team_reports") or [])
    plan["team_reports"] = (pending + reports)[-20:]
    if _use_db():
        _repo(project_root).plan_put(_project_id(project_root), _safe_id(chat_id), "project", plan)
    else:
        write_json_atomic(_plan_path(chat_id, project_root), plan)
    # Who to tell reads the project's chat list: off the call path, so a tick returns at once.
    _after_save(_push_assignment_invalidated, previous, plan, project_root)
    plan_hooks.after_save(previous, plan, reports, project_root)
    return plan


def create_plan(
    chat_id: str,
    *,
    title: str = "",
    overview: str = "",
    body_markdown: str = "",
    nodes: Any = None,
    todos: list[dict[str, Any]] | None = None,
    template_id: str | None = None,
    project_root: str | None = None,
    team_check: bool = True,
) -> dict[str, Any]:
    """``team_check``: refuse a second plan for a chat on its team's plan (the person may)."""
    cid = (chat_id or "").strip()
    if not cid:
        raise ValueError("chat_id required")
    if team_check:
        _refuse_if_on_team_plan(cid, project_root)
        _refuse_if_lead_has_no_plan(cid, project_root)
    overview, nodes, _ = scrub_overview_and_recover_nodes(overview, nodes)
    if nodes is not None:
        tree = _normalize_nodes(nodes)
    else:
        tree = _todos_to_nodes(todos or [])
    now = time.time()
    plan = {
        "id": uuid.uuid4().hex[:12],
        "kind": "project",
        "chat_id": cid,
        "title": (title or "Plan").strip()[:200],
        "overview": (overview or "").strip(),
        "body_markdown": (body_markdown or "").strip(),
        "nodes": tree,
        "template_id": (template_id or "").strip() or None,
        "created_at": now,
        "updated_at": now,
        "status": "open",
    }
    return save_plan(plan, project_root)


@_serialize_plan_edit
def update_plan(
    chat_id: str,
    *,
    title: str | None = None,
    overview: str | None = None,
    body_markdown: str | None = None,
    nodes: Any = None,
    todos: list[dict[str, Any]] | None = None,
    merge: bool = True,
    status: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Update a project plan. merge=True merges root/flat todos by id when todos= is used."""
    cid = (chat_id or "").strip()
    if not cid:
        raise ValueError("chat_id required")
    plan = load_plan(cid, project_root)
    if not plan:
        raise _no_plan_error(cid, project_root)

    # Heal mangled overview↔nodes before deciding what touched structure.
    if overview is not None or nodes is not None:
        base_overview = overview if overview is not None else str(plan.get("overview") or "")
        clean, recovered_nodes, recovered = scrub_overview_and_recover_nodes(base_overview, nodes)
        if overview is not None or recovered:
            overview = clean
        if nodes is not None or recovered:
            nodes = recovered_nodes

    # Status ticks (todos merge / plan status) stay allowed while playing;
    # pause unlocks structure + prose. Finished stays locked.
    content_touch = title is not None or overview is not None or body_markdown is not None or nodes is not None
    if content_touch:
        _refuse_if_structure_locked(plan)

    if title is not None:
        plan["title"] = (title or "Plan").strip()[:200]
    if overview is not None:
        plan["overview"] = (overview or "").strip()
    if body_markdown is not None:
        plan["body_markdown"] = (body_markdown or "").strip()
    if status is not None:
        next_status = (status or "open").strip()[:40]
        if str(plan.get("status") or "").strip().lower() == "finished" and next_status != "finished":
            raise ValueError("finished plans can't be resumed — duplicate to start a fresh copy")
        plan["status"] = next_status

    if nodes is not None:
        normalized = _normalize_nodes(nodes)
        _assert_completed_tree_preserved(plan.get("nodes"), normalized)
        plan["nodes"] = normalized
    elif todos is not None:
        incoming = _todos_to_nodes(todos)
        if not merge:
            plan["nodes"] = incoming
        else:
            # Merge by id across flattened tree; keep tree structure, update matching nodes.
            flat = {str(n["id"]): n for n in _flatten_nodes(plan.get("nodes"))}
            for item in incoming:
                tid = str(item["id"])
                if tid in flat:
                    try:
                        item["status"] = _apply_status_gate(
                            flat[tid], item["status"], chat_id,
                            owners=_owners_of(plan.get("nodes") or [], tid), project_root=project_root)
                    except ValueError:
                        item["status"] = str(flat[tid].get("status") or "pending")
                    flat[tid]["content"] = item["content"]
                    flat[tid]["status"] = item["status"]
                else:
                    roots = list(plan.get("nodes") or [])
                    roots.append(item)
                    plan["nodes"] = roots

    return save_plan(plan, project_root)


@_serialize_plan_edit
def add_node(
    chat_id: str,
    *,
    content: str,
    parent_id: str = "",
    index: int | None = None,
    status: str = "pending",
    kind: str = "",
    body_markdown: str = "",
    project_root: str | None = None,
    template_id: str | None = None,
    assignee: str = "",
    after: list[str] | str | None = None,
) -> dict[str, Any]:
    """Add a step/subplan node. Pass template_id to edit a template instead of a project plan.

    A new step may join a playing plan (it changes no step already there), so a team's lead
    adds the fix for a blocker, and a member splits its own step, without pausing the team.
    """
    node = _normalize_node(
        {
            "id": uuid.uuid4().hex[:10],
            "content": content,
            "status": status,
            "kind": kind,
            "body_markdown": body_markdown,
            "children": [],
            "assignee": assignee,
        }
    )
    if not node:
        raise ValueError("content required")
    doc, save = _load_editable(chat_id, project_root=project_root, template_id=template_id)
    node["body_markdown"] = _cap_notes(doc, node, node.get("body_markdown"), project_root)
    playing = doc.get("kind") != "template" and plan_structure_locked(doc)
    if playing and str(doc.get("status") or "").strip().lower() == "finished":
        _refuse_if_structure_locked(doc)
    roots = list(doc.get("nodes") or [])
    if after is not None:
        _set_after(doc, node, after)
    parent = (parent_id or "").strip()
    if not parent:
        idx = len(roots) if index is None else max(0, min(int(index), len(roots)))
        roots.insert(idx, node)
        doc["nodes"] = roots
    else:
        sibs, _, parent_node = _walk_find(roots, parent)
        if parent_node is None:
            raise ValueError("parent node not found")
        # A fix for a finished section goes back to the same seat: the section (and any finished
        # section above it) is in progress again, so its owner is woken; its old steps stay frozen.
        # Every follow-up as a new top-level section grew one plan to 22 sections.
        reopen = str(parent_node.get("status")) == "completed" and bool(parent_node.get("children"))
        if playing and _is_done_node(parent_node) and not reopen:
            raise ValueError("completed steps stay frozen while the plan plays: add the new step beside it")
        kids = list(parent_node.get("children") or [])
        idx = len(kids) if index is None else max(0, min(int(index), len(kids)))
        kids.insert(idx, node)
        parent_node["children"] = kids
        if reopen:
            for up in _path_to(roots, parent):
                if str(up.get("status")) == "completed":
                    up["status"] = "in_progress"
        elif str(parent_node.get("status")) == "completed":
            parent_node["status"] = "pending"
        if str(parent_node.get("kind") or "") == "step":
            parent_node["kind"] = "subplan"
        doc["nodes"] = roots
    return save(doc)


@_serialize_plan_edit
def update_node(
    chat_id: str,
    node_id: str,
    *,
    content: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    body_markdown: str | None = None,
    project_root: str | None = None,
    template_id: str | None = None,
    assignee: str | None = None,
    after: list[str] | str | None = None,
    by_user: bool = False,
    planned_assignee: str | None = None,
) -> dict[str, Any]:
    doc, save = _load_editable(chat_id, project_root=project_root, template_id=template_id)
    roots = list(doc.get("nodes") or [])
    _, _, node = _walk_find(roots, node_id)
    if node is None:
        raise ValueError("node not found")
    if doc.get("kind") != "template" and _is_done_node(node):
        sent = {"content": content, "kind": kind, "body_markdown": body_markdown, "assignee": assignee,
                "after": after, "planned_assignee": planned_assignee, "status": status}
        if not _changes_done_node(node, sent):
            # A resend of what the done step already holds (lost reply, then retry) is a no-op, not a refusal.
            return save(doc)
        if node.get("children") and node.get("status") == "completed":
            raise ValueError(
                "completed steps can't be edited: this section completed itself when its last "
                "step did, and the notes on its steps are its record. Nothing more to tick."
            )
        _refuse_if_done_node(node)
    # Who owns a step is bookkeeping like its status: allowed while the plan plays.
    if assignee is not None:
        owner = assignee.strip()
        if owner:
            node["assignee"] = owner
        else:
            node.pop("assignee", None)
    # So is what a step waits for: a lead orders the work without pausing the team.
    if after is not None:
        _set_after(doc, node, after)
    # The agent the owner picked for a sub plan (a ducky profile id or a chat id). Unlike
    # assignee it starts nobody: launching the plan seats it later.
    if planned_assignee is not None:
        planned = planned_assignee.strip()
        if planned:
            node["planned_assignee"] = planned
        else:
            node.pop("planned_assignee", None)
    structure_touch = content is not None or kind is not None
    if structure_touch and doc.get("kind") != "template":
        # A step nobody has started can be reworded while the plan plays: pausing to split one
        # oversized step stopped every seat of the team.
        if not _unstarted(node):
            _refuse_if_structure_locked(doc)
        _refuse_if_done_node(node)
    if content is not None:
        text = (content or "").strip()
        if not text:
            raise ValueError("content required")
        node["content"] = text[:500]
    if kind is not None:
        node["kind"] = _normalize_kind(kind, children=list(node.get("children") or []))
    if body_markdown is not None:
        node["body_markdown"] = _cap_notes(doc, node, body_markdown, project_root)
    if status is not None:
        if status == "in_progress" and node.get("status") != "in_progress" and not by_user:
            _refuse_if_waiting(doc, node)
        if status == "completed" and node.get("status") != "completed" and doc.get("kind") != "template":
            plan_hooks.before_complete(doc, node, chat_id)
        node["status"] = _apply_status_gate(node, status, chat_id, by_user=by_user,
                                            owners=_owners_of(roots, node_id), project_root=project_root)
    doc["nodes"] = roots
    return save(doc)


def _changes_done_node(node: dict[str, Any], sent: dict[str, Any]) -> bool:
    """True when any sent field (None = not sent) differs from what ``node`` holds."""
    for key, value in sent.items():
        if value is None:
            continue
        if key == "after":
            if _normalize_after(value, own_id=str(node.get("id") or "")) != list(node.get("after") or []):
                return True
        elif str(value).strip() != str(node.get(key) or "").strip():
            return True
    return False


def _set_after(doc: dict[str, Any], node: dict[str, Any], after: list[str] | str) -> None:
    """Replace ``node``'s prerequisites (``[]`` clears them); every id must be a step of this plan."""
    nid = str(node.get("id") or "")
    wanted = _normalize_after(after, own_id=nid)
    known = {str(n.get("id")) for n in _flatten_nodes(doc.get("nodes"))}
    inside = {str(n.get("id")) for n in _flatten_nodes(node.get("children") or [])}
    unknown = [sid for sid in wanted if sid not in known]
    if unknown:
        raise ValueError(f"after: no step {', '.join(unknown)} in this plan (use step ids from its outline)")
    if any(sid in inside for sid in wanted):
        raise ValueError("after: a step can't wait for one of its own sub-steps")
    if wanted:
        node["after"] = wanted
    else:
        node.pop("after", None)


def _refuse_if_waiting(doc: dict[str, Any], node: dict[str, Any]) -> None:
    """A step whose prerequisites are unfinished can't start: its owner waits, quietly."""
    pending = waiting_on(doc, node)
    if not pending:
        return
    rows = "; ".join(f"`{p['id']}` {p['content']} ({p['status']})" for p in pending)
    raise ValueError(
        f"step `{node.get('id')}` waits for {rows}. Nothing to do or report until they finish: "
        "the plan wakes you then. Its coordinator can change the order with "
        "ducky_plan_update_node(node_id=..., after=[...])."
    )


@_serialize_plan_edit
def delete_node(
    chat_id: str,
    node_id: str,
    *,
    project_root: str | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    doc, save = _load_editable(chat_id, project_root=project_root, template_id=template_id)
    if doc.get("kind") != "template":
        _refuse_if_structure_locked(doc)
    roots = list(doc.get("nodes") or [])
    sibs, idx, node = _walk_find(roots, node_id)
    if node is None or sibs is None or idx < 0:
        raise ValueError("node not found")
    if doc.get("kind") != "template":
        _refuse_if_done_node(node, action="remov")
        if any(_is_done_node(n) for n in _flatten_nodes(list(node.get("children") or []))):
            raise ValueError("can't remove a branch that contains completed steps")
    sibs.pop(idx)
    doc["nodes"] = roots
    return save(doc)


@_serialize_plan_edit
def move_node(
    chat_id: str,
    node_id: str,
    *,
    parent_id: str = "",
    index: int = 0,
    project_root: str | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    """Move a subplan under parent_id (empty = root) at index. Refuses cycles."""
    doc, save = _load_editable(chat_id, project_root=project_root, template_id=template_id)
    if doc.get("kind") != "template":
        _refuse_if_structure_locked(doc)
    roots = list(doc.get("nodes") or [])
    sibs, idx, node = _walk_find(roots, node_id)
    if node is None or sibs is None or idx < 0:
        raise ValueError("node not found")
    if doc.get("kind") != "template":
        _refuse_if_done_node(node, action="mov")
        if any(_is_done_node(n) for n in _flatten_nodes(list(node.get("children") or []))):
            raise ValueError("can't move a branch that contains completed steps")
    moved = sibs.pop(idx)
    new_parent = (parent_id or "").strip()
    if new_parent:
        if new_parent == str(moved.get("id")) or _contains_id(list(moved.get("children") or []), new_parent):
            raise ValueError("cannot move a subplan under itself or a descendant")
        # Re-find parent after removal.
        _, _, parent_node = _walk_find(roots, new_parent)
        if parent_node is None:
            raise ValueError("parent node not found")
        kids = list(parent_node.get("children") or [])
        insert_at = max(0, min(int(index), len(kids)))
        kids.insert(insert_at, moved)
        parent_node["children"] = kids
        parent_node["kind"] = "subplan"
    else:
        insert_at = max(0, min(int(index), len(roots)))
        roots.insert(insert_at, moved)
    doc["nodes"] = roots
    return save(doc)


def _load_editable(
    chat_id: str,
    *,
    project_root: str | None,
    template_id: str | None,
) -> tuple[dict[str, Any], Any]:
    tid = (template_id or "").strip()
    if tid:
        doc = load_template(tid)
        if not doc:
            raise ValueError("template not found")
        return doc, lambda d: save_template(d)

    cid = (chat_id or "").strip()
    if not cid:
        raise ValueError("chat_id or template_id required")
    doc = load_plan(cid, project_root)
    if not doc:
        raise _no_plan_error(cid, project_root)
    return doc, lambda d: save_plan(d, project_root)


def todo_progress(plan: dict[str, Any] | None) -> dict[str, int]:
    """Progress over non-cancelled nodes (including parents); report cancellations separately."""
    flat = _flatten_nodes((plan or {}).get("nodes") if plan else None)
    if not flat and plan:
        flat = [
            {"status": t.get("status")}
            for t in (plan.get("todos") or [])
            if isinstance(t, dict)
        ]
    total = sum(1 for t in flat if str(t.get("status")) != "cancelled")
    completed = sum(1 for t in flat if str(t.get("status")) == "completed")
    cancelled = sum(1 for t in flat if str(t.get("status")) == "cancelled")
    in_progress = sum(1 for t in flat if str(t.get("status")) == "in_progress")
    pending = sum(1 for t in flat if str(t.get("status")) == "pending")
    return {
        "total": total,
        "completed": completed,
        "cancelled": cancelled,
        "in_progress": in_progress,
        "pending": pending,
    }


PLAN_CRUD_TOOLS = frozenset(
    {
        "ducky_create_plan",
        "ducky_update_plan",
        "ducky_get_plan",
        "ducky_list_plans",
        "ducky_plan_add_node",
        "ducky_plan_update_node",
        "ducky_plan_delete_node",
        "ducky_plan_move_node",
        "ducky_list_plan_templates",
        "ducky_get_plan_template",
        "ducky_create_plan_template",
        "ducky_update_plan_template",
        "ducky_instantiate_plan_template",
    }
)

_PLAN_TICK_SKIP = PLAN_CRUD_TOOLS | frozenset(
    {
        "ducky_call_tool",
        "ducky_get_tools",
        "ducky_find_tools",
        "ducky_get_status",
        "ducky_ask_user",
        # Claude Code's approval hook: the gated tool itself is checked when it runs.
        "ducky_permission_prompt",
    }
)


def _is_leaf_node(node: dict[str, Any]) -> bool:
    kids = node.get("children") or []
    return not (isinstance(kids, list) and kids)


def next_open_leaf(plan: dict[str, Any] | None) -> tuple[str, dict[str, Any]] | None:
    """Depth-first: the in_progress leaf, else the first pending leaf."""
    if not plan or not isinstance(plan, dict):
        return None
    pending: tuple[str, dict[str, Any]] | None = None
    for lab, node in outline_numbers(plan.get("nodes")):
        if not _is_leaf_node(node):
            continue
        status = str(node.get("status") or "pending")
        if status == "in_progress":
            return (lab, node)
        if status == "pending" and pending is None:
            pending = (lab, node)
    return pending


def active_plan_chat_id() -> str:
    try:
        from frontend.ui_web.agent_modes import get_active_conv_id

        cid = (get_active_conv_id() or "").strip()
        if cid:
            return cid
    except Exception:
        pass
    import os

    return (os.environ.get("DUCKY_CONV_ID") or "").strip()


def load_active_plan() -> dict[str, Any] | None:
    cid = active_plan_chat_id()
    if not cid:
        return None
    try:
        return load_plan(cid, None)
    except Exception:
        return None


def plan_mutator_block_reason(tool_name: str) -> str | None:
    """Block mutators until an open plan leaf is marked in_progress."""
    n = (tool_name or "").strip()
    if not n or n in _PLAN_TICK_SKIP:
        return None
    try:
        from backend.agent.toolsets.plan_safe import is_plan_safe_tool

        if is_plan_safe_tool(n):
            return None
    except Exception:
        pass
    plan = load_active_plan()
    if not plan:
        return None
    tick = next_tick_dict(plan)
    if not tick:
        return None
    if tick["status"] == "in_progress":
        return None
    return (
        f"Plan tick required before `{n}`: no leaf is in_progress. "
        f"Call {tick['call']} first, then retry. "
        f"Next leaf {tick['n']} `{tick['id']}` — {tick['content']}"
    )


def format_plan_tick_nudge_for_tool(tool_name: str) -> str:
    n = (tool_name or "").strip()
    if not n or n in _PLAN_TICK_SKIP:
        return ""
    try:
        from backend.agent.toolsets.plan_safe import is_plan_safe_tool

        if is_plan_safe_tool(n):
            return ""
    except Exception:
        pass
    return format_plan_tick_nudge(load_active_plan())


def _waiting_note(node: dict[str, Any]) -> str:
    """" — waits for `x` (owner)": said once in the outline, so nobody asks or reports it."""
    rows = node.get("waiting_on") or []
    if not rows or str(node.get("status") or "") in _DONE:
        return ""
    return " — waits for " + ", ".join(
        f"`{r['id']}`" + (f" (owner {r['assignee'][:8]})" if r.get("assignee") else "") for r in rows
    ) + ": nothing to do or report until then"


def format_plan_prompt_block(
    plan: dict[str, Any] | None,
    *,
    max_nodes: int = 40,
) -> str:
    """Compact outline for agent system prompts — followable, not a chat dump."""
    if not plan or not isinstance(plan, dict):
        return ""
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return ""
    title = str(plan.get("title") or "Plan").strip() or "Plan"
    overview = str(plan.get("overview") or "").strip()
    prog = todo_progress(plan)
    lines = [
        "## Active chat plan (follow this — never invent a parallel prose Fix plan)",
        (
            f"**{title}** — {prog['completed']}/{prog['total']} done"
            + (f", {prog['in_progress']} in progress" if prog["in_progress"] else "")
        ),
    ]
    assignment = plan.get("assigned_from")
    if isinstance(assignment, dict):
        lines[0] = "## Your steps in your team's plan"
        lines.extend([
            f"Your team works in ONE plan, “{assignment.get('plan_title') or title}” (`{plan.get('id')}`): "
            "never create a plan of your own.",
            "Tick your steps with ducky_plan_update_node(node_id=...) and split one with "
            "ducky_plan_add_node(parent_id=<your step>, ...): no chat_id needed. "
            f"The whole plan: ducky_get_plan(chat_id={json.dumps(assignment.get('chat_id'))}).",
        ])
        for i, (lab, node) in enumerate(outline_numbers(nodes)):
            if i >= max_nodes:
                lines.append("…(truncated — read the full canonical plan)")
                break
            lines.append(f"- {lab} [{node.get('status', 'pending')}] `{node['id']}` — {str(node.get('content') or '')[:160]}"
                         + _waiting_note(node))
        return "\n".join(lines) + "\n"
    nudge = format_plan_tick_nudge(plan)
    if nudge:
        lines.append(nudge)
    if overview:
        lines.append(overview[:400])
    lines.append("")
    lines.append(
        "Outline (depth-first open leaves). CHECK OFF every step with `ducky_plan_update_node`:"
    )
    for i, (lab, n) in enumerate(outline_numbers(nodes)):
        if i >= max_nodes:
            lines.append("…(truncated — call `ducky_get_plan` for the full tree)")
            break
        status = str(n.get("status") or "pending")
        nid = str(n.get("id") or "")
        content = str(n.get("content") or "").strip().replace("\n", " ")[:160]
        lines.append(f"- {lab} [{status}] `{nid}` — {content}" + _waiting_note({**n, "waiting_on": waiting_on(plan, n)}))
    lines.append(
        "Protocol (every tool round): mark the current leaf `in_progress` BEFORE the "
        "work, `completed` when Done-when is met, then mark the next leaf `in_progress`. "
        "Send each tick in the same turn as the work it covers, before its calls (a turn's calls run in order). "
        "Re-read this outline constantly. Leaving finished work pending is a failure. "
        "If diagnosis changes the approach, update the tree first — never thrash off-plan."
    )
    return "\n".join(lines) + "\n"


def _chat_and_group_ids(chat_id: str, project_root: str | None) -> list[str]:
    """This chat, then the group it sits in, then that group's group, and so on."""
    from frontend.ui_web.project_chats import load_conversation_meta

    ids: list[str] = []
    cur = (chat_id or "").strip()
    for _ in range(8):
        if not cur or cur in ids:
            break
        try:
            conv = load_conversation_meta(cur, project_root=project_root)
        except Exception:
            conv = None
        if conv is None:
            break
        ids.append(cur)
        cur = (getattr(conv, "parent_conv_id", None) or "").strip() if conv is not None else ""
    return ids


def _live_plan(plan: dict[str, Any] | None) -> bool:
    return isinstance(plan, dict) and str(plan.get("status") or "open") not in ("archived", "finished", "completed")


def team_plan_for(chat_id: str, project_root: str | None = None) -> dict[str, Any] | None:
    """The live plan of this chat's team when another chat owns it, else None.

    That is the plan of the lead of a group this chat sits in (the nearest group first),
    else a live plan that gives this chat or its group an open step. A team works in one
    plan: a member made its own private plan, the lead's plan never showed that work, and
    a teammate reported the same "blocked" for an hour while the fix sat out of sight.
    """
    from frontend.ui_web.project_chats import load_conversation_meta

    ids = _chat_and_group_ids(chat_id, project_root)
    if not ids:
        return None
    for gid in ids[1:]:
        try:
            group = load_conversation_meta(gid, project_root=project_root)
        except Exception:
            group = None
        lead = str(getattr(group, "leader_conv_id", "") or "").strip() if group is not None else ""
        if not lead or lead == ids[0]:
            continue
        try:
            alive = load_conversation_meta(lead, project_root=project_root) is not None
        except Exception:
            alive = False
        plan = load_plan(lead, project_root) if alive else None
        if _live_plan(plan):
            return plan
    try:
        view = assigned_plan_view(chat_id, project_root)
    except ValueError:
        view = None
    if view and any(str(n.get("status") or "") not in _DONE for n in _flatten_nodes(view.get("nodes"))):
        plan = load_plan(str(view.get("chat_id") or ""), project_root)
        if _live_plan(plan):
            return plan
    return None


def _refuse_if_on_team_plan(chat_id: str, project_root: str | None) -> None:
    team = team_plan_for(chat_id, project_root)
    if team is None:
        return
    lead = str(team.get("chat_id") or "")
    raise ValueError(
        f"This chat is on its team's plan “{team.get('title') or 'Plan'}” (coordinator chat {lead}). "
        "A team works in ONE plan, so don't make another. To split your step, add sub-steps under it there: "
        f"ducky_plan_add_node(chat_id=\"{lead}\", parent_id=<your step id>, content=..., body_markdown=...). "
        "Work outside your steps: ask the coordinator to add a step for it."
    )


def _refuse_if_lead_has_no_plan(chat_id: str, project_root: str | None) -> None:
    """A member whose lead has no plan yet works from its task and makes no plan of its own.

    Oct 15 2026: a lead handed out work by message before writing its plan, a member wrote a
    plan of its own, and Ducky's keeper then woke that member as the team's lead."""
    from frontend.ui_web.project_chats import load_conversation_meta

    ids = _chat_and_group_ids(chat_id, project_root)
    for gid in ids[1:]:
        try:
            group = load_conversation_meta(gid, project_root=project_root)
            lead = str(getattr(group, "leader_conv_id", "") or "").strip() if group is not None else ""
            alive = bool(lead) and lead != ids[0] and load_conversation_meta(lead, project_root=project_root) is not None
        except Exception:
            alive = False
        if not alive:
            continue
        plan = load_plan(lead, project_root)
        if plan is None or plan.get("status") == "archived":
            raise ValueError(
                f"This chat is in a team whose lead (chat {lead}) has no plan yet. A team works in ONE "
                "plan, its lead's, so don't make one of your own: do the task you were given. If the work "
                "needs a plan, say so in your answer; the lead writes it."
            )


def _group_short_name(title: str) -> str:
    """"Island Media Kit — Edit & Render" → "Edit & Render": how plans name a team's groups."""
    name = (title or "").strip()
    return name.rsplit(" — ", 1)[-1].strip() if " — " in name else name


def _with_inferred_owners(plan: dict[str, Any], lead_id: str, convs: list[Any]) -> dict[str, Any]:
    """A copy of a lead's plan whose unassigned steps that name a group the lead runs
    ("Edit & Render group implements…") carry ``assignee_inferred`` = that group."""
    groups = [(c.id, _group_short_name(getattr(c, "title", "") or "")) for c in convs
              if getattr(c, "is_group", False) and (getattr(c, "leader_conv_id", "") or "").strip() == lead_id]
    groups = [(gid, name) for gid, name in groups if len(name) >= 4]
    if not groups:
        return plan

    def walk(nodes: list[Any]) -> list[Any]:
        out = []
        for node in nodes or []:
            row = dict(node)
            if not row.get("assignee"):
                text = str(row.get("content") or "").lower()
                hits = [(len(name), gid) for gid, name in groups if name.lower() in text]
                if hits:
                    row["assignee_inferred"] = max(hits)[1]
            if row.get("children"):
                row["children"] = walk(row["children"])
            out.append(row)
        return out

    return {**plan, "nodes": walk(list(plan.get("nodes") or []))}


def with_team_links(plan: dict[str, Any] | None, chat_id: str,
                    project_root: str | None = None) -> dict[str, Any] | None:
    """Display links between a team's main plan and its members' own plans; never saved.

    A lead's plan shows who works on each step it did not assign (the group its text
    names). A member's own plan gets ``team_plan``: the plan of the nearest lead above
    it and the step its group works on, so the plan links back to the main plan.
    """
    if not plan or plan.get("assigned_from"):
        return plan
    try:
        from frontend.ui_web.project_chats import island_for_conversation, load_conversation_meta

        convs, _folders = island_for_conversation(chat_id, project_root)
    except Exception:
        return plan
    plan = _with_inferred_owners(plan, chat_id, convs)
    by_id = {c.id: c for c in convs}
    ids = _chat_and_group_ids(chat_id, project_root)
    for hub_id in ids[1:]:
        hub = by_id.get(hub_id) or load_conversation_meta(hub_id, project_root=project_root)
        lead = (getattr(hub, "leader_conv_id", "") or "").strip() if hub is not None else ""
        if not lead or lead == chat_id:
            continue
        main = load_plan(lead, project_root)
        if main is None or main.get("status") == "archived":
            continue
        main = _with_inferred_owners(main, lead, convs)
        node_id = next((str(n.get("id") or "") for n in _flatten_nodes(main.get("nodes"))
                        if (n.get("assignee") or n.get("assignee_inferred")) in ids), "")
        return {**plan, "team_plan": {"chat_id": lead, "title": str(main.get("title") or "Plan"), "node_id": node_id}}
    return plan


def load_plan_view(chat_id: str, project_root: str | None = None) -> dict[str, Any] | None:
    """Resolve an active read view without redirecting raw storage or mutations.

    Open steps in the team's plan outrank a plan of the chat's own: a member that made a
    private plan kept seeing it instead of the step its lead assigned.
    """
    own = load_plan(chat_id, project_root)
    own_live = own is not None and own.get("status") != "archived"
    try:
        view = assigned_plan_view(chat_id, project_root, report_ambiguity=True)
    except ValueError:
        if own_live:
            return own
        raise
    if view is not None and (not own_live or any(
            str(n.get("status") or "") not in _DONE for n in _flatten_nodes(view.get("nodes")))):
        return view
    return own if own_live else view


def assigned_plan_view(chat_id: str, project_root: str | None = None, *,
                       report_ambiguity: bool = False) -> dict[str, Any] | None:
    """The part of a team plan assigned to this chat or to a group it belongs to.

    Team members have no plan of their own, so their Plan pane said "No plan" while
    the coordinator's plan held their work. The view is read-only: edits go to the
    plan's own chat (``assigned_from.chat_id``).
    """
    from frontend.ui_web.project_chats import load_conversation_meta

    ids = _chat_and_group_ids(chat_id, project_root)
    if not ids:
        return None
    # Stored plans may outlive their owner chats. They are retained records,
    # not active assignments, and must not shadow a surviving coordinator.
    plans = [p for p in list_plans(project_root)
             if str(p.get("chat_id") or "") != ids[0]
             and p.get("status") != "archived"
             and load_conversation_meta(str(p.get("chat_id") or ""), project_root=project_root) is not None]
    for wanted in ids:  # the most specific owner wins
        matches = [(p, n) for p in plans for n in _flatten_nodes(p.get("nodes"))
                   if str(n.get("assignee") or "") == wanted]
        # Work still to do outranks finished parts: a member's section in a finished
        # plan made every later assignment "ambiguous", so its chat showed no plan.
        live = [(p, n) for p, n in matches
                if p.get("status") not in ("finished", "completed")
                and n.get("status") not in ("completed", "cancelled")]
        # Several open steps in ONE plan is ordinary (a builder owns three features).
        # Oct 11 2026: counting steps instead of plans blocked every builder of a team.
        by_plan: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        for p, n in live:
            by_plan.setdefault(str(p.get("chat_id") or ""), (p, []))[1].append(n)
        if len(by_plan) > 1:
            # Open steps in two plans: the plan of this chat's own team lead wins.
            leads = _team_leads(ids, project_root)
            own = {cid: row for cid, row in by_plan.items() if cid in leads}
            if len(own) != 1:
                if report_ambiguity:
                    raise ValueError(_two_plans_message(chat_id, by_plan))
                return None
            by_plan = own
        if by_plan:
            plan, nodes = next(iter(by_plan.values()))
            nodes = _outermost(nodes)
        elif matches:
            # Only finished parts: nothing to act on, so the latest one is shown read-only.
            plan, node = max(matches, key=lambda m: float(m[0].get("updated_at") or 0))
            nodes = [node]
        else:
            continue
        title = str(plan.get("title") or "Plan")
        nodes = [_with_waiting(plan, n) for n in nodes]
        first = nodes[0]
        return {
            "id": str(plan.get("plan_id") or ""),
            "kind": "project",
            "chat_id": str(plan.get("chat_id") or ""),
            # Always the team plan's own name: a one-step view titled with the step's name
            # read like a separate plan.
            "title": (f"Your sub plan in “{title}”" if len(nodes) == 1 and first.get("children")
                      else f"Your step in “{title}”" if len(nodes) == 1
                      else f"Your {len(nodes)} steps in “{title}”"),
            "overview": f"Part of the plan “{title}”.",
            "body_markdown": str(first.get("body_markdown") or "") if len(nodes) == 1 else "",
            "status": str(plan.get("status") or "open"),
            "nodes": nodes,
            "todos": [
                {"id": n["id"], "content": n["content"], "status": n["status"]}
                for n in _flatten_nodes(nodes)
            ],
            "updated_at": plan.get("updated_at"),
            "assigned_from": {
                "chat_id": str(plan.get("chat_id") or ""),
                "node_id": str(first.get("id") or ""),
                "node_ids": [str(n.get("id") or "") for n in nodes],
                "plan_title": title,
                "assignee": wanted,
            },
        }
    return None


def _with_waiting(plan: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``node`` (and its steps) carrying ``waiting_on``: unfinished steps it needs first."""
    row = dict(node)
    pending = waiting_on(plan, node) if str(node.get("status") or "") not in _DONE else []
    if pending:
        row["waiting_on"] = pending
    if node.get("children"):
        row["children"] = [_with_waiting(plan, c) for c in node.get("children") or []]
    return row


def _outermost(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop steps that sit inside another listed step (its section shows them already)."""
    inner = {str(c.get("id")) for n in nodes for c in _flatten_nodes(n.get("children") or [])}
    return [n for n in nodes if str(n.get("id")) not in inner]


def _team_leads(ids: list[str], project_root: str | None) -> set[str]:
    """Leaders of the groups this chat sits in (``ids``: the chat, then its groups)."""
    from frontend.ui_web.project_chats import load_conversation_meta

    leads: set[str] = set()
    for gid in ids[1:]:
        try:
            group = load_conversation_meta(gid, project_root=project_root)
        except Exception:
            group = None
        lead = str(getattr(group, "leader_conv_id", "") or "").strip() if group is not None else ""
        if lead:
            leads.add(lead)
    return leads


def _two_plans_message(chat_id: str, by_plan: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]) -> str:
    """A real conflict, said so its coordinator can fix it in one call."""
    rows = []
    for owner, (plan, nodes) in by_plan.items():
        steps = ", ".join(f"`{n.get('id')}`" for n in nodes)
        rows.append(f"“{plan.get('title') or 'Plan'}” (coordinator chat {owner}: steps {steps})")
    return (
        f"Chat {chat_id} has open steps in {len(by_plan)} plans: " + "; ".join(rows) + ". "
        "Its coordinator fixes this in one call: keep the steps in the plan this chat works on now and "
        "unassign the others with ducky_plan_update_node(chat_id=<that plan's coordinator>, node_id=<step>, "
        "assignee=\"\"), or pause the other plan with ducky_update_plan(chat_id=<its coordinator>, status=\"paused\")."
    )


def list_plans(project_root: str | None = None) -> list[dict[str, Any]]:
    """List chat plans: every one in the database (they follow their duckies, not a
    project); the files store lists the active (or given) project's."""
    from frontend.ui_web.project_chats import conversation_title

    root = _resolve_project_root(project_root)
    if not root and not _use_db():
        return []

    rows: list[dict[str, Any]] = []
    sources: list[tuple[str, dict[str, Any]]] = []
    if _use_db():
        sources = _repo(root).plan_docs(_project_id(root), "project")
    else:
        try:
            directory = _plans_dir(root, create=False)
        except ValueError:
            return []
        if not directory.is_dir():
            return []
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                sources.append((path.stem, data))

    for stem, data in sources:
        doc = _normalize_plan_doc(data, kind="project")
        chat_id = str(doc.get("chat_id") or stem or "").strip()
        if not chat_id:
            continue
        chat_title = ""
        try:
            chat_title = conversation_title(chat_id, project_root=root)
        except Exception:
            pass
        overview = str(doc.get("overview") or "").strip()
        rows.append(
            {
                "chat_id": chat_id,
                "plan_id": str(doc.get("id") or ""),
                "kind": "project",
                "title": str(doc.get("title") or "Plan").strip()[:200] or "Plan",
                "overview": overview[:240],
                "progress": todo_progress(doc),
                "updated_at": float(doc.get("updated_at") or doc.get("created_at") or 0),
                "created_at": float(doc.get("created_at") or 0),
                "status": str(doc.get("status") or "open"),
                "template_id": doc.get("template_id"),
                "nodes": doc.get("nodes") or [],
                "team_reports": doc.get("team_reports") or [],
                "project_root": root,
                "project_name": _project_display_name(root),
                "chat_title": chat_title,
            }
        )

    rows.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)
    return rows


def delete_plan(chat_id: str, project_root: str | None = None) -> bool:
    cid = (chat_id or "").strip()
    if not cid:
        raise ValueError("chat_id required")
    previous = load_plan(cid, project_root) or {}
    if _use_db():
        deleted = _repo(project_root).plan_delete(_project_id(project_root), _safe_id(cid))
        if deleted:
            _push_assignment_invalidated(previous, {"chat_id": cid}, project_root)
        return deleted
    path = _plan_path(cid, project_root)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    _push_assignment_invalidated(previous, {"chat_id": cid}, project_root)
    return True


def copy_plan(
    *,
    source_chat_id: str,
    dest_chat_id: str,
    source_project_root: str | None = None,
    dest_project_root: str | None = None,
) -> dict[str, Any]:
    """Copy a project plan onto another chat. Node statuses reset to pending."""
    src = load_plan(source_chat_id, source_project_root)
    if not src:
        raise ValueError("plan not found")
    return create_plan(
        dest_chat_id,
        title=str(src.get("title") or "Plan"),
        overview=str(src.get("overview") or ""),
        body_markdown=str(src.get("body_markdown") or ""),
        nodes=_reset_node_statuses(_normalize_nodes(src.get("nodes"))),
        template_id=None,
        project_root=dest_project_root,
    )


# ----- Templates (global, reusable) -----


def _templates_repo():
    from backend.store.importers import phase4
    from backend.store.repos import plans as repo

    phase4.ensure("plan_templates")
    return repo


def load_template(template_id: str) -> dict[str, Any] | None:
    if _use_db():
        safe = _safe_id(template_id)
        if not safe:
            return None
        data = _templates_repo().plan_get("", safe)
        if not isinstance(data, dict):
            return None
        doc = _normalize_plan_doc(data, kind="template")
        doc["id"] = str(doc.get("id") or template_id).strip()
        doc["chat_id"] = ""
        return doc
    path = _template_path(template_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        doc = _normalize_plan_doc(data, kind="template")
        doc["id"] = str(doc.get("id") or template_id).strip()
        doc["chat_id"] = ""
        return doc
    except (OSError, json.JSONDecodeError):
        return None


def save_template(plan: dict[str, Any]) -> dict[str, Any]:
    plan = _normalize_plan_doc(dict(plan), kind="template")
    tid = str(plan.get("id") or "").strip()
    if not tid:
        raise ValueError("template id required")
    plan["id"] = tid
    plan["chat_id"] = ""
    plan["template_id"] = None
    plan["updated_at"] = time.time()
    # Templates are blueprints — keep statuses pending in storage for clarity.
    plan["nodes"] = _reset_node_statuses(plan.get("nodes") or [])
    plan["status"] = "template"
    if _use_db():
        _templates_repo().plan_put("", _safe_id(tid), "template", plan)
        return plan
    write_json_atomic(_template_path(tid), plan)
    return plan


def create_template(
    *,
    title: str = "",
    overview: str = "",
    body_markdown: str = "",
    nodes: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tid = uuid.uuid4().hex[:12]
    tree = _normalize_nodes(nodes) if nodes is not None else _todos_to_nodes(todos or [])
    now = time.time()
    return save_template(
        {
            "id": tid,
            "kind": "template",
            "title": (title or "Plan template").strip()[:200],
            "overview": (overview or "").strip(),
            "body_markdown": (body_markdown or "").strip(),
            "nodes": tree,
            "created_at": now,
            "updated_at": now,
            "status": "template",
        }
    )


def update_template(
    template_id: str,
    *,
    title: str | None = None,
    overview: str | None = None,
    body_markdown: str | None = None,
    nodes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tid = (template_id or "").strip()
    doc = load_template(tid)
    if not doc:
        raise ValueError("template not found")
    if title is not None:
        doc["title"] = (title or "Plan template").strip()[:200]
    if overview is not None:
        doc["overview"] = (overview or "").strip()
    if body_markdown is not None:
        doc["body_markdown"] = (body_markdown or "").strip()
    if nodes is not None:
        doc["nodes"] = _normalize_nodes(nodes)
    return save_template(doc)


DEMO_TEMPLATE_ID = "demo-getting-started"
VERIFY_LOOP_TEMPLATE_ID = "verify-loop"


def ensure_demo_plan_template() -> dict[str, Any] | None:
    """Seed a tiny Getting started template once (walkthrough / first-run demo)."""
    existing = load_template(DEMO_TEMPLATE_ID)
    if existing:
        return existing
    now = time.time()
    return save_template(
        {
            "id": DEMO_TEMPLATE_ID,
            "kind": "template",
            "title": "Getting started",
            "overview": "A short demo plan — open Settings, pick a Ducky, start a chat.",
            "body_markdown": "",
            "nodes": [
                {"id": "open-settings", "content": "Open Settings", "status": "pending", "children": []},
                {"id": "pick-ducky", "content": "Pick a Ducky", "status": "pending", "children": []},
                {"id": "start-chat", "content": "Start a chat", "status": "pending", "children": []},
            ],
            "created_at": now,
            "updated_at": now,
            "status": "template",
        }
    )


def ensure_verify_loop_template() -> dict[str, Any] | None:
    """Seed Diagnose → Fix → Verify with a check-tool Done-when."""
    existing = load_template(VERIFY_LOOP_TEMPLATE_ID)
    if existing:
        return existing
    now = time.time()
    return save_template(
        {
            "id": VERIFY_LOOP_TEMPLATE_ID,
            "kind": "template",
            "title": "Verify loop",
            "overview": "Diagnose → Fix → Verify. The Verify leaf names a check tool.",
            "body_markdown": "",
            "nodes": [
                {
                    "id": "diagnose",
                    "content": "Diagnose — inspect the failure",
                    "status": "pending",
                    "kind": "subplan",
                    "children": [
                        {
                            "id": "d1",
                            "content": "Read errors / census (workspace_list_verse_errors)",
                            "status": "pending",
                            "kind": "step",
                            "children": [],
                        }
                    ],
                },
                {
                    "id": "fix",
                    "content": "Fix — apply the smallest change",
                    "status": "pending",
                    "kind": "subplan",
                    "children": [
                        {
                            "id": "f1",
                            "content": "Edit files or wire the device",
                            "status": "pending",
                            "kind": "step",
                            "children": [],
                        }
                    ],
                },
                {
                    "id": "verify",
                    "content": "Verify — run the check tool",
                    "status": "pending",
                    "kind": "subplan",
                    "children": [
                        {
                            "id": "v1",
                            "content": (
                                "Verify: workspace_compile_verse / get_verse_editables / "
                                "verse_test_run (Done-when: check tool ok)"
                            ),
                            "status": "pending",
                            "kind": "step",
                            "children": [],
                        }
                    ],
                },
            ],
            "created_at": now,
            "updated_at": now,
            "status": "template",
        }
    )


def list_templates() -> list[dict[str, Any]]:
    ensure_demo_plan_template()
    ensure_verify_loop_template()
    rows: list[dict[str, Any]] = []
    sources: list[tuple[str, dict[str, Any]]] = []
    if _use_db():
        sources = _templates_repo().plan_docs("", "template")
    else:
        directory = _templates_dir(create=False)
        if not directory.is_dir():
            return []
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                sources.append((path.stem, data))
    for stem, data in sources:
        doc = _normalize_plan_doc(data, kind="template")
        tid = str(doc.get("id") or stem or "").strip()
        if not tid:
            continue
        overview = str(doc.get("overview") or "").strip()
        rows.append(
            {
                "template_id": tid,
                "plan_id": tid,
                "kind": "template",
                "title": str(doc.get("title") or "Plan template").strip()[:200] or "Plan template",
                "overview": overview[:240],
                "updated_at": float(doc.get("updated_at") or doc.get("created_at") or 0),
                "created_at": float(doc.get("created_at") or 0),
                "status": "template",
                "nodes": doc.get("nodes") or [],
                "node_count": len(_flatten_nodes(doc.get("nodes"))),
            }
        )
    rows.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)
    return rows


def delete_template(template_id: str) -> bool:
    tid = (template_id or "").strip()
    if not tid:
        raise ValueError("template_id required")
    if _use_db():
        return _templates_repo().plan_delete("", _safe_id(tid))
    path = _template_path(tid)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def instantiate_template(
    template_id: str,
    *,
    chat_id: str,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Snapshot a template into a project plan. Template file is never modified."""
    src = load_template(template_id)
    if not src:
        raise ValueError("template not found")
    # Load again via raw file to prove isolation — use in-memory copy only.
    nodes = _reset_node_statuses(_normalize_nodes(src.get("nodes")))
    return create_plan(
        chat_id,
        title=str(src.get("title") or "Plan"),
        overview=str(src.get("overview") or ""),
        body_markdown=str(src.get("body_markdown") or ""),
        nodes=nodes,
        template_id=str(src.get("id") or template_id),
        project_root=project_root,
    )


def save_plan_as_template(chat_id: str, project_root: str | None = None) -> dict[str, Any]:
    """Copy a project plan into the global template library (snapshot)."""
    src = load_plan(chat_id, project_root)
    if not src:
        raise ValueError("plan not found")
    return create_template(
        title=str(src.get("title") or "Plan template"),
        overview=str(src.get("overview") or ""),
        body_markdown=str(src.get("body_markdown") or ""),
        nodes=_normalize_nodes(src.get("nodes")),
    )


_AFTER_SAVE = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plan-after-save")


def _after_save(fn, *args) -> None:
    """Side work of a save, in save order, after the call returns."""
    _AFTER_SAVE.submit(fn, *args)


def drain_after_save(timeout: float = 10.0) -> None:
    """Wait for the side work queued so far (tests, and callers that need it done)."""
    _AFTER_SAVE.submit(lambda: None).result(timeout)


def push_plan_updated(plan: dict[str, Any]) -> None:
    """Notify the panel UI that a chat plan changed."""
    try:
        from frontend.ui_web.agent_modes import _resolve_push

        _resolve_push(None)(
            {
                "type": "plan_updated",
                "conv_id": str(plan.get("chat_id") or ""),
                "plan": plan,
                "progress": todo_progress(plan),
            }
        )
    except Exception:
        pass
