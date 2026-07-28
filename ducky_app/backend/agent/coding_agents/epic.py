"""UEFN-Ducky Tasks / artifacts / verify helpers."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from frontend.atomic_json import write_json_atomic
from frontend.settings import PanelSettings, default_app_data_dir


def tasks_root(project_root: str | None = None) -> Path:
    root = (project_root or PanelSettings.load().uefn_project_root or "").strip()
    if root:
        d = Path(root) / ".ducky" / "tasks"
    else:
        d = default_app_data_dir() / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifacts_dir(task_id: str, project_root: str | None = None) -> Path:
    d = tasks_root(project_root) / task_id / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_task(
    title: str,
    *,
    goal: str = "",
    conv_ids: list[str] | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    task_id = uuid.uuid4().hex[:12]
    now = time.time()
    task = {
        "id": task_id,
        "title": (title or "Untitled task").strip()[:200],
        "goal": (goal or "").strip(),
        "created": now,
        "updated": now,
        "status": "open",
        "phases": [],
        "conv_ids": list(conv_ids or []),
        "artifacts": [],
    }
    path = tasks_root(project_root) / f"{task_id}.json"
    write_json_atomic(path, task)
    return task


def list_tasks(project_root: str | None = None) -> list[dict[str, Any]]:
    root = tasks_root(project_root)
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id"):
                out.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    return out


def load_task(task_id: str, project_root: str | None = None) -> dict[str, Any] | None:
    path = tasks_root(project_root) / f"{(task_id or '').strip()}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_task(task: dict[str, Any], project_root: str | None = None) -> dict[str, Any]:
    task = dict(task)
    task["updated"] = time.time()
    tid = str(task.get("id") or "").strip()
    if not tid:
        raise ValueError("task id required")
    write_json_atomic(tasks_root(project_root) / f"{tid}.json", task)
    return task


def add_phase(task_id: str, title: str, plan: str = "", project_root: str | None = None) -> dict[str, Any]:
    task = load_task(task_id, project_root)
    if not task:
        raise ValueError("task not found")
    phase = {
        "id": uuid.uuid4().hex[:10],
        "title": (title or "Phase").strip()[:200],
        "plan": (plan or "").strip(),
        "status": "pending",
        "created": time.time(),
    }
    phases = list(task.get("phases") or [])
    phases.append(phase)
    task["phases"] = phases
    return save_task(task, project_root)


def write_artifact(
    task_id: str,
    name: str,
    content: str,
    *,
    kind: str = "spec",
    project_root: str | None = None,
) -> dict[str, Any]:
    task = load_task(task_id, project_root)
    if not task:
        raise ValueError("task not found")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "artifact"))[:80]
    if not safe.endswith(".md"):
        safe += ".md"
    path = artifacts_dir(task_id, project_root) / safe
    path.write_text(content or "", encoding="utf-8")
    entry = {
        "id": uuid.uuid4().hex[:10],
        "name": safe,
        "kind": kind or "spec",
        "path": str(path),
        "updated": time.time(),
    }
    arts = [a for a in (task.get("artifacts") or []) if isinstance(a, dict) and a.get("name") != safe]
    arts.append(entry)
    task["artifacts"] = arts
    save_task(task, project_root)
    return entry


def build_handoff_prompt(task_id: str, phase_id: str = "", project_root: str | None = None) -> str:
    task = load_task(task_id, project_root)
    if not task:
        raise ValueError("task not found")
    lines = [
        f"# Task: {task.get('title')}",
        "",
        "## Goal",
        str(task.get("goal") or "(none)"),
        "",
    ]
    phases = task.get("phases") or []
    target = None
    if phase_id:
        target = next((p for p in phases if isinstance(p, dict) and p.get("id") == phase_id), None)
    if target is None and phases:
        target = next((p for p in phases if isinstance(p, dict) and p.get("status") == "pending"), phases[0])
    if isinstance(target, dict):
        lines.extend(
            [
                f"## Phase: {target.get('title')}",
                str(target.get("plan") or ""),
                "",
                "Implement this phase. When done, summarize what changed.",
            ]
        )
    arts = task.get("artifacts") or []
    if arts:
        lines.append("## Artifacts")
        for a in arts:
            if not isinstance(a, dict):
                continue
            lines.append(f"- {a.get('kind')}: {a.get('name')} ({a.get('path')})")
    return "\n".join(lines)


def verify_against_plan(
    task_id: str,
    *,
    phase_id: str = "",
    implementation_summary: str = "",
    project_root: str | None = None,
) -> dict[str, Any]:
    """Lightweight verify: compare summary to phase plan and emit review comments."""
    task = load_task(task_id, project_root)
    if not task:
        raise ValueError("task not found")
    phases = task.get("phases") or []
    target = None
    if phase_id:
        target = next((p for p in phases if isinstance(p, dict) and p.get("id") == phase_id), None)
    if target is None and phases:
        target = phases[-1] if isinstance(phases[-1], dict) else None
    plan = str((target or {}).get("plan") or task.get("goal") or "").strip()
    summary = (implementation_summary or "").strip()
    comments: list[dict[str, str]] = []
    if not summary:
        comments.append(
            {
                "severity": "Critical",
                "text": "No implementation summary provided — cannot verify against the plan.",
            }
        )
    elif not plan:
        comments.append(
            {
                "severity": "Minor",
                "text": "Task has no written plan; treat the summary as the source of truth.",
            }
        )
    else:
        # Heuristic keyword coverage
        plan_words = {w.lower() for w in plan.replace(",", " ").split() if len(w) > 4}
        summary_l = summary.lower()
        missing = sorted(w for w in plan_words if w not in summary_l)[:12]
        if missing:
            comments.append(
                {
                    "severity": "Major",
                    "text": "Plan topics not clearly addressed in the summary: " + ", ".join(missing),
                }
            )
        else:
            comments.append(
                {
                    "severity": "Minor",
                    "text": "Summary appears to cover the main plan keywords.",
                }
            )
    if isinstance(target, dict):
        target["status"] = "verified" if not any(c["severity"] == "Critical" for c in comments) else "needs_work"
        target["verify_comments"] = comments
        target["verified_at"] = time.time()
        for i, p in enumerate(phases):
            if isinstance(p, dict) and p.get("id") == target.get("id"):
                phases[i] = target
        task["phases"] = phases
        save_task(task, project_root)
    review_md = "# Verification\n\n" + "\n".join(f"- **{c['severity']}**: {c['text']}" for c in comments)
    art = write_artifact(task_id, f"verify-{(target or {}).get('id') or 'task'}", review_md, kind="review", project_root=project_root)
    return {"ok": True, "comments": comments, "artifact": art, "task_id": task_id}
