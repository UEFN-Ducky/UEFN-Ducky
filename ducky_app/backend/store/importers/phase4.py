"""Phase 4 importers: plan templates, project memory, the usage ledger and the
rolling JSONL logs (once per database), plus a per-project fold of
``<project>/.ducky/plans`` and ``<project>/.ducky/tasks`` (once per project)
that moves task artifacts under AppData and leaves the island without side
files (ADR 0001).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from backend.store.importers.phase1 import _move_to_legacy, _read_json, once
from backend.store.repos import events, kv, memory, plans, usage


# --------------------------------------------------------------------------- templates


def import_templates(root: Path) -> dict[str, Any]:
    from backend.agent.coding_agents.plans import _normalize_plan_doc

    report = {"templates": 0}
    directory = root / "plan_templates"
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            data = _read_json(path)
            if not isinstance(data, dict):
                continue
            doc = _normalize_plan_doc(data, kind="template")
            tid = str(doc.get("id") or path.stem).strip()
            if tid:
                plans.plan_put(plans.TEMPLATE_PROJECT, tid, "template", doc)
                report["templates"] += 1
        _move_to_legacy(root, directory, "plan_templates")
    return report


# --------------------------------------------------------------------------- memory


def import_memory(root: Path) -> dict[str, Any]:
    from backend.memory.project import _default_description, _parse_entry

    report = {"projects": 0, "entries": 0}
    base = root / "memory" / "projects"
    if not base.is_dir():
        return report

    def _put(project_id: str, name: str, path: Path) -> None:
        try:
            meta, body = _parse_entry(path.read_text(encoding="utf-8"))
        except OSError:
            return
        memory.put(
            project_id,
            name,
            description=meta.get("description") or _default_description(body),
            author=meta.get("author") or "",
            updated=meta.get("updated") or "",
            body=body.strip(),
        )
        report["entries"] += 1

    for project_dir in sorted(base.iterdir()):
        if not project_dir.is_dir():
            continue
        project_id = project_dir.name
        report["projects"] += 1
        for path in sorted(project_dir.glob("*.md")):
            _put(project_id, path.stem, path)
        for sub_dir in sorted(project_dir.iterdir()):
            if not sub_dir.is_dir():
                continue
            main = sub_dir / "MEMORY.md"
            if main.is_file():
                _put(project_id, sub_dir.name, main)
            for sub in sorted(sub_dir.glob("*.md")):
                if sub.name != "MEMORY.md":
                    _put(project_id, f"{sub_dir.name}/{sub.stem}", sub)
    _move_to_legacy(root, root / "memory", "memory")
    return report


# --------------------------------------------------------------------------- usage


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def import_usage(root: Path) -> dict[str, Any]:
    path = root / "provider_usage.jsonl"
    if not path.is_file():
        return {"rows": 0}
    n = 0
    for row in _jsonl_rows(path):
        if not row.get("provider"):
            continue
        entry = {c: row.get(c) for c in usage.COLUMNS}
        for c in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            entry[c] = int(entry.get(c) or 0)
        entry["ts"] = float(entry.get("ts") or 0.0)
        for c in ("provider", "model", "conv_id", "agent", "ducky_label"):
            entry[c] = str(entry.get(c) or "")
        usage.insert(entry)
        n += 1
    _move_to_legacy(root, path, "usage")
    return {"rows": n}


# --------------------------------------------------------------------------- events


_LOG_KINDS = (
    ("errors.jsonl", "error", False),
    ("activity.jsonl", "activity", False),
    ("agent_crashes.jsonl", "agent_crash", True),
    ("verse_error_stats.jsonl", "verse_stat", True),
)


def import_logs(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name, kind, structured in _LOG_KINDS:
        path = root / name
        if not path.is_file():
            continue
        rows = _jsonl_rows(path)
        if structured:
            events.insert_many(kind, [{"ts": r.get("ts"), "payload": r} for r in rows])
        else:
            events.insert_many(kind, [{"ts": r.get("ts"), "source": r.get("source"), "message": r.get("message")} for r in rows])
        report[kind] = len(rows)
        _move_to_legacy(root, path, "logs")
    return report


ALL = {
    "plan_templates": import_templates,
    "memory": import_memory,
    "usage": import_usage,
    "logs": import_logs,
}


def ensure(name: str) -> None:
    once(name, ALL[name])


# --------------------------------------------------------------------------- per-project .ducky fold


def _project_flag(slug: str) -> str:
    return f"imported:project_dotducky:{slug}"


def ensure_project(project_root: str) -> dict[str, Any] | None:
    """Fold ``<project>/.ducky/plans`` and ``.ducky/tasks`` into rows, once per project."""
    root = (project_root or "").strip()
    if not root:
        return None
    from backend.agent.coding_agents.plans import _normalize_plan_doc
    from backend.store import db
    from frontend.ui_web.project_chats import project_slug

    slug = project_slug(root)
    flag = _project_flag(slug)
    if kv.meta_get(flag) is not None:
        return None
    report = {"plans": 0, "tasks": 0, "artifacts": 0, "bak_dropped": 0}
    dot = Path(root) / ".ducky"
    plans_dir = dot / "plans"
    if plans_dir.is_dir():
        for path in sorted(plans_dir.iterdir()):
            if not path.is_file():
                continue
            if ".bak." in path.name or path.suffix != ".json":
                report["bak_dropped"] += 1
                continue
            data = _read_json(path)
            if not isinstance(data, dict):
                continue
            doc = _normalize_plan_doc(data, kind="project")
            chat_id = str(doc.get("chat_id") or path.stem).strip()
            if chat_id:
                plans.plan_put(slug, chat_id, "project", doc)
                report["plans"] += 1
    tasks_dir = dot / "tasks"
    app_root = db.app_root()
    if tasks_dir.is_dir():
        dest_root = app_root / "tasks" / slug
        for path in sorted(tasks_dir.glob("*.json")):
            data = _read_json(path)
            if not isinstance(data, dict) or not data.get("id"):
                continue
            tid = str(data["id"])
            src_art = tasks_dir / tid / "artifacts"
            if src_art.is_dir():
                dst_art = dest_root / tid / "artifacts"
                dst_art.mkdir(parents=True, exist_ok=True)
                for f in src_art.iterdir():
                    if f.is_file():
                        shutil.copy2(f, dst_art / f.name)
                        report["artifacts"] += 1
                for art in data.get("artifacts") or []:
                    if isinstance(art, dict) and art.get("name"):
                        art["path"] = str(dst_art / str(art["name"]))
            plans.task_put(slug, data)
            report["tasks"] += 1
    legacy_dir = app_root / "legacy" / "projects" / slug
    for sub in ("plans", "tasks"):
        src = dot / sub
        if src.exists():
            legacy_dir.mkdir(parents=True, exist_ok=True)
            dest = legacy_dir / sub
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            try:
                shutil.move(str(src), str(dest))
            except OSError:
                shutil.copytree(src, dest, dirs_exist_ok=True)
                shutil.rmtree(src, ignore_errors=True)
    try:
        if dot.is_dir() and not any(dot.iterdir()):
            dot.rmdir()
    except OSError:
        pass
    kv.meta_set(flag, json.dumps(report))
    return report
