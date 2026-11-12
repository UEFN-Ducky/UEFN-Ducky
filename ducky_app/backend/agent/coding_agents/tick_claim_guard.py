"""Undo plan ticks made in a turn whose reply lists files that are not there.

Nov 11 2026: a lead on a small model ticked all 9 steps of its plan and listed five Verse files,
a HUD and meshes in its reply. It had written nothing and changed nothing. The "Files not
written" banner said so, but the plan still read 9/9 and the person was told the map was built.

A tick is never refused while the turn runs: that was tried and dropped (a refused member
resent its tick 219 times, see tick_gate), and a step can honestly be done in a turn that
changes nothing (a read-only step, work from an earlier turn, the person asking for the tick).
The ticks stand until the turn ends and are undone only on proof, all of it:

- the reply names files as written (write_claim_guard's reading of it),
- this turn wrote none of them and they are not in the project,
- the turn changed nothing in the project (no file, no editor change, no plugin's program).

Then every step this turn ticked is pending again, its notes say why, and the reply says so.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable

from backend.agent.toolsets import effective_tool_name
from backend.agent.write_claim_guard import claimed_paths, normalize_rel

log = logging.getLogger(__name__)

TICK_TOOLS = frozenset({"ducky_plan_update_node", "ducky_update_plan"})
REOPEN_NOTE = ("Reopened by Ducky: the reply that ticked this listed files that are not in the project, "
               "and that turn changed nothing.")
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "Intermediate", "Saved", "DerivedDataCache"})
MAX_WALK = 20000
MAX_SHOWN = 8


def _args(rec: Any) -> tuple[str, dict[str, Any]]:
    """(the tool really called, its arguments), through a ducky_call_tool wrapper."""
    raw = getattr(rec, "arguments", None)
    args = raw if isinstance(raw, dict) else {}
    outer = str(getattr(rec, "name", "") or "").strip()
    name = effective_tool_name(outer, args)
    if outer == "ducky_call_tool" and isinstance(args.get("arguments"), dict):
        args = args["arguments"]
    return name, args


def is_tick_tool(rec: Any) -> bool:
    return _args(rec)[0] in TICK_TOOLS


def _refused(result: Any) -> bool:
    """A plan tool answers a refusal as ``{"ok": false, "error": ...}``, not as a failed call."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return False
    return isinstance(result, dict) and result.get("ok") is False


def _completed(status: Any) -> bool:
    from backend.agent.coding_agents import plans

    return bool(str(status or "").strip()) and plans._normalize_status(status) == "completed"


def _walk_completed(nodes: Any, out: list[str]) -> None:
    if isinstance(nodes, str):
        try:
            nodes = json.loads(nodes)
        except ValueError:
            return
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id") or "").strip()
        if nid and _completed(node.get("status")):
            out.append(nid)
        _walk_completed(node.get("children"), out)


def ticked_steps(rec: Any) -> list[str]:
    """Step ids a plan tool call that worked set to completed (a whole-plan update names every
    completed step it carries, old ones too: the caller drops what was done before the turn)."""
    name, args = _args(rec)
    if name not in TICK_TOOLS or str(getattr(rec, "status", "") or "") != "success" or _refused(getattr(rec, "result", None)):
        return []
    if name == "ducky_plan_update_node":
        nid = str(args.get("node_id") or "").strip()
        return [nid] if nid and _completed(args.get("status")) else []
    out: list[str] = []
    _walk_completed(args.get("todos"), out)
    _walk_completed(args.get("nodes"), out)
    return list(dict.fromkeys(out))


def _plan_of(chat_id: str, project_root: str | None) -> dict[str, Any] | None:
    """The plan this chat ticks in: its own, else its team's."""
    from backend.agent.coding_agents import plans

    try:
        return plans.load_plan(chat_id, project_root) or plans.team_plan_for(chat_id, project_root)
    except Exception:
        return None


def completed_steps(chat_id: str, project_root: str | None = None) -> set[str]:
    """Ids of the steps already completed in the plan this chat ticks in."""
    from backend.agent.coding_agents import plans

    plan = _plan_of(chat_id, project_root)
    return {str(n.get("id")) for n in plans._flatten_nodes((plan or {}).get("nodes")) if str(n.get("status")) == "completed"}


def _verse_names(root: Path) -> set[str]:
    names: set[str] = set()
    seen = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            seen += 1
            if name.lower().endswith(".verse"):
                names.add(name.lower())
        if seen > MAX_WALK:
            break
    return names


def invented_files(text: str, written: Iterable[str], project_root: str | None) -> list[str]:
    """Files the reply says were written that this turn did not write and the project does not
    have, by path or by name anywhere in it. [] when that can't be told (no project folder)."""
    root = Path(project_root) if project_root else None
    if root is None or not root.is_dir():
        return []
    done = {normalize_rel(p) for p in written if p}
    missing = [p for p in claimed_paths(text) if normalize_rel(p) not in done]
    if not missing:
        return []
    names: set[str] | None = None
    out: list[str] = []
    for claimed in missing:
        rel = claimed.replace("\\", "/").lstrip("/")
        if (root / rel).is_file() or (root / "Content" / rel).is_file():
            continue
        if names is None:
            names = _verse_names(root)
        if Path(rel).name.lower() not in names:
            out.append(claimed)
    return out


def run_changed_project(run_id: str, project_root: str | None) -> bool:
    """True when the change journal holds a change this run made to the project: a file, the
    editor, a plugin's program. Ducky's own rows (a seated chat, a sent message) are not one.
    True when it can't be told, so an unknown never undoes a tick."""
    if not run_id:
        return True
    try:
        from backend.workspace.journal import OUTCOME_OK
        from backend.workspace.runtime import get_writer

        run = get_writer().journal.get_run(run_id, project_root=str(project_root or ""))
    except ValueError:
        return False  # no row for this run: it journaled nothing
    except Exception:
        return True
    for entry in run.get("entries") or []:
        if entry.get("outcome", OUTCOME_OK) != OUTCOME_OK:
            continue
        if str((entry.get("editor") or {}).get("program") or "") == "ducky":
            continue
        return True
    return False


def reopen_steps(chat_id: str, step_ids: Iterable[str], project_root: str | None = None) -> list[str]:
    """Put these completed steps back to pending with REOPEN_NOTE in their notes. Returns the
    outline numbers of the ones reopened; a step that can't be (gone, not this chat's) is skipped."""
    from backend.agent.coding_agents import plans

    plan = _plan_of(chat_id, project_root)
    if not plan:
        return []
    labels = {str(n.get("id")): lab for lab, n in plans.outline_numbers(plan.get("nodes"))}
    nodes = {str(n.get("id")): n for n in plans._flatten_nodes(plan.get("nodes"))}
    reopened: list[str] = []
    for nid in dict.fromkeys(str(s) for s in step_ids):
        node = nodes.get(nid)
        if node is None or node.get("children") or str(node.get("status")) != "completed":
            continue
        body = str(node.get("body_markdown") or "").rstrip()
        note = body if REOPEN_NOTE in body else f"{body}\n\n{REOPEN_NOTE}".strip()
        try:
            plans.update_node(chat_id, nid, status="pending", body_markdown=note, project_root=project_root)
        except Exception:  # a refused edit leaves that step as it is
            log.info("could not reopen step %s", nid, exc_info=True)
            continue
        reopened.append(labels.get(nid, nid))
    if reopened:
        fresh = _plan_of(chat_id, project_root)
        if fresh:
            plans.push_plan_updated(fresh)
    return reopened


def undo_unearned_ticks(chat_id: str, run_id: str, ticked: Iterable[str], text: str,
                        written: Iterable[str], project_root: str | None = None) -> str | None:
    """Reopen the steps this turn ticked when its reply lists files that are not there and the
    turn changed nothing. Returns the line for the reply, or None when the ticks stand."""
    steps = [s for s in dict.fromkeys(ticked) if s]
    if not steps:
        return None
    from backend.agent.coding_agents import plans

    root = project_root or plans._resolve_project_root(None)
    missing = invented_files(text, written, root)
    if not missing or run_changed_project(run_id, root):
        return None
    reopened = reopen_steps(chat_id, steps, project_root)
    if not reopened:
        return None
    shown = ", ".join(reopened[:MAX_SHOWN]) + (f" and {len(reopened) - MAX_SHOWN} more" if len(reopened) > MAX_SHOWN else "")
    files = ", ".join(missing[:MAX_SHOWN]) + (f" and {len(missing) - MAX_SHOWN} more" if len(missing) > MAX_SHOWN else "")
    plural = "s" if len(reopened) != 1 else ""
    return (f"{len(reopened)} plan step{plural} ticked this turn {'were' if plural else 'was'} reopened ({shown}): "
            f"the reply lists {files}, which {'are' if len(missing) != 1 else 'is'} not in the project, and this turn "
            "changed nothing. Do the work, then tick.")
