"""Folder bundles: a folder of workflows, nested folders and empty ones included, as
one value, so a whole tree can be copied, moved to or from a team, or kept as a
template, with every Run workflow step still pointing at the right workflow.

A bundle (version 1)::

    {"version": 1, "root": "BrainRot TCG",
     "folders": ["Functions", "Functions/Cards", "Art"],
     "workflows": [{"key": "<id it had>", "name": "Open pack", "description": "",
                    "enabled": True, "folder": "Functions", "graph": {...}}]}

``folders`` and each workflow's ``folder`` are relative to the root (``""`` = the root
itself). ``key`` names a workflow inside the bundle: a Run workflow step (or a plugin
step setting of type ``workflow``) holding a key, or ``"@key"``, is pointed at the new
id when the bundle is imported. Ids of workflows outside the bundle are kept as they are.
"""

from __future__ import annotations

import time
import uuid
from copy import deepcopy
from typing import Any

from backend.automations import store

BUNDLE_VERSION = 1
MAX_WORKFLOWS = 500
MAX_FOLDERS = 1000
_CALL_FIELDS: dict[str, tuple[str, ...]] = {"workflow.call": ("workflow_id",)}


class BundleError(ValueError):
    """The bundle can't be read (the message says why)."""


# --------------------------------------------------------------------------- references


def workflow_fields() -> dict[str, tuple[str, ...]]:
    """Node type → the settings that name another workflow (Run workflow, and any plugin
    step with a setting of type ``workflow``)."""
    out = dict(_CALL_FIELDS)
    try:
        from backend.automations.catalog import node_specs

        specs = node_specs()
    except Exception:
        return out
    for ntype, spec in specs.items():
        ids = tuple(str(f.get("id")) for f in spec.get("config_fields") or []
                    if isinstance(f, dict) and f.get("type") == "workflow" and f.get("id"))
        if ids:
            out[ntype] = tuple(dict.fromkeys(out.get(ntype, ()) + ids))
    return out


def _mapped(ref: Any, idmap: dict[str, str]) -> str | None:
    if not isinstance(ref, str):
        return None
    ref = ref.strip()
    if ref in idmap:
        return idmap[ref]
    if ref.startswith("@") and ref[1:] in idmap:
        return idmap[ref[1:]]
    return None


def remap_calls(graph: Any, idmap: dict[str, str], *, fields: dict[str, tuple[str, ...]] | None = None) -> dict[str, Any]:
    """A copy of ``graph`` whose workflow references go through ``idmap`` (old id or key
    → new id; ``"@key"`` works too). References not in ``idmap`` are kept."""
    out = deepcopy(graph) if isinstance(graph, dict) else {"nodes": [], "edges": []}
    fields = workflow_fields() if fields is None else fields
    for node in out.get("nodes") or []:
        if not isinstance(node, dict) or not isinstance(node.get("config"), dict):
            continue
        cfg = node["config"]
        for fid in fields.get(str(node.get("type") or ""), ()):
            to = _mapped(cfg.get(fid), idmap)
            if to is not None:
                cfg[fid] = to
    return out


def calls_of(graph: Any, *, fields: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    """Every workflow a graph names (Run workflow steps and workflow settings)."""
    fields = workflow_fields() if fields is None else fields
    out: list[str] = []
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    for node in nodes or []:
        cfg = node.get("config") if isinstance(node, dict) else None
        if not isinstance(cfg, dict):
            continue
        for fid in fields.get(str(node.get("type") or ""), ()):
            ref = cfg.get(fid)
            if isinstance(ref, str) and ref.strip() and ref.strip() not in out:
                out.append(ref.strip())
    return out


# --------------------------------------------------------------------------- export


def _owner_key(owner: str) -> str:
    return (owner or store.LOCAL).strip() or store.LOCAL


def _owner_label(key: str) -> str:
    if not store.use_db("automations"):
        if key != store.LOCAL:
            raise ValueError(f"unknown workflow owner: {key}")
        return "Local"
    from backend.automations import owned

    return str(owned.owner_view(owned.scope_for(key))["label"])


def _relative(folder: str, root: str) -> str | None:
    """``folder`` inside ``root`` as a relative path (``""`` = root), None = outside."""
    if not root:
        return folder
    if folder == root:
        return ""
    return folder[len(root) + 1:] if folder.startswith(root + "/") else None


def export_folder(owner: str, path: str) -> dict[str, Any]:
    """Everything under folder ``path`` of ``owner`` (``""`` = the whole owner) as a
    bundle: every nested folder, empty ones too, and every workflow with its graph.
    Raises ``KeyError`` for a folder the owner doesn't have."""
    key = _owner_key(owner)
    root = store.normalize_folder(path)
    label = _owner_label(key)
    rows = [wf for wf in store.all_workflows() if store.owner_key(wf) == key]
    known = store.folders_of(key, rows)
    if root and root not in known:
        raise KeyError("folder not found")
    folders = [rel for f in known if (rel := _relative(f, root))]
    workflows = []
    for wf in rows:
        rel = _relative(store.normalize_folder(wf.get("folder")), root)
        if rel is None:
            continue
        workflows.append({
            "key": str(wf["id"]),
            "name": str(wf.get("name") or ""),
            "description": str(wf.get("description") or ""),
            "enabled": bool(wf.get("enabled", True)),
            "folder": rel,
            "graph": deepcopy(wf.get("graph") or {"nodes": [], "edges": []}),
        })
    workflows.sort(key=lambda w: ([p.casefold() for p in w["folder"].split("/")] if w["folder"] else [], w["name"].casefold()))
    return {"version": BUNDLE_VERSION, "root": root.rsplit("/", 1)[-1] if root else label,
            "folders": folders, "workflows": workflows}


# --------------------------------------------------------------------------- import


def _root_name(raw: Any) -> str:
    return store.normalize_folder(str(raw or "").replace("/", " ").replace("\\", " "))


def clean_bundle(bundle: Any) -> dict[str, Any]:
    """A bundle checked and tidied (paths normalized, every workflow keyed once).
    Raises :class:`BundleError` when it can't be used."""
    if not isinstance(bundle, dict):
        raise BundleError("A folder bundle is a JSON object.")
    version = bundle.get("version", BUNDLE_VERSION)
    if version != BUNDLE_VERSION:
        raise BundleError(f"This folder bundle is version {version}; this app reads version {BUNDLE_VERSION}.")
    raw_rows = bundle.get("workflows")
    if not isinstance(raw_rows, list):
        raise BundleError("A folder bundle needs a workflows list.")
    if len(raw_rows) > MAX_WORKFLOWS:
        raise BundleError(f"A folder bundle holds at most {MAX_WORKFLOWS} workflows.")
    raw_folders = bundle.get("folders") or []
    if not isinstance(raw_folders, list):
        raise BundleError("A folder bundle's folders is a list of paths.")
    folders = list(dict.fromkeys(f for f in (store.normalize_folder(p) for p in raw_folders if isinstance(p, str)) if f))
    if len(folders) > MAX_FOLDERS:
        raise BundleError(f"A folder bundle holds at most {MAX_FOLDERS} folders.")
    rows: list[dict[str, Any]] = []
    keys: set[str] = set()
    for index, row in enumerate(raw_rows):
        if not isinstance(row, dict):
            raise BundleError(f"Workflow {index + 1} of the bundle isn't an object.")
        key = str(row.get("key") or row.get("id") or "").strip() or f"workflow-{index + 1}"
        if key in keys:
            raise BundleError(f"Two workflows in the bundle share the key {key!r}.")
        keys.add(key)
        graph = row.get("graph")
        rows.append({
            "key": key,
            "name": str(row.get("name") or "").strip() or "Untitled",
            "description": str(row.get("description") or ""),
            "enabled": bool(row.get("enabled", True)),
            "folder": store.normalize_folder(row.get("folder")),
            "graph": store.normalize_graph(graph if isinstance(graph, dict) else {}),
        })
    return {"version": BUNDLE_VERSION, "root": _root_name(bundle.get("root")) or "Imported", "folders": folders,
            "workflows": rows}


def _join(parent: str, rel: str) -> str:
    return store.normalize_folder(f"{parent}/{rel}" if parent and rel else parent or rel)


def free_folder(owner: str, parent: str, name: str) -> str:
    """``parent/name``, or ``parent/name (2)`` and so on when the owner has that folder."""
    taken = set(store.folders_of(_owner_key(owner)))
    base = _root_name(name) or "Imported"
    path = _join(parent, base)
    n = 2
    while path in taken:
        suffix = f" ({n})"
        path = _join(parent, base[: 64 - len(suffix)].rstrip() + suffix)
        n += 1
    return path


def _refuse_team_code(key: str, rows: list[dict[str, Any]]) -> None:
    if key == store.LOCAL or not store.use_db("automations"):
        return
    from backend.automations import owned

    if owned.scope_for(key)["kind"] == "team" and any(store._has_code(r["graph"].get("nodes")) for r in rows):
        from backend.automations.code_approval import TEAM_REFUSAL

        raise ValueError(TEAM_REFUSAL)


def _discard(key: str, workflow_id: str) -> None:
    """Undo one workflow an unfinished import wrote (only in the owner it went to)."""
    try:
        if not store.use_db("automations"):
            store._files_delete(workflow_id)
            return
        from backend.automations import owned

        owned.remove(owned.scope_for(key), workflow_id)
    except Exception:
        pass


def _restore_folders(key: str, listed: list[str]) -> None:
    try:
        store._put_folders(key, listed)
    except Exception:
        pass


def import_bundle(bundle: Any, owner: str, parent_path: str = "", *, name: str | None = None) -> dict[str, Any]:
    """Make the bundle's tree under ``owner``/``parent_path``/``root`` (``name`` renames
    the root; ``"Name (2)"`` when that folder is taken) with every workflow under a new
    id, Run workflow steps between them re-pointed. All or nothing: a failure removes
    what this import made. Returns ``{folder, owner, workflows: [{key, id, name}],
    outside: [ids called that aren't in the bundle]}``."""
    clean = clean_bundle(bundle)
    key = _owner_key(owner)
    parent = store.normalize_folder(parent_path)
    _owner_label(key)  # unknown owner: ValueError before anything is written
    rows = clean["workflows"]
    _refuse_team_code(key, rows)
    idmap = {row["key"]: str(uuid.uuid4()) for row in rows}
    fields = workflow_fields()
    outside = sorted({ref for row in rows for ref in calls_of(row["graph"], fields=fields)
                      if _mapped(ref, idmap) is None})
    made: list[dict[str, Any]] = []
    with store._SAVE_LOCK:
        root = free_folder(key, parent, name or clean["root"])
        before = store.listed_folders(key)
        store._remember_folders(key, [root] + [_join(root, f) for f in clean["folders"]])  # read-only owner: raises here
        try:
            for row in rows:
                doc = {"id": idmap[row["key"]], "name": row["name"], "description": row["description"],
                       "enabled": row["enabled"], "folder": _join(root, row["folder"]),
                       "graph": remap_calls(row["graph"], idmap, fields=fields)}
                made.append(store.save_quietly(doc, key))
        except BaseException:
            for wf in made:
                _discard(key, str(wf["id"]))
            _restore_folders(key, before)
            raise
    store._announce_graphs_changed()
    return {"folder": root, "owner": key, "outside": outside,
            "workflows": [{"key": row["key"], "id": idmap[row["key"]], "name": row["name"]} for row in rows]}


# --------------------------------------------------------------------------- copy and move


def copy_folder(owner_from: str, path: str, owner_to: str, parent_path: str = "", *, move: bool = False) -> dict[str, Any]:
    """Copy folder ``path`` of ``owner_from`` with everything in it, nested as it is,
    into ``owner_to``/``parent_path`` (Local or a team). A copy gets new ids with its
    Run workflow steps re-pointed; a move to another owner keeps every id (calls from
    outside keep working) and removes the source only once the whole tree is in place.
    Inside one owner a move is a folder move. Returns :func:`import_bundle`'s shape
    plus ``moved``."""
    src_key, dst_key = _owner_key(owner_from), _owner_key(owner_to)
    src = store.normalize_folder(path)
    if not src:
        raise ValueError("Choose a folder to copy")
    parent = store.normalize_folder(parent_path)
    if src_key != dst_key and not store.use_db("automations"):
        raise ValueError("Sharing with a team needs the database store")
    if move and src_key == dst_key:
        if parent == src.rpartition("/")[0]:  # already there
            return {"folder": src, "owner": dst_key, "outside": [], "workflows": [], "moved": 0}
        dst = free_folder(dst_key, parent, src.rsplit("/", 1)[-1])
        count = store.move_folder(src_key, src, dst)
        return {"folder": dst, "owner": dst_key, "outside": [], "workflows": [], "moved": count}
    bundle = export_folder(src_key, src)
    if not move:
        out = import_bundle(bundle, dst_key, parent)
        for row in out["workflows"]:
            copied = store.get_workflow(row["id"])
            if copied:
                store.carry_approvals(copied, [row["key"]])
        return {**out, "moved": 0}
    return _move_tree(bundle, src_key, src, dst_key, parent)


def _move_tree(bundle: dict[str, Any], src_key: str, src_path: str, dst_key: str, parent: str) -> dict[str, Any]:
    """Another owner, same ids: write the whole tree there, then remove the source."""
    from backend.automations import owned

    rows = bundle["workflows"]
    src_scope = owned.writable(owned.scope_for(src_key))
    dst_scope = owned.writable(owned.scope_for(dst_key))
    _refuse_team_code(dst_key, rows)
    fields = workflow_fields()
    ids = {row["key"] for row in rows}
    outside = sorted({ref for row in rows for ref in calls_of(row["graph"], fields=fields) if ref not in ids})
    written: list[str] = []
    with store._SAVE_LOCK:
        root = free_folder(dst_key, parent, bundle["root"])
        before = store.listed_folders(dst_key)
        store._remember_folders(dst_key, [root] + [_join(root, f) for f in bundle["folders"]])
        try:
            for row in rows:
                doc = {"id": row["key"], "name": row["name"], "description": row["description"],
                       "enabled": row["enabled"], "folder": _join(root, row["folder"]),
                       "graph": row["graph"], "updated": time.time()}
                owned.write(dst_scope, doc, versions=(doc,))
                written.append(row["key"])
        except BaseException:
            for wid in written:
                try:
                    owned.remove(dst_scope, wid)
                except Exception:
                    pass
            _restore_folders(dst_key, before)
            raise
        if dst_scope["kind"] == "team":
            for wid in written:
                owned.set_run_here(dst_scope["account"], wid, True)
        for wid in written:
            owned.remove(src_scope, wid)
        store.drop_folders(src_key, src_path)
    store._announce_graphs_changed()
    return {"folder": root, "owner": dst_key, "outside": outside, "moved": len(written),
            "workflows": [{"key": row["key"], "id": row["key"], "name": row["name"]} for row in rows]}
