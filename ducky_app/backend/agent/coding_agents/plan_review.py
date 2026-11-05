"""Plan review: the written protocol for plans and the check for steps a person can't follow."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

# Single copy of the Diagnose→Fix→Verify protocol (prompt / MCP / BYOA / modes).
PLAN_PROTOCOL = (
    "Followable `nodes`: Diagnose → Fix → Verify. Size each leaf as ONE outcome one agent "
    "finishes in about 15–30 minutes; a step covering several features or checks (all, every, "
    "end to end) becomes one step per feature or check, so progress shows and a blocker names "
    "itself. Each leaf: `content` = a short title in plain words (under 80 characters); "
    "`body_markdown` = a description someone can build from without asking anyone: **Why** (the "
    "problem), **Files** (exact paths, the functions to change, line numbers when known), **Do** "
    "(numbered), **Watch out** (only for a real trap), **Tests**, **Done when** (name the check "
    "tool) and **Example** (one concrete result). Read the code first and name real functions. "
    "Give each section a one-line description too. "
    "`ducky_plan_update_node` `in_progress` BEFORE the work, in the same turn ahead of the "
    "work's calls (a turn's calls run in order), never a turn of its own; "
    "`completed` when Done-when is met. Mutators are blocked until a leaf is "
    "in_progress. If the approach changes, update the tree before more mutators — "
    "never thrash off-plan. Team plans: ONE main plan, the lead's, with one sub plan per agent "
    "(a section whose `assignee` is that agent) holding that agent's points; members never make "
    "plans of their own. The agent ticks its points; ticking the last one checks its sub plan off "
    "in the main plan and tells the lead, so nobody sends reports. A sub plan's description says "
    "**Files** (what it owns), **Done when** (the change and its tests, never evidence files, "
    "read-backs or live checks only the person can do), **Not yours** (what to leave to others) "
    "and **Hands over:** when its files pass to another section; `after` = ids "
    "of steps or sub plans that must finish first. Team rules: every file has exactly one owner "
    "section and a step's Files stay inside its section's Files; a change needed in another lane "
    "is a separate \"Hand-off:\" step in the owner's section with `after`. Balance the seats: one "
    "seat per group of files that can be worked at the same time; split a seat with more than "
    "about 2× the median work, merge a seat that would only wait; seats whose work can't start in "
    "the first wave start later, never seated idle. A hot shared file (many steps need it) gets a "
    "first \"Split:\" step that moves parts into new modules, each handed to its own seat. "
    "Templates: `ducky_create_plan_template` / "
    "`ducky_instantiate_plan_template`. Plan mode: create/update then stop."
)

_TITLE_MAX = 100
_BIG_STEP_RE = re.compile(r"\b(all|every|everything|end[ -]to[ -]end|whole)\b", re.IGNORECASE)
_REVIEW_MAX = 8
_THIN_MIN = 200
_FILES_RE = re.compile(r"\bFiles:\**([^\n]*)")
_DONE_WHEN_RE = re.compile(r"\bdone[ -]when\b", re.IGNORECASE)
_HANDS_OVER_RE = re.compile(r"\bhands over:", re.IGNORECASE)
_TICKED_RE = re.compile(r"`([^`\s(]+)`")
_FILE_EXT_RE = re.compile(r"\.[A-Za-z]\w{0,5}$")
_READ_FIRST_RE = re.compile(r"\bread first:", re.IGNORECASE)
_APP = Path(__file__).resolve().parents[3]  # ducky_app/
_NEAR_CAP = 100
_SPLIT_RE = re.compile(r"^\s*split\b", re.IGNORECASE)


def _size_caps(app: Path) -> tuple[int, dict[str, int]]:
    """(MAX_LINES, SPLITTING) read from ``test_file_sizes.py`` as text; (0, {}) without it."""
    try:
        text = (app / "test_file_sizes.py").read_text(encoding="utf-8")
    except OSError:
        return 0, {}
    m = re.search(r"^MAX_LINES\s*=\s*(\d+)", text, re.MULTILINE)
    caps = {k: int(v) for k, v in re.findall(r"^\s*'([^']+\.py)':\s*(\d+),", text, re.MULTILINE)}
    return (int(m.group(1)) if m else 0), caps


def near_size_cap(paths: list[str], app: Path | None = None) -> list[tuple[str, int, int]]:
    """(path, lines, cap) for each named source file within 100 lines of its test_file_sizes.py cap.

    Nov 5 2026: plans.py and worldgen.py went over their caps mid-run and main was red for 17 min."""
    root = app or _APP
    limit, caps = _size_caps(root)
    out = []
    for raw in paths:
        rel = raw.replace("\\", "/").removeprefix("./").removeprefix("ducky_app/")
        name = rel.rsplit("/", 1)[-1]
        if not rel.endswith(".py") or name.startswith("test_") or name == "conftest.py":
            continue
        cap = caps.get(rel, limit)
        try:
            with open(root / rel, "rb") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            continue
        if cap and lines >= cap - _NEAR_CAP:
            out.append((raw, lines, cap))
    return out


def suggest_read_first(text: str, plan: dict[str, Any] | None) -> list[str]:
    """Project memory entries that fit *text*, read from the plan's repo ([] until memory can say)."""
    try:
        from backend.memory.project import suggest_entries
    except ImportError:
        return []
    root = ""
    try:
        from backend.memory.scope import memory_root

        root = memory_root(plan=plan)
    except (ImportError, TypeError):
        root = ""
    try:
        return [str(n) for n in suggest_entries(text, root) or [] if str(n).strip()]
    except (TypeError, ValueError, OSError):
        return []


def _files(body: str) -> list[str] | None:
    """Paths named on the first ``Files:`` line (backticked, with a slash or an extension); None without one."""
    m = _FILES_RE.search(body)
    if not m:
        return None
    return [p.replace("\\", "/").removeprefix("./") for p in _TICKED_RE.findall(m.group(1))
            if "/" in p or _FILE_EXT_RE.search(p)]


def _same(a: str, b: str) -> bool:
    # Paths are written relative to different roots (`ducky_app/backend/x.py` vs `backend/x.py`).
    a, b = a.rstrip("/"), b.rstrip("/")
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _is_abs(path: str) -> bool:
    return path.startswith("/") or (len(path) > 1 and path[1] == ":")


def in_lane_root(path: str, own: str) -> str:
    """*path* read against the folder of a full-path lane in another repo (a step names
    ``src/hooks.rs`` under ``C:/repo/plugin-meshy/**``); '' when that doesn't apply. Nov 3 2026:
    such steps were flagged "Outside its lane". A relative lane shares the step's root already."""
    if _is_abs(path) or not _is_abs(own):
        return ""
    root = re.split(r"[*?\[]", own, maxsplit=1)[0].rstrip("/")
    if root == own.rstrip("/") and _FILE_EXT_RE.search(root):
        return ""  # a single-file lane has no folder to read against
    return f"{root}/{path.removeprefix('./')}"


def _inside(path: str, lane: list[str]) -> bool:
    for own in lane:
        for p in filter(None, (path, in_lane_root(path, own))):
            if fnmatch.fnmatch(p, own) or _same(p, own):
                return True
            folder = own.rstrip("/") + "/"
            if (own.endswith("/") or not _FILE_EXT_RE.search(own)) and (p.startswith(folder) or "/" + folder in p):
                return True
    return False


def plan_review(plan: dict[str, Any] | None) -> list[dict[str, str]]:
    """Open steps a person can't follow: too big to show progress, too thin to build from, or
    files with two owners.

    A team kept one step ("check all original feature paths end to end") open for two hours
    while fifteen checks inside it passed one by one; the plan read 15/19 the whole time.
    """
    from backend.agent.coding_agents.plans import _DONE, _flatten_nodes, outline_numbers

    issues: list[dict[str, str]] = []
    flat = _flatten_nodes((plan or {}).get("nodes"))
    known = {str(n.get("id")) for n in flat}
    parent = {str(c.get("id")): n for n in flat for c in n.get("children") or []}
    # A team plan (any step has an owner): an open step nobody owns is work nobody does.
    team = any(n.get("assignee") or n.get("planned_assignee") for n in flat)
    owned_sections = {str(c.get("id")) for n in flat if n.get("assignee") or n.get("planned_assignee")
                      for c in _flatten_nodes(n.get("children") or [])}
    # An agent with several loose steps at the top: they belong in one sub plan assigned to it,
    # so the agent works through its points and the lead hears once, when the sub plan is done.
    loose: dict[str, int] = {}
    for node in (plan or {}).get("nodes") or []:
        if node.get("assignee") and not node.get("children") and node.get("status") not in _DONE:
            loose[str(node["assignee"])] = loose.get(str(node["assignee"]), 0) + 1
    for owner, count in loose.items():
        if count > 1:
            issues.append({"n": "-", "issue": f"{count} top-level steps are assigned to {owner}: put them in one "
                                              "sub plan assigned to that agent (its points inside)."})
    lanes: list[tuple[str, dict[str, Any], list[str]]] = []  # open sub plans: (label, node, Files)
    for label, node in outline_numbers((plan or {}).get("nodes")):
        if node.get("status") in _DONE:
            continue
        unknown = [sid for sid in node.get("after") or [] if sid not in known]
        if unknown:
            issues.append({"n": label, "issue": f"Waits for steps that don't exist ({', '.join(unknown)}): use ids from the outline."})
        if (team and not node.get("children") and not (node.get("assignee") or node.get("planned_assignee"))
                and str(node.get("id")) not in owned_sections):
            issues.append({"n": label, "issue": "No owner: set its assignee to the member who does it."})
        content = str(node.get("content") or "").strip()
        body = str(node.get("body_markdown") or "").strip()
        if node.get("children"):
            if not body:
                issues.append({"n": label, "issue": "Section has no description: add one line saying what it delivers."})
            # A sub plan holds steps directly; in a team plan it says which files it owns.
            if team and any(not c.get("children") for c in node["children"]):
                lane = _files(body)
                if lane is None:
                    issues.append({"n": label, "issue": "Sub plan has no Files: name the files it owns, so no file has two owners."})
                else:
                    lanes.append((label, node, lane))
            continue
        if _BIG_STEP_RE.search(content):
            issues.append({"n": label, "issue": "Too big for one step: split it into one step per feature or check."})
        elif len(content) > _TITLE_MAX:
            issues.append({"n": label, "issue": "Title too long: keep it short and move the details into its description."})
        if not body:
            issues.append({"n": label, "issue": "No description: write Why, Files, Do, Tests, Done when and Example."})
        else:
            step_files = _files(body)
            missing = [name for name, ok in (("Files:", step_files is not None),
                                             ("Done when", _DONE_WHEN_RE.search(body))) if not ok]
            if missing or len(body) < _THIN_MIN:
                need = f"needs {' and '.join(missing)}" if missing else "too short"
                issues.append({"n": label, "issue": f"Too thin to build from ({need}): write Why, Files (exact paths and "
                                                    "functions), Do, Tests, Done when and Example."})
            section = parent.get(str(node.get("id")))
            lane = _files(str((section or {}).get("body_markdown") or "")) if section and team else None
            for path in (step_files or []) if lane else []:
                if not _inside(path, lane):
                    issues.append({"n": label, "issue": f"Outside its lane: {path} is not in its sub plan's Files; "
                                                        "make it a Hand-off: step in the owner's section."})
        if not _SPLIT_RE.match(content):
            for path, lines, cap in near_size_cap(_files(body) or []):
                issues.append({"n": label, "issue": f"Add a `Split:` step first: {path} is {lines}/{cap} lines"})
        # Advice only: the lessons a team step should open with, so nobody repeats one.
        if team and not _READ_FIRST_RE.search(body):
            names = suggest_read_first(f"{content}\n{body}", plan)
            if names:
                issues.append({"n": label, "issue": f"Add `Read first: {', '.join(names)}`"})
        if len(issues) >= _REVIEW_MAX:
            break
    for i, (_, a, a_files) in enumerate(lanes):
        for b_label, b, b_files in lanes[i + 1:]:
            if _HANDS_OVER_RE.search(str(a.get("body_markdown") or "")) or _HANDS_OVER_RE.search(str(b.get("body_markdown") or "")):
                continue
            for path in a_files:
                if any(_same(path, other) for other in b_files):
                    issues.append({"n": b_label, "issue": f"Two owners: {path} in {a.get('content')} and {b.get('content')}"})
                    break
    return issues[:_REVIEW_MAX]


def with_plan_review(payload: dict[str, Any], plan: dict[str, Any] | None) -> dict[str, Any]:
    """Add ``plan_review`` to a tool result that wrote steps, when any step needs fixing."""
    issues = plan_review(plan)
    if not issues:
        return payload
    return {**payload, "plan_review": issues,
            "plan_review_hint": "Fix these with ducky_update_plan or ducky_plan_update_node before starting the work."}
