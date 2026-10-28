"""Tick gate: a group member can't complete a plan step while its lane's changed plugin files fail lint.

``check_tick(chat_id, node)`` is called by the plan store when a member sets a step to
completed; it returns None (tick) or the refusal text. Only laned chats (group members) are
gated: the lead and solo chats resolve no lane and always pass. Uncommitted files never refuse
(the lead stages and commits at the end). When the lane's changed files sit in a plugin (a
``plugin.json`` above them), the static ``ducky_plugin_validate`` rules run on just those files.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.workspace import lanes as lane_rules

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_SHOWN = 20


def _git(cwd: Path, *args: str) -> bytes | None:
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.quotepath=off", "-C", str(cwd), *args],
            capture_output=True, timeout=15, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _toplevel(path: Path) -> Path | None:
    while not path.is_dir():
        if path.parent == path:
            return None
        path = path.parent
    out = _git(path, "rev-parse", "--show-toplevel")
    return Path(out.decode("utf-8", "replace").strip()) if out else None


def _bases(lane: Sequence[str]) -> list[Path]:
    """Folders a lane covers: the open project for relative globs, the literal prefix of full ones."""
    roots = lane_rules.project_roots()
    out: list[Path] = []
    for glob in lane:
        if lane_rules._is_abs(glob):
            try:
                out.append(Path(lane_rules.literal_prefix(glob)))
            except lane_rules.LaneGlobError:  # its project left the list: covers nothing now
                continue
        elif roots:
            out.append(Path(roots[0]))
    return list(dict.fromkeys(out))


def _shown(full: str) -> str:
    """Project-relative inside the open project (what the lane globs say), full path elsewhere."""
    roots = lane_rules.project_roots()
    root = roots[0] if roots else ""
    if root and lane_rules._inside(full, root) and len(full) > len(root):
        return full[len(root) + 1:]
    return full


def _in_lane(lane: Sequence[str], full: str) -> bool:
    return lane_rules.in_lane(lane, _shown(full))


def _repo_files(lane: Sequence[str], *commands: tuple[str, ...], parse_status: bool = False) -> list[str]:
    """Full paths in the lane that the first git command that works lists, per repo."""
    seen: list[str] = []
    for top in dict.fromkeys(t for t in map(_toplevel, _bases(lane)) if t is not None):
        out = next((o for o in (_git(top, *c) for c in commands) if o is not None), None)
        if out is None:
            continue
        parts = iter(out.split(b"\0"))
        for entry in parts:
            if not entry:
                continue
            rel = entry.decode("utf-8", "replace")
            if parse_status:
                code, rel = rel[:2], rel[3:]
                if "R" in code or "C" in code:
                    next(parts, None)  # -z: the rename's old path follows as its own entry
            full = f"{top.as_posix().rstrip('/')}/{rel.rstrip('/')}"
            if _in_lane(lane, full):
                seen.append(full)
    return list(dict.fromkeys(seen))


def lane_dirty_files(lanes: Sequence[str]) -> list[str]:
    """Uncommitted files (modified, staged, untracked, deleted) inside these lane globs,
    across every git repo the lane covers. Lanes outside git have none."""
    status = ("status", "--porcelain=v1", "-z", "--untracked-files=all")
    return [_shown(f) for f in _repo_files(lanes, status, parse_status=True)]


# ------------------------------------------------------------ plugin checks

_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "vendor"})
MAX_WALK = 5000
# Zero-width, bidi-control and other invisible characters that change how code reads or runs.
_HIDDEN = {chr(c) for c in (*range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x00AD, 0x2060, 0xFEFF)}
_TEXT_SUFFIXES = frozenset({".py", ".js", ".mjs", ".ts", ".tsx", ".html", ".htm", ".css", ".json", ".md", ".toml", ".yml", ".yaml"})
_REF_SUFFIXES = frozenset({".html", ".htm", ".js", ".mjs", ".css", ".md", ".json", ".py", ".png", ".svg", ".jpg", ".jpeg"})


def _changed_files(lane: Sequence[str]) -> list[str]:
    """Full paths of the lane's work: unpushed commits (or the last commit without an upstream)
    plus uncommitted files in git; every file under a lane that isn't in git (a plugin draft)."""
    committed = _repo_files(
        lane,
        ("diff", "--name-only", "-z", "@{u}...HEAD"),
        ("show", "--name-only", "-z", "--format=", "HEAD"),
    )
    dirty = _repo_files(lane, ("status", "--porcelain=v1", "-z", "--untracked-files=all"), parse_status=True)
    loose: list[str] = []
    for base in _bases(lane):
        if not base.is_dir() or _toplevel(base) is not None:
            continue
        stack = [base]
        while stack and len(loose) < MAX_WALK:
            for entry in stack.pop().iterdir():
                if entry.is_dir():
                    if entry.name not in _SKIP_DIRS:
                        stack.append(entry)
                elif _in_lane(lane, entry.as_posix()):
                    loose.append(entry.as_posix())
    return list(dict.fromkeys(committed + dirty + loose))


def _plugin_root(path: Path) -> Path | None:
    for parent in path.parents:
        if (parent / "plugin.json").is_file():
            return parent
    return None


def _manifest_issues(root: Path) -> list[str]:
    from backend.uefn_plugins.plugin_lint import check_manifest

    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"plugin.json: {exc}"]
    if not isinstance(manifest, dict):
        return ["plugin.json must be an object"]
    issues = [f"plugin.json: {m}" for m in check_manifest(manifest)]
    refs: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif (isinstance(node, str) and "://" not in node and not node.startswith(("{", "/"))
              and Path(node).suffix.lower() in _REF_SUFFIXES):
            refs.append(node)

    walk(manifest.get("contributes"))
    for ref in dict.fromkeys(refs):
        if not (root / ref.lstrip("./")).is_file():
            issues.append(f"plugin.json names {ref}, but that file isn't in the plugin.")
    return issues


def _file_issues(root: Path, path: Path) -> list[str]:
    from backend.uefn_plugins.plugin_lint import _Backend

    rel = path.relative_to(root).as_posix()
    if path.suffix.lower() not in _TEXT_SUFFIXES or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    issues: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        found = sorted({f"U+{ord(ch):04X}" for i, ch in enumerate(line) if ch in _HIDDEN and not (n == 1 and i == 0)})
        if found:
            issues.append(f"{rel}:{n}: hidden character {', '.join(found)}; delete it (it changes how the code reads).")
    if path.suffix.lower() == ".py" and rel.startswith("backend/"):
        try:
            tree = ast.parse(text, filename=rel)
        except (SyntaxError, ValueError) as exc:
            return issues + [f"{rel}: {exc}"]
        scan = _Backend()
        scan.scan(rel, tree)
        issues.extend(scan.source_reads)
    return issues


def lane_plugin_issues(lane: Sequence[str]) -> list[str]:
    """The static ``ducky_plugin_validate`` rules (import rules, manifest refs, hidden
    characters) on the lane's changed files that sit in a plugin. No tests run."""
    issues: list[str] = []
    manifests: set[Path] = set()
    for full in _changed_files(lane):
        path = Path(full)
        root = _plugin_root(path)
        if root is None:
            continue
        if path.name == "plugin.json" and path.parent == root:
            manifests.add(root)
            continue
        issues.extend(_file_issues(root, path))
    for root in sorted(manifests):
        issues.extend(_manifest_issues(root))
    return list(dict.fromkeys(issues))


def check_tick(chat_id: str, node: Mapping[str, Any] | None = None) -> str | None:
    """None when the step may be completed, else why not. ``node`` is the step being ticked."""
    del node
    lane = lane_rules.resolve_lane(chat_id)
    if not lane:  # lead, solo chat, or read-only member: nothing of theirs to commit
        return None
    # Uncommitted lane files don't refuse: swarm leads stage and commit once at the end, and members
    # told "nobody commits" looped resending a refused tick (219 refusals on Oct 20 2026).
    issues = lane_plugin_issues(lane)
    if issues:
        more = f" …and {len(issues) - MAX_SHOWN} more." if len(issues) > MAX_SHOWN else ""
        return "Can't tick: plugin checks failed on your files:\n- " + "\n- ".join(issues[:MAX_SHOWN]) + more
    return None
