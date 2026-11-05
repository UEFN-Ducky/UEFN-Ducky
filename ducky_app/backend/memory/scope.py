"""Which project's memory an agent reads: the repo the work is in, not just the open project.

Nov 5 2026: a team working on UEFN-Ducky-Release while LimoRide was open read LimoRide's
memory and repeated mistakes the release repo's memory already named.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _chat_plan(conv_id: str, project_root: str) -> dict[str, Any] | None:
    from backend.agent.coding_agents import plans

    root = project_root or None
    plan = plans.load_plan(conv_id, root)
    if plan:
        return plan
    return plans.team_plan_for(conv_id, root)


def _inside(child: str, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _common_repo(cwd: str) -> str:
    out = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW,
                         stdin=subprocess.DEVNULL)
    if out.returncode != 0 or not out.stdout.strip():
        return ""
    common = Path(out.stdout.strip())
    return str(common.parent if common.name == ".git" else common)


def _worktree_repo(conv_id: str, cwd: str) -> str:
    from backend.sandbox import paths

    box = paths.sandbox_dir(conv_id)
    if not box.is_dir():
        return ""
    if cwd and _inside(cwd, box):
        repo = _common_repo(cwd)
        if repo:
            return repo
    try:
        rows = json.loads((box / "worktrees.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    for row in reversed(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            continue
        repo, path = str(row.get("repo") or ""), str(row.get("path") or "")
        if repo and path and Path(path).is_dir() and Path(repo).is_dir():
            return repo
    return ""


def memory_root(conv_id: str = "", project_root: str = "", plan: dict[str, Any] | None = None,
                cwd: str = "") -> str:
    """The project whose memory this chat should read.

    The plan's ``**Repo:**`` line first (the chat's own plan, else its team plan), then the repo
    of the chat's sandbox worktree, then ``project_root``. Never raises.
    """
    try:
        from backend.tools.panel.swarm_preview import plan_repo

        if plan is None and conv_id:
            plan = _chat_plan(conv_id, project_root)
        repo = plan_repo(plan) if plan else ""
        if repo:
            return repo
        if conv_id:
            repo = _worktree_repo(conv_id, cwd or os.getcwd())
            if repo:
                return repo
    except Exception:  # noqa: BLE001 - memory scope must never break a prompt
        return project_root
    return project_root
