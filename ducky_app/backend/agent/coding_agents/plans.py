"""Plans — outline trees (main → subplans) for templates and project instances."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from frontend.atomic_json import write_json_atomic
from frontend.settings import PanelSettings, default_app_data_dir

_NODE_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})
_DONE = frozenset({"completed", "cancelled"})
_NODE_KINDS = frozenset({"step", "subplan"})
_STARTED_NODE = frozenset({"in_progress", "completed"})


def _safe_id(raw: str, *, fallback: str = "") -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (raw or "").strip())[:80]
    return safe or fallback


def _resolve_project_root(project_root: str | None = None) -> str:
    if project_root is None:
        return (PanelSettings.load().uefn_project_root or "").strip()
    return (project_root or "").strip()


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
    return {
        "id": nid,
        "content": content[:500],
        "status": _normalize_status(raw.get("status")),
        "kind": _normalize_kind(raw.get("kind"), children=children),
        "body_markdown": body,
        "children": children,
    }


def _normalize_nodes(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        node = _normalize_node(item)
        if node:
            out.append(node)
    return out


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
        raise ValueError("plan is playing — pause it to edit unfinished steps or add new ones")


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


def _apply_status_gate(node: dict[str, Any], status: str) -> str:
    """Refuse completed unless all descendants are done; return effective status."""
    st = _normalize_status(status)
    if st == "completed" and not _descendants_done(node):
        raise ValueError("cannot complete a subplan while nested subplans are unfinished")
    return st


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
    """Migrate legacy todos/parent_chat_id into nodes tree."""
    doc = dict(data)
    doc.pop("parent_chat_id", None)
    nodes = doc.get("nodes")
    if not isinstance(nodes, list) or (not nodes and doc.get("todos")):
        nodes = _todos_to_nodes(doc.get("todos"))
    else:
        nodes = _normalize_nodes(nodes)
    doc["nodes"] = nodes
    # Compat shim: flat mirror of all outline nodes for older UI/tool paths.
    doc["todos"] = [
        {"id": n["id"], "content": n["content"], "status": n["status"]} for n in _flatten_nodes(nodes)
    ]
    doc["kind"] = kind
    doc["template_id"] = str(doc.get("template_id") or "").strip() or None
    doc["title"] = str(doc.get("title") or "Plan").strip()[:200] or "Plan"
    doc["overview"] = str(doc.get("overview") or "").strip()
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
        return _normalize_plan_doc(data, kind="project")
    except (OSError, json.JSONDecodeError):
        return None


def save_plan(plan: dict[str, Any], project_root: str | None = None) -> dict[str, Any]:
    plan = _normalize_plan_doc(dict(plan), kind="project")
    chat_id = str(plan.get("chat_id") or "").strip()
    if not chat_id:
        raise ValueError("chat_id required")
    if not _resolve_project_root(project_root):
        raise ValueError("project_root required for project plans")
    plan["updated_at"] = time.time()
    _roll_plan_status(plan)
    write_json_atomic(_plan_path(chat_id, project_root), plan)
    return plan


def create_plan(
    chat_id: str,
    *,
    title: str = "",
    overview: str = "",
    body_markdown: str = "",
    nodes: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
    template_id: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    cid = (chat_id or "").strip()
    if not cid:
        raise ValueError("chat_id required")
    tree = _normalize_nodes(nodes) if nodes is not None else _todos_to_nodes(todos or [])
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


def update_plan(
    chat_id: str,
    *,
    title: str | None = None,
    overview: str | None = None,
    body_markdown: str | None = None,
    nodes: list[dict[str, Any]] | None = None,
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
        raise ValueError("plan not found — call ducky_create_plan first")

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
        _assert_completed_tree_preserved(plan.get("nodes"), nodes)
        plan["nodes"] = _normalize_nodes(nodes)
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
                        item["status"] = _apply_status_gate(flat[tid], item["status"])
                    except ValueError:
                        item["status"] = str(flat[tid].get("status") or "pending")
                    flat[tid]["content"] = item["content"]
                    flat[tid]["status"] = item["status"]
                else:
                    roots = list(plan.get("nodes") or [])
                    roots.append(item)
                    plan["nodes"] = roots

    return save_plan(plan, project_root)


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
) -> dict[str, Any]:
    """Add a step/subplan node. Pass template_id to edit a template instead of a project plan."""
    node = _normalize_node(
        {
            "id": uuid.uuid4().hex[:10],
            "content": content,
            "status": status,
            "kind": kind,
            "body_markdown": body_markdown,
            "children": [],
        }
    )
    if not node:
        raise ValueError("content required")
    doc, save = _load_editable(chat_id, project_root=project_root, template_id=template_id)
    if doc.get("kind") != "template":
        _refuse_if_structure_locked(doc)
    roots = list(doc.get("nodes") or [])
    parent = (parent_id or "").strip()
    if not parent:
        idx = len(roots) if index is None else max(0, min(int(index), len(roots)))
        roots.insert(idx, node)
        doc["nodes"] = roots
    else:
        sibs, _, parent_node = _walk_find(roots, parent)
        if parent_node is None:
            raise ValueError("parent node not found")
        kids = list(parent_node.get("children") or [])
        idx = len(kids) if index is None else max(0, min(int(index), len(kids)))
        kids.insert(idx, node)
        parent_node["children"] = kids
        if str(parent_node.get("status")) == "completed":
            parent_node["status"] = "pending"
        if str(parent_node.get("kind") or "") == "step":
            parent_node["kind"] = "subplan"
        doc["nodes"] = roots
    return save(doc)


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
) -> dict[str, Any]:
    doc, save = _load_editable(chat_id, project_root=project_root, template_id=template_id)
    roots = list(doc.get("nodes") or [])
    _, _, node = _walk_find(roots, node_id)
    if node is None:
        raise ValueError("node not found")
    structure_touch = content is not None or kind is not None or body_markdown is not None
    if structure_touch and doc.get("kind") != "template":
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
        node["body_markdown"] = (body_markdown or "").strip()
    if status is not None:
        node["status"] = _apply_status_gate(node, status)
    doc["nodes"] = roots
    return save(doc)


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
        raise ValueError("plan not found — call ducky_create_plan first")
    return doc, lambda d: save_plan(d, project_root)


def todo_progress(plan: dict[str, Any] | None) -> dict[str, int]:
    """Progress over all nodes in the outline tree (not just leaves)."""
    flat = _flatten_nodes((plan or {}).get("nodes") if plan else None)
    if not flat and plan:
        flat = [
            {"status": t.get("status")}
            for t in (plan.get("todos") or [])
            if isinstance(t, dict)
        ]
    total = len(flat)
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
    if overview:
        lines.append(overview[:400])
    lines.append("")
    lines.append(
        "Outline (depth-first open leaves; tick with `ducky_plan_update_node`):"
    )
    for i, (lab, n) in enumerate(outline_numbers(nodes)):
        if i >= max_nodes:
            lines.append("…(truncated — call `ducky_get_plan` for the full tree)")
            break
        status = str(n.get("status") or "pending")
        nid = str(n.get("id") or "")
        content = str(n.get("content") or "").strip().replace("\n", " ")[:160]
        lines.append(f"- {lab} [{status}] `{nid}` — {content}")
    lines.append(
        "Protocol: mark leaf `in_progress` → do the step → `completed`. "
        "If diagnosis changes the approach, update the tree first — never thrash off-plan."
    )
    return "\n".join(lines) + "\n"


def list_plans(project_root: str | None = None) -> list[dict[str, Any]]:
    """List project plans for the active (or given) project only."""
    from frontend.ui_web.project_chats import load_conversation

    root = _resolve_project_root(project_root)
    if not root:
        return []

    rows: list[dict[str, Any]] = []
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
        if not isinstance(data, dict):
            continue
        doc = _normalize_plan_doc(data, kind="project")
        chat_id = str(doc.get("chat_id") or path.stem or "").strip()
        if not chat_id:
            continue
        chat_title = ""
        try:
            conv = load_conversation(chat_id, project_root=root)
            if conv is not None:
                chat_title = str(getattr(conv, "title", "") or "").strip()
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
    path = _plan_path(cid, project_root)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
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


def load_template(template_id: str) -> dict[str, Any] | None:
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


def list_templates() -> list[dict[str, Any]]:
    ensure_demo_plan_template()
    rows: list[dict[str, Any]] = []
    directory = _templates_dir(create=False)
    if not directory.is_dir():
        return []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        doc = _normalize_plan_doc(data, kind="template")
        tid = str(doc.get("id") or path.stem or "").strip()
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
