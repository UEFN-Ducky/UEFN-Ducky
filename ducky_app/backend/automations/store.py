"""Persist automation graphs (db or AppData JSON files)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from backend.store.switch import use_db
from frontend.app_paths import resolve_app_data_dir

_RUN_CAP = 20
KIND_AUTOMATION = "automation"
KIND_PIPELINE = "pipeline"
_KINDS = frozenset({KIND_AUTOMATION, KIND_PIPELINE})


def _announce_graphs_changed() -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "graphs_changed"})
    except Exception:
        pass


def normalize_kind(raw: Any) -> str:
    key = str(raw or "").strip().lower()
    return key if key in _KINDS else KIND_AUTOMATION


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
    ids = {n["id"] for n in nodes}
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for e in src.get("edges") or []:
        if not isinstance(e, dict):
            continue
        source = str(e.get("source") or "").strip()
        target = str(e.get("target") or "").strip()
        kind = str(e.get("kind") or "main").strip() or "main"
        key = (source, target, kind)
        if source in ids and target in ids and source != target and key not in seen:
            seen.add(key)
            edges.append({"source": source, "target": target, "kind": kind})
    return {"nodes": nodes, "edges": edges}


def empty_workflow(*, name: str = "Untitled", kind: str = KIND_AUTOMATION) -> dict[str, Any]:
    now = time.time()
    return {
        "id": str(uuid.uuid4()),
        "name": (name or "Untitled").strip() or "Untitled",
        "kind": normalize_kind(kind),
        "description": "",
        "enabled": True,
        "graph": {"nodes": [], "edges": []},
        "runs": [],
        "updated": now,
        "last_run": 0.0,
    }


def list_automations(kind: str = KIND_AUTOMATION) -> list[dict[str, Any]]:
    want = normalize_kind(kind) if kind else ""
    rows = _all()
    if want:
        rows = [w for w in rows if normalize_kind(w.get("kind")) == want]
    return [_summary(w) for w in rows]


def get_automation(workflow_id: str) -> dict[str, Any] | None:
    wid = (workflow_id or "").strip()
    if not wid:
        return None
    return _get(wid)


def save_automation(doc: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    existing = _get(str(doc.get("id") or "").strip()) if doc.get("id") else None
    seed_kind = doc.get("kind") if existing is None else existing.get("kind")
    out = existing or empty_workflow(
        name=str(doc.get("name") or "Untitled"),
        kind=str(seed_kind or KIND_AUTOMATION),
    )
    if str(doc.get("id") or "").strip():
        out["id"] = str(doc["id"]).strip()
    if "name" in doc:
        out["name"] = str(doc.get("name") or "").strip() or out["name"]
    if "kind" in doc:
        out["kind"] = normalize_kind(doc.get("kind"))
    if "description" in doc:
        out["description"] = str(doc.get("description") or "")
    if "enabled" in doc:
        out["enabled"] = bool(doc.get("enabled"))
    if "graph" in doc:
        out["graph"] = normalize_graph(doc.get("graph"))
    if "runs" in doc and isinstance(doc.get("runs"), list):
        out["runs"] = list(doc["runs"])[-_RUN_CAP:]
    out["updated"] = now
    if "last_run" in doc:
        out["last_run"] = float(doc.get("last_run") or 0.0)
    _put(out)
    _announce_graphs_changed()
    return out


def delete_automation(workflow_id: str) -> bool:
    wid = (workflow_id or "").strip()
    ok = bool(wid) and _delete(wid)
    if ok:
        _announce_graphs_changed()
    return ok


def append_run(workflow_id: str, run: dict[str, Any]) -> dict[str, Any] | None:
    wf = _get(workflow_id)
    if wf is None:
        return None
    runs = list(wf.get("runs") or [])
    runs.append(run)
    wf["runs"] = runs[-_RUN_CAP:]
    wf["last_run"] = float(run.get("ended") or run.get("started") or time.time())
    wf["updated"] = time.time()
    _put(wf)
    return wf


def _summary(wf: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": wf["id"],
        "name": wf.get("name") or "",
        "kind": normalize_kind(wf.get("kind")),
        "description": str(wf.get("description") or ""),
        "enabled": bool(wf.get("enabled")),
        "updated": float(wf.get("updated") or 0.0),
        "last_run": float(wf.get("last_run") or 0.0),
        "node_count": len((wf.get("graph") or {}).get("nodes") or []),
    }


def _all() -> list[dict[str, Any]]:
    if use_db("automations"):
        from backend.store.repos import automations as repo

        return repo.list_all()
    out: list[dict[str, Any]] = []
    for path in sorted(_files_dir().glob("*.json")):
        row = _read_file(path)
        if row:
            out.append(row)
    out.sort(key=lambda w: (-float(w.get("updated") or 0.0), str(w.get("name") or "")))
    return out


def _get(workflow_id: str) -> dict[str, Any] | None:
    if use_db("automations"):
        from backend.store.repos import automations as repo

        return repo.get(workflow_id)
    return _read_file(_files_dir() / f"{workflow_id}.json")


def _put(doc: dict[str, Any]) -> None:
    if use_db("automations"):
        from backend.store.repos import automations as repo

        repo.put(doc)
        return
    path = _files_dir() / f"{doc['id']}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _delete(workflow_id: str) -> bool:
    if use_db("automations"):
        from backend.store.repos import automations as repo

        return repo.delete(workflow_id)
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
    data["kind"] = normalize_kind(data.get("kind"))
    data["description"] = str(data.get("description") or "")
    return data
