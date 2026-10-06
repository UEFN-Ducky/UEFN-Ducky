"""Persist workflow graphs.

A workflow is one graph; what starts it comes from its nodes (Chat input, a
schedule, a plugin trigger, or Run). Each workflow is owned by **Local** (this PC
only) or a **team** (synced to every member): see :mod:`backend.automations.owned`.
``DUCKY_STORE_BACKEND=files`` keeps the old JSON folder, Local only.
"""

from __future__ import annotations

import json
import math
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.store.switch import use_db
from frontend.app_paths import resolve_app_data_dir

_SAVE_LOCK = threading.RLock()
_RUN_CAP = 20
LOCAL = "local"
_LOCAL_OWNER = {"id": LOCAL, "kind": LOCAL, "label": "Local", "state": "ok", "readOnly": False, "reason": ""}
_BUILTIN_STARTERS = {"start.manual", "start.chat", "start.cron", "flow.input"}
CODE_TYPE = "code.js"
_FOLDER_NAME_MAX = 64
_FOLDER_PATH_MAX = 512
_FOLDERS_FILE = "_folders.json"  # files mode: Local's folder list (no "id", so never read as a workflow)
# Named colors a node or a group box can take (the editor maps them to theme colors).
_COLORS = {"red", "amber", "green", "blue", "purple"}
# A picked icon is one emoji (or a very short symbol); emoji sequences run up to ~8 code points.
_ICON_MAX = 8


def _icon(raw: Any) -> str:
    icon = raw.strip() if isinstance(raw, str) else ""
    if not icon or len(icon) > _ICON_MAX or any(ch.isspace() or ord(ch) < 32 for ch in icon):
        return ""
    return icon


def _announce_graphs_changed() -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "graphs_changed"})
    except Exception:
        pass


def normalize_graph(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    nodes: list[dict[str, Any]] = []
    for n in src.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "").strip()
        ntype = str(n.get("type") or "").strip()
        if not nid or not ntype:
            continue
        cfg = n.get("config") if isinstance(n.get("config"), dict) else {}
        nodes.append(
            {
                "id": nid,
                "type": ntype,
                "x": float(n.get("x") or 0),
                "y": float(n.get("y") or 0),
                "config": dict(cfg),
                "label": str(n.get("label") or ""),
                "description": str(n.get("description") or ""),
            }
        )
        if "width" in n:
            try:
                width = float(n["width"])
            except (TypeError, ValueError):
                width = 320.0
            nodes[-1]["width"] = min(720.0, max(280.0, width)) if math.isfinite(width) else 320.0
        if n.get("color") in _COLORS:
            nodes[-1]["color"] = n["color"]
        if n.get("locked") is True:  # the editor won't move or change it until unlocked
            nodes[-1]["locked"] = True
        if _icon(n.get("icon")):
            nodes[-1]["icon"] = _icon(n.get("icon"))
    ids = {n["id"] for n in nodes}
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    fed: set[tuple[str, str]] = set()  # one data wire per input pin
    for e in src.get("edges") or []:
        if not isinstance(e, dict):
            continue
        source = str(e.get("source") or "").strip()
        target = str(e.get("target") or "").strip()
        kind = str(e.get("kind") or "main").strip() or "main"
        if source not in ids or target not in ids or source == target:
            continue
        if kind == "data":
            source_pin = str(e.get("source_pin") or "").strip()
            target_pin = str(e.get("target_pin") or "").strip()
            if not source_pin or not target_pin or (target, target_pin) in fed:
                continue
            fed.add((target, target_pin))
            edges.append({"source": source, "target": target, "kind": kind, "source_pin": source_pin, "target_pin": target_pin})
            continue
        key = (source, target, kind)
        if key not in seen:
            seen.add(key)
            edges.append({"source": source, "target": target, "kind": kind})
    out = {"nodes": nodes, "edges": edges}
    if "groups" in src:
        out["groups"] = _normalize_groups(src.get("groups"), ids)
    return out


def _normalize_groups(raw: Any, ids: set[str]) -> list[dict[str, Any]]:
    """Visual boxes. A node sits in at most one group (its innermost); ``parent_id``
    nests a group inside another; ``color`` is one of ``_COLORS``. Missing parents and cycles become top level, and a
    group with no nodes and no child groups is dropped."""
    groups: list[dict[str, Any]] = []
    parents: dict[str, str] = {}
    grouped_nodes: set[str] = set()
    for group in raw if isinstance(raw, list) else []:
        if not isinstance(group, dict):
            continue
        gid = str(group.get("id") or "").strip()
        members = group.get("node_ids")
        if not gid or gid in parents or not isinstance(members, list):
            continue
        kept: list[str] = []
        for member in members:
            if isinstance(member, str) and member in ids and member not in grouped_nodes:
                kept.append(member)
                grouped_nodes.add(member)
        parents[gid] = str(group.get("parent_id") or "").strip()
        box: dict[str, Any] = {"id": gid, "name": str(group.get("name") or "Group").strip() or "Group", "node_ids": kept}
        if group.get("color") in _COLORS:
            box["color"] = group["color"]
        if group.get("locked") is True:
            box["locked"] = True
        if _icon(group.get("icon")):
            box["icon"] = _icon(group.get("icon"))
        groups.append(box)
    for gid in parents:
        seen = {gid}
        at = parents[gid]
        while at and at in parents and at not in seen:
            seen.add(at)
            at = parents[at]
        if at:  # unknown parent, or a loop back into the chain
            parents[gid] = ""
    alive = {g["id"] for g in groups}
    while True:
        with_children = {parents[gid] for gid in alive if parents[gid]}
        empty = {g["id"] for g in groups if g["id"] in alive and not g["node_ids"] and g["id"] not in with_children}
        if not empty:
            break
        alive -= empty
    out: list[dict[str, Any]] = []
    for group in groups:
        if group["id"] in alive:
            if parents[group["id"]] in alive:
                group["parent_id"] = parents[group["id"]]
            out.append(group)
    return out


def normalize_folder(raw: Any) -> str:
    """A workflow's folder inside its owner: ``"Play tests/Tycoon"``, ``""`` = top level."""
    parts = [" ".join(str(part).split())[:_FOLDER_NAME_MAX] for part in str(raw or "").replace("\\", "/").split("/")]
    return "/".join(part for part in parts if part)[:_FOLDER_PATH_MAX]


def signature_of(nodes: list[dict[str, Any]]) -> dict[str, list[Any]] | None:
    """What a reusable workflow takes (Inputs node) and gives back (Return nodes)."""
    inputs = [n for n in nodes if n.get("type") == "flow.input"]
    returns = [n for n in nodes if n.get("type") == "flow.output"]
    if not inputs and not returns:
        return None
    from backend.automations.pins import clean_type

    params: list[dict[str, str]] = []
    for node in inputs:
        for row in (node.get("config") or {}).get("inputs") or []:
            name = str(row.get("name") or "").strip() if isinstance(row, dict) else ""
            if name and all(p["name"] != name for p in params):
                param = {"name": name, "default": str(row.get("default") or "")}
                if row.get("type"):
                    param["type"] = clean_type(row.get("type"))
                params.append(param)
    outputs: list[str] = []
    output_types: dict[str, str] = {}
    for node in returns:
        for row in (node.get("config") or {}).get("outputs") or []:
            name = str(row.get("name") or "").strip() if isinstance(row, dict) else ""
            if name and name not in outputs:
                outputs.append(name)
                if row.get("type"):
                    output_types[name] = clean_type(row.get("type"))
    out: dict[str, Any] = {"inputs": params, "outputs": outputs}
    if output_types:
        out["output_types"] = output_types  # pin types on the Run workflow node
    return out


def empty_workflow(*, name: str = "Untitled") -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "name": (name or "Untitled").strip() or "Untitled",
        "description": "",
        "enabled": True,
        "folder": "",
        "graph": {"nodes": [], "edges": []},
        "updated": time.time(),
    }


# --------------------------------------------------------------------------- public API


def list_workflows() -> list[dict[str, Any]]:
    """Summaries of every workflow this account can see, Local first, then teams."""
    return [_summary(wf) for wf in all_workflows()]


def all_workflows() -> list[dict[str, Any]]:
    """Full workflows (graph, owner, per-PC state) for the list, scheduler and triggers."""
    if not use_db("automations"):
        return [_files_view(w) for w in _files_all()]
    from backend.automations import owned

    out: list[dict[str, Any]] = []
    for scope in owned.owner_scopes():
        owner = owned.owner_view(scope)
        rows = [_view(doc, owner, owned.state(scope["account"], str(doc["id"])), []) for doc in owned.docs(scope)]
        rows.sort(key=lambda w: (-float(w.get("updated") or 0.0), str(w.get("name") or "")))
        out.extend(rows)
    return out


def get_workflow(workflow_id: str) -> dict[str, Any] | None:
    wid = (workflow_id or "").strip()
    if not wid:
        return None
    if not use_db("automations"):
        row = _read_file(_files_dir() / f"{wid}.json") if _safe_file_id(wid) else None
        return _files_view(row) if row else None
    from backend.automations import owned

    found = owned.find(wid)
    if found is None:
        return None
    scope, doc = found
    aid = scope["account"]
    return _view(doc, owned.owner_view(scope), owned.state(aid, wid), owned.runs(aid, wid))


def save_workflow(doc: dict[str, Any], *, owner: str = "") -> dict[str, Any]:
    """Create or update a workflow. A new one lands in ``owner`` (default Local); an
    existing one stays with its owner (:func:`copy_workflow` moves it). Raises
    ``PermissionError`` when that owner is read-only here."""
    with _SAVE_LOCK:
        out = save_quietly(doc, owner)
    _announce_graphs_changed()
    return out


def save_quietly(doc: dict[str, Any], owner: str = "") -> dict[str, Any]:
    """:func:`save_workflow` without telling the editor (a caller saving many tells it once)."""
    return _files_save(doc) if not use_db("automations") else _db_save(doc, owner)


def delete_workflow(workflow_id: str) -> bool:
    wid = (workflow_id or "").strip()
    if not wid:
        return False
    if not use_db("automations"):
        ok = _safe_file_id(wid) and _files_delete(wid)
    else:
        from backend.automations import owned

        found = owned.find(wid)
        ok = False
        if found is not None:
            scope = owned.writable(found[0])
            ok = owned.remove(scope, wid)
            owned.forget(scope["account"], [wid])
    if ok:
        _forget_approvals(wid)
        _announce_graphs_changed()
    return ok


def copy_workflow(workflow_id: str, owner: str, *, move: bool = False) -> dict[str, Any]:
    """Copy (new id) or move (same id) a workflow to Local or a team. A copy's Custom code
    may run on its own only where the original's could."""
    if not use_db("automations"):
        raise ValueError("Sharing with a team needs the database store")
    from backend.automations import owned

    with _SAVE_LOCK:
        found = owned.find(workflow_id)
        if found is None:
            raise KeyError("workflow not found")
        src, doc = found
        dst = owned.writable(owned.scope_for(owner, src["account"]))
        if dst["kind"] == "team" and _has_code((doc.get("graph") or {}).get("nodes")):
            from backend.automations.code_approval import TEAM_REFUSAL

            raise ValueError(TEAM_REFUSAL)
        if move:
            owned.writable(src)
            if src["id"] == dst["id"]:
                return get_workflow(workflow_id) or {}
        out = {**doc, "id": doc["id"] if move else str(uuid.uuid4()), "updated": time.time()}
        owned.write(dst, out, versions=(out,))
        if move:
            owned.remove(src, str(doc["id"]))
        if dst["kind"] == "team":
            owned.set_run_here(dst["account"], str(out["id"]), True)
    copied = get_workflow(str(out["id"])) or {}
    if not move:
        carry_approvals(copied, [doc["id"]])
    _announce_graphs_changed()
    return copied


def set_folder(workflow_id: str, folder: str) -> dict[str, Any] | None:
    """File one workflow in a folder of its owner (no saved version, keeps its place).
    The folder it leaves stays in the owner's folder list."""
    wid = (workflow_id or "").strip()
    wf = get_workflow(wid)
    if wf is None:
        return None
    doc = {"id": wid, "folder": normalize_folder(folder)}
    with _SAVE_LOCK:
        out = _files_save(doc, archive=False) if not use_db("automations") else _db_save(doc, LOCAL, archive=False)
        old = normalize_folder(wf.get("folder"))
        if old and old != out["folder"]:
            _remember_folders(owner_key(wf), [old])
    _announce_graphs_changed()
    return out


def owner_key(wf: dict[str, Any]) -> str:
    return str((wf.get("owner") or {}).get("id") or LOCAL)


def moved_path(folder: str, src: str, dst: str) -> str | None:
    """Where ``folder`` lands when folder ``src`` moves to ``dst`` (None = not inside it)."""
    if folder != src and not folder.startswith(src + "/"):
        return None
    return normalize_folder(dst + folder[len(src):] if dst else folder[len(src):])


def move_folder(owner: str, path: str, new_path: str) -> int:
    """Rename or move a folder (and its subfolders) inside one owner. Deleting a folder
    is moving it into its parent: the workflows stay. Returns how many moved."""
    src, dst = normalize_folder(path), normalize_folder(new_path)
    if not src:
        raise ValueError("Choose a folder to move")
    if dst == src or dst.startswith(src + "/"):
        if dst != src:
            raise ValueError("A folder can't move inside itself")
        return 0
    key = (owner or LOCAL).strip() or LOCAL
    moved = 0
    with _SAVE_LOCK:
        for wf in all_workflows():
            if owner_key(wf) != key:
                continue
            folder = moved_path(normalize_folder(wf.get("folder")), src, dst)
            if folder is None:
                continue
            # A folder is filing, not an edit: no saved version for it.
            doc = {"id": wf["id"], "folder": folder}
            _files_save(doc, archive=False) if not use_db("automations") else _db_save(doc, key, archive=False)
            moved += 1
        listed = listed_folders(key)
        relisted = [f if (to := moved_path(f, src, dst)) is None else to for f in listed]
        changed = relisted != listed and _put_folders(key, relisted)
    if moved or changed:
        _announce_graphs_changed()
    return moved


# --------------------------------------------------------------------------- folder lists


def folders_of(owner: str = LOCAL, workflows: list[dict[str, Any]] | None = None) -> list[str]:
    """Every folder of an owner: its folder list (empty folders too) and each folder a
    workflow is filed in, with the folders around them, sorted. ``workflows``: rows
    already read (any owner's), so a caller listing every owner reads them once."""
    key = (owner or LOCAL).strip() or LOCAL
    paths = set(listed_folders(key))
    rows = all_workflows() if workflows is None else workflows
    paths.update(normalize_folder(wf.get("folder")) for wf in rows if owner_key(wf) == key)
    out: set[str] = set()
    for path in paths:
        parts = path.split("/") if path else []
        out.update("/".join(parts[: i + 1]) for i in range(len(parts)))
    return sorted(out, key=lambda p: [part.casefold() for part in p.split("/")])


def add_folder(owner: str, path: str) -> list[str]:
    """Make a folder (it may hold nothing yet); a team's folders sync with its workflows.
    Returns the owner's folders."""
    clean = normalize_folder(path)
    if not clean:
        raise ValueError("Name the folder")
    key = (owner or LOCAL).strip() or LOCAL
    with _SAVE_LOCK:
        changed = _remember_folders(key, [clean])
    if changed:
        _announce_graphs_changed()
    return folders_of(key)


def drop_folders(owner: str, path: str) -> bool:
    """Forget a folder and everything under it in the owner's folder list (its
    workflows are not touched). False when nothing was listed there."""
    src = normalize_folder(path)
    key = (owner or LOCAL).strip() or LOCAL
    listed = listed_folders(key)
    kept = [f for f in listed if moved_path(f, src, src) is None] if src else []
    return kept != listed and _put_folders(key, kept)


def _remember_folders(owner: str, paths: list[str]) -> bool:
    listed = listed_folders(owner)
    new = [p for p in (normalize_folder(p) for p in paths) if p and p not in listed]
    return _put_folders(owner, listed + new) if new else False


def listed_folders(owner: str) -> list[str]:
    """The owner's folder list as kept (synced for a team), without the folders its
    workflows imply; :func:`folders_of` has both."""
    if not use_db("automations"):
        if owner != LOCAL:
            return []
        try:
            raw = json.loads((_files_dir() / _FOLDERS_FILE).read_text(encoding="utf-8")).get("folders")
        except (OSError, ValueError, AttributeError):
            return []
    else:
        from backend.automations import owned

        try:
            raw = owned.folders(owned.scope_for(owner))
        except ValueError:
            return []
    paths = [normalize_folder(p) for p in raw if isinstance(p, str)] if isinstance(raw, list) else []
    return list(dict.fromkeys(p for p in paths if p))


def _put_folders(owner: str, paths: list[str]) -> bool:
    """Store an owner's folder list; False when it already was that."""
    clean = list(dict.fromkeys(p for p in (normalize_folder(p) for p in paths) if p))
    if clean == listed_folders(owner):
        return False
    if not use_db("automations"):
        if owner != LOCAL:
            raise ValueError(f"unknown workflow owner: {owner}")
        path = _files_dir() / _FOLDERS_FILE
        temporary = path.with_suffix(f".{uuid.uuid4()}.tmp")
        temporary.write_text(json.dumps({"folders": clean}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return True
    from backend.automations import owned

    owned.write_folders(owned.writable(owned.scope_for(owner)), clean)
    return True


def set_run_here(workflow_id: str, on: bool) -> dict[str, Any] | None:
    """Team workflows: whether this PC runs its schedule and triggers."""
    if not use_db("automations"):
        return get_workflow(workflow_id)
    from backend.automations import owned

    found = owned.find(workflow_id)
    if found is None:
        return None
    owned.set_run_here(found[0]["account"], str(found[1]["id"]), on)
    _announce_graphs_changed()
    return get_workflow(workflow_id)


def runs_here(wf: dict[str, Any]) -> bool:
    """Local workflows run where they live; a team one only where a member said so,
    so a team schedule never fires once per member."""
    owner = wf.get("owner") or {}
    return owner.get("kind", LOCAL) == LOCAL or bool(wf.get("run_here"))


def append_run(workflow_id: str, run: dict[str, Any]) -> None:
    """Per-PC run log; never touches the (synced) workflow doc."""
    at = float(run.get("ended") or run.get("started") or time.time())
    if not use_db("automations"):
        wf = _read_file(_files_dir() / f"{workflow_id}.json") if _safe_file_id(workflow_id) else None
        if wf is None:
            return
        wf["runs"] = (list(wf.get("runs") or []) + [run])[-_RUN_CAP:]
        wf["last_run"] = at
        _files_put(wf)
        return
    from backend.automations import owned

    found = owned.find(workflow_id)
    if found is not None:
        owned.append_run(found[0]["account"], str(found[1]["id"]), run, at)


def clear_runs(workflow_id: str) -> bool:
    """Empty this PC's run log for one workflow (Clear log); the workflow is untouched."""
    if not use_db("automations"):
        wf = _read_file(_files_dir() / f"{workflow_id}.json") if _safe_file_id(workflow_id) else None
        if wf is None:
            return False
        wf["runs"] = []
        _files_put(wf)
        return True
    from backend.automations import owned

    found = owned.find(workflow_id)
    if found is None:
        return False
    owned.clear_runs(found[0]["account"], str(found[1]["id"]))
    return True


# --------------------------------------------------------------------------- views


def _view(doc: dict[str, Any], owner: dict[str, Any], state: dict[str, Any], runs: list[Any]) -> dict[str, Any]:
    return {
        "id": str(doc["id"]),
        "name": str(doc.get("name") or ""),
        "description": str(doc.get("description") or ""),
        "enabled": bool(doc.get("enabled", True)),
        "folder": normalize_folder(doc.get("folder")),
        "graph": normalize_graph(doc.get("graph")),
        "updated": float(doc.get("updated") or 0.0),
        "runs": list(runs),
        "last_run": float(state.get("last_run") or 0.0),
        "run_here": bool(state.get("run_here")),
        "owner": dict(owner),
    }


def _summary(wf: dict[str, Any]) -> dict[str, Any]:
    nodes = (wf.get("graph") or {}).get("nodes") or []
    return {
        "id": wf["id"],
        "name": wf.get("name") or "",
        "description": str(wf.get("description") or ""),
        "enabled": bool(wf.get("enabled")),
        "folder": normalize_folder(wf.get("folder")),
        "updated": float(wf.get("updated") or 0.0),
        "last_run": float(wf.get("last_run") or 0.0),
        "node_count": len(nodes),
        "owner": wf.get("owner") or dict(_LOCAL_OWNER),
        "run_here": bool(wf.get("run_here")),
        "trigger": trigger_of(nodes),
        "signature": signature_of(nodes),
    }


def trigger_of(nodes: list[dict[str, Any]]) -> dict[str, str]:
    """What starts it, for the list badge: schedule > event > function > chat > manual."""
    cron = next((n for n in nodes if n.get("type") == "start.cron"), None)
    if cron is not None:
        cfg = cron.get("config") or {}
        try:
            every = float(cfg.get("interval_seconds") or 0)
        except (TypeError, ValueError):
            every = 0.0
        return {"kind": "schedule", "label": _every(every) if every > 0 else str(cfg.get("cron") or "Schedule")}
    types = {str(n.get("type") or "") for n in nodes}
    if (types - _BUILTIN_STARTERS) & _trigger_types():
        return {"kind": "event", "label": "Event"}
    if "flow.input" in types:
        return {"kind": "function", "label": "Function"}
    if "start.chat" in types:
        return {"kind": "chat", "label": "Chat"}
    return {"kind": "manual", "label": "Manual"}


def _every(seconds: float) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"Every {int(seconds // size)}{unit}"
    return f"Every {int(seconds)}s"


def _trigger_types() -> set[str]:
    try:
        from backend.automations.catalog import trigger_types

        return trigger_types()
    except Exception:
        return set()


# --------------------------------------------------------------------------- database mode


def _db_save(doc: dict[str, Any], owner: str, *, archive: bool = True) -> dict[str, Any]:
    from backend.automations import owned
    from backend.store.repos import automations as versions_repo
    from backend.uefn_plugins.scopes import valid_doc_key

    wid = str(doc.get("id") or "").strip()
    found = owned.find(wid) if wid else None
    if found is not None:
        scope, existing = found
    else:
        scope, existing = owned.scope_for(owner or LOCAL), None
        if wid and (not valid_doc_key(wid) or wid == owned.FOLDERS_KEY):
            raise ValueError("Workflow ids use lowercase letters, digits, '.', '_' and '-'.")
    owned.writable(scope)
    out = deepcopy(existing) if existing else empty_workflow(name=str(doc.get("name") or "Untitled"))
    if wid:
        out["id"] = wid
    _merge(out, doc, team=scope["kind"] == "team")
    if archive:  # filing keeps its place in the list
        out["updated"] = time.time()
    aid = scope["account"]
    first = (existing,) if existing and not versions_repo.has_versions(wid) else ()
    owned.write(scope, out, versions=(*first, out) if archive else ())
    wid = str(out["id"])
    if existing is None and scope["kind"] == "team":
        owned.set_run_here(aid, wid, True)  # on for the member who made it
    return _view(out, owned.owner_view(scope), owned.state(aid, wid), owned.runs(aid, wid))


def _merge(out: dict[str, Any], doc: dict[str, Any], *, team: bool = False) -> None:
    if "name" in doc:
        out["name"] = str(doc.get("name") or "").strip() or out["name"]
    if "description" in doc:
        out["description"] = str(doc.get("description") or "")
    if "enabled" in doc:
        out["enabled"] = bool(doc.get("enabled"))
    if "folder" in doc:
        out["folder"] = normalize_folder(doc.get("folder"))
    if "graph" in doc:
        out["graph"] = _with_code(normalize_graph(doc.get("graph")), out.get("graph"), team=team)


# --------------------------------------------------------------------------- custom code nodes


def _has_code(nodes: Any) -> bool:
    return any(isinstance(n, dict) and n.get("type") == CODE_TYPE for n in nodes or [])


def _forget_approvals(workflow_id: str) -> None:
    try:
        from backend.automations.code_approval import forget

        forget(workflow_id)
    except Exception:
        pass


def _with_code(graph: dict[str, Any], before: Any, *, team: bool) -> dict[str, Any]:
    """Every Custom code node as saved: blank code filled in, its sha and problems
    refreshed, and its pins, settings and tools read from the code (kept from the last
    good check while the code has errors, so wires survive a half-typed edit). A node
    sent without its code but with the saved code's sha keeps the saved code. Saves
    are refused only for code that is too big, or for code in a team's workflow."""
    nodes = [n for n in graph.get("nodes") or [] if n.get("type") == CODE_TYPE]
    if not nodes:
        return graph
    if team:
        from backend.automations.code_approval import TEAM_REFUSAL

        raise ValueError(TEAM_REFUSAL)
    from backend.automations import code_check

    old = {str(n.get("id")): n for n in normalize_graph(before).get("nodes") or [] if n.get("type") == CODE_TYPE}
    total = 0
    for node in nodes:
        cfg = node["config"]
        name = node.get("label") or node["id"]
        was = (old.get(node["id"]) or {}).get("config") or {}
        code = cfg.get("code")
        if not isinstance(code, str):
            sha = str(cfg.get("code_sha") or "")
            if sha and sha == str(was.get("code_sha") or "") and isinstance(was.get("code"), str):
                code = was["code"]
            elif sha:
                raise ValueError(f"{name}: it came without its code and its code_sha isn't the saved one. "
                                 "Send the code, or read the workflow again for the current code_sha.")
            else:
                code = ""
        if not code.strip():
            from backend.automations.code_api import BLANK_CODE

            code = BLANK_CODE
        size = code_check.code_bytes(code)
        if size > code_check.MAX_NODE_BYTES:
            raise ValueError(f"{name}: its code is {size // 1024} KB; a node holds at most {code_check.MAX_NODE_BYTES // 1024} KB.")
        total += size
        checked = code_check.check(code)
        cfg["code"] = code
        cfg["code_sha"] = checked["code_sha"]
        cfg["problems"] = checked["problems"]
        if checked["ok"]:
            cfg["pins"] = checked["pins"]
            cfg["settings_spec"] = checked["settings_spec"]
            cfg["uses"] = checked["uses"]
        else:
            for key, empty in (("pins", {"exec": True, "inputs": [], "outputs": []}), ("settings_spec", []),
                               ("uses", {"tools": [], "builtins": []})):
                kept = was.get(key) if key in was else cfg.get(key)
                cfg[key] = kept if isinstance(kept, type(empty)) else empty
            from backend.automations.pins import clean_pins

            pins = cfg["pins"]
            cfg["pins"] = {"exec": pins.get("exec", True) is not False,
                           "inputs": clean_pins(pins.get("inputs")), "outputs": clean_pins(pins.get("outputs"))}
        for key in ("settings", "inputs"):
            if not isinstance(cfg.get(key), dict):
                cfg[key] = {}
        cfg["spend"] = cfg.get("spend") is True
        cfg.pop("code_lines", None)  # get_workflow's read-only line count
        if "based_on" in cfg and not (isinstance(cfg["based_on"], dict) and cfg["based_on"].get("type")):
            cfg.pop("based_on")
    if total > code_check.MAX_WORKFLOW_BYTES:
        raise ValueError(f"This workflow holds {total // 1024} KB of code; the most is {code_check.MAX_WORKFLOW_BYTES // 1024} KB.")
    # A value node has no white pins: a white wire on one would stop the run there.
    values = {str(n["id"]) for n in nodes if n["config"]["pins"].get("exec", True) is False}
    if values:
        graph["edges"] = [e for e in graph.get("edges") or []
                          if e.get("kind") == "data" or not {str(e.get("source")), str(e.get("target"))} & values]
    return graph


def carry_approvals(wf: dict[str, Any], sources: Any) -> None:
    """A copy's Custom code may run on its own where that same node's same code already
    could in one of ``sources`` (workflow ids); anything else waits for a person."""
    from backend.automations.code_approval import approval, approve, is_local, node_sha

    if not wf or not is_local(wf):
        return
    wid = str(wf.get("id") or "")
    for node in (wf.get("graph") or {}).get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != CODE_TYPE:
            continue
        nid, sha = str(node.get("id") or ""), node_sha(node)
        for source in sources:
            row = approval(str(source), nid, sha) if str(source) != wid else None
            if row:
                approve(wid, nid, sha, str(row.get("by") or ""))
                break


# --------------------------------------------------------------------------- files mode (Local only)


def _files_view(row: dict[str, Any]) -> dict[str, Any]:
    return _view(row, _LOCAL_OWNER, {"last_run": row.get("last_run"), "run_here": False}, list(row.get("runs") or []))


def _files_save(doc: dict[str, Any], *, archive: bool = True) -> dict[str, Any]:
    from backend.automations.versions import archive_file, directory

    wid = str(doc.get("id") or "").strip()
    if wid and (not _safe_file_id(wid) or f"{wid}.json" == _FOLDERS_FILE):
        raise ValueError("invalid workflow id")
    existing = _read_file(_files_dir() / f"{wid}.json") if wid else None
    out = deepcopy(existing) if existing else {**empty_workflow(name=str(doc.get("name") or "Untitled")),
                                                "runs": [], "last_run": 0.0}
    if wid:
        out["id"] = wid
    _merge(out, doc)
    if archive:
        out["updated"] = time.time()
        if existing and not any(directory(out["id"]).glob("*.json")):
            archive_file(existing)
        archive_file(out)
    _files_put(out)
    return _files_view(out)


def _safe_file_id(workflow_id: str) -> bool:
    """File names come from ids: never a path separator or a dot-dot."""
    return bool(workflow_id) and all(c.isalnum() or c in "-_." for c in workflow_id) and ".." not in workflow_id


def _files_all() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(_files_dir().glob("*.json")):
        row = _read_file(path)
        if row:
            out.append(row)
    out.sort(key=lambda w: (-float(w.get("updated") or 0.0), str(w.get("name") or "")))
    return out


def _files_put(doc: dict[str, Any]) -> None:
    path = _files_dir() / f"{doc['id']}.json"
    temporary = path.with_suffix(f".{uuid.uuid4()}.tmp")
    temporary.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _files_delete(workflow_id: str) -> bool:
    path = _files_dir() / f"{workflow_id}.json"
    if not path.is_file():
        return False
    path.unlink()
    return True


def _files_dir() -> Path:
    path = resolve_app_data_dir(for_write=True) / "automations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    data["graph"] = normalize_graph(data.get("graph"))
    if not isinstance(data.get("runs"), list):
        data["runs"] = []
    data.pop("kind", None)
    data["description"] = str(data.get("description") or "")
    return data
