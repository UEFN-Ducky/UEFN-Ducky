"""No source file grows past MAX_LINES.

host.py (4759 lines), project_chats.py (2720), agent_modes.py (2394), panel_api_chats.py
(2130) and skills/store.py (2059) grew one fix at a time until nobody could own them. A new
file over the limit fails here; split it into modules with their own tests instead.

SPLITTING lists the files already over it, with their line count today. A listed file may
shrink but never grow past its count; lower the count after a split (or drop the entry once
the file is under MAX_LINES).
"""

from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parent  # ducky_app/
MAX_LINES = 1500
_SKIP_DIRS = {"__pycache__", "node_modules", ".venv", "venv", "build", "dist"}

# path relative to ducky_app -> its line count when it was listed
SPLITTING: dict[str, int] = {
    'backend/agent/coding_agents/plans.py': 2160,
    'backend/automations/runner.py': 2817,
    'backend/tools/panel/ducky_panel.py': 2826,
    'backend/workspace/journal.py': 2045,
    'frontend/duckyos_account.py': 2878,
    'frontend/ui_web/browser_overlay.py': 1645,
    'frontend/ui_web/group_orchestrator.py': 1895,
    'frontend/ui_web/panel_api_settings.py': 2185,
    'frontend/ui_web/panel_api_store.py': 2199,
    'frontend/ui_web/panel_httpd.py': 1623,
    'frontend/ui_web/project_files.py': 1659,
    'frontend/ui_web/win_frameless.py': 1506,
    'uefn_listener/listener/registry/worldgen.py': 1685,
    'uefn_listener/listener/verse_editable_editor.py': 1940,
}


def _is_test(path: Path) -> bool:
    return path.name.startswith("test_") or path.name == "conftest.py"


def line_counts(app: Path = APP) -> dict[str, int]:
    """{path relative to *app*: lines} for every non-test ``.py`` under it."""
    counts: dict[str, int] = {}
    for path in sorted(app.rglob("*.py")):
        rel = path.relative_to(app)
        if _is_test(path) or _SKIP_DIRS.intersection(rel.parts):
            continue
        with open(path, "rb") as fh:
            counts[rel.as_posix()] = sum(1 for _ in fh)
    return counts


def oversize(counts: dict[str, int], splitting: dict[str, int], limit: int = MAX_LINES) -> list[str]:
    """Why each file breaks the rule; [] when none does."""
    bad = []
    for rel, lines in sorted(counts.items()):
        cap = splitting.get(rel)
        if cap is None and lines > limit:
            bad.append(f"{rel}: {lines} lines (max {limit}); split it into modules with their own tests")
        elif cap is not None and lines > cap:
            bad.append(f"{rel}: {lines} lines, grew past its SPLITTING count {cap}; move code out instead")
    return bad


def test_no_file_grows_past_the_limit():
    bad = oversize(line_counts(), SPLITTING)
    assert not bad, "Files too big:\n  " + "\n  ".join(bad)


def test_splitting_lists_only_files_that_exist():
    gone = sorted(set(SPLITTING) - set(line_counts()))
    assert not gone, "Not in ducky_app any more; delete from SPLITTING: " + ", ".join(gone)


def test_a_planted_oversize_file_fails(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "small.py").write_text("x = 1\n" * 1495)
    (tmp_path / "backend" / "host.py").write_text("x = 1\n" * 4759)
    (tmp_path / "backend" / "test_huge.py").write_text("x = 1\n" * 5000)  # tests are not counted
    listed = {"backend/host.py": 4759}
    assert oversize(line_counts(tmp_path), listed) == []
    (tmp_path / "backend" / "small.py").write_text("x = 1\n" * 1505)  # +10 lines on an unlisted file
    (tmp_path / "backend" / "host.py").write_text("x = 1\n" * 4769)  # a listed file grew
    assert oversize(line_counts(tmp_path), listed) == [
        "backend/host.py: 4769 lines, grew past its SPLITTING count 4759; move code out instead",
        "backend/small.py: 1505 lines (max 1500); split it into modules with their own tests",
    ]
    (tmp_path / "backend" / "host.py").write_text("x = 1\n" * 3000)  # shrinking is fine
    assert oversize(line_counts(tmp_path), listed) == [
        "backend/small.py: 1505 lines (max 1500); split it into modules with their own tests",
    ]
