"""Tick gate: uncommitted lane files never refuse a member's tick; plugin lint failures do."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.agent.coding_agents import tick_gate
from backend.workspace import lanes

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

MEMBER = "member-chat"


class _Lanes:
    def __init__(self, lane: list[str]) -> None:
        self.lane = lane

    def lane_for(self, conv_id: str):
        return tuple(self.lane) if conv_id == MEMBER else None


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                   capture_output=True)


@pytest.fixture
def project(tmp_path: Path):
    root = tmp_path.resolve() / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "other").mkdir()
    lanes.reset_for_tests()
    lanes.set_project_roots_source(lambda: [str(root)])
    lanes.register_lane_provider(_Lanes(["pkg/**"]))
    yield root
    lanes.reset_for_tests()


def _commit_all(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c")


def test_dirty_lane_file_still_ticks(project: Path) -> None:
    _git(project, "init", "-q")
    (project / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _commit_all(project)
    (project / "pkg" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (project / "pkg" / "new.py").write_text("y = 1\n", encoding="utf-8")
    (project / "other" / "not_mine.py").write_text("z = 1\n", encoding="utf-8")
    assert lanes.resolve_lane(MEMBER) == ("pkg/**",)
    assert sorted(tick_gate.lane_dirty_files(["pkg/**"])) == ["pkg/a.py", "pkg/new.py"]
    # The lead stages and commits at the end: uncommitted files never refuse a member's tick.
    assert tick_gate.check_tick(MEMBER, {"id": "s1"}) is None


def test_clean_committed_lane_ticks(project: Path) -> None:
    _git(project, "init", "-q")
    (project / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _commit_all(project)
    (project / "other" / "dirty_but_not_mine.py").write_text("z = 1\n", encoding="utf-8")
    assert tick_gate.check_tick(MEMBER, {"id": "s1"}) is None


def test_non_git_lane_ticks(project: Path) -> None:
    (project / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    if tick_gate._toplevel(project) is not None:
        pytest.skip("temp folder sits inside a git repo")
    assert tick_gate.check_tick(MEMBER, {"id": "s1"}) is None


def _plugin(root: Path, backend_src: str) -> None:
    plugin = root / "pkg" / "myplugin"
    (plugin / "backend").mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"id": "myplugin", "data_sharing": false, "contributes": {}}', encoding="utf-8")
    (plugin / "backend" / "gmail_send.py").write_text(backend_src, encoding="utf-8")


def test_plugin_file_loading_by_path_refuses(project: Path) -> None:
    _git(project, "init", "-q")
    _plugin(project, "import importlib.util\nspec = importlib.util.spec_from_file_location('x', 'x.py')\n")
    _commit_all(project)
    refusal = tick_gate.check_tick(MEMBER, {"id": "s1"})
    assert refusal and refusal.startswith("Can't tick: plugin checks failed")
    assert "backend/gmail_send.py:2" in refusal and "spec_from_file_location" in refusal
    assert "from . import" in refusal


def test_clean_plugin_file_ticks(project: Path) -> None:
    _git(project, "init", "-q")
    _plugin(project, "from . import helpers\n")
    _commit_all(project)
    assert tick_gate.check_tick(MEMBER, {"id": "s1"}) is None


def test_plugin_draft_outside_git_checks_hidden_characters_and_manifest_refs(project: Path) -> None:
    if tick_gate._toplevel(project) is not None:
        pytest.skip("temp folder sits inside a git repo")
    _plugin(project, "x = 1​\n")
    (project / "pkg" / "myplugin" / "plugin.json").write_text(
        '{"id": "myplugin", "data_sharing": false, "contributes": {"ui.panels": [{"entry": "ui/panel.html"}]}}',
        encoding="utf-8",
    )
    refusal = tick_gate.check_tick(MEMBER, {"id": "s1"}) or ""
    assert "backend/gmail_send.py:1: hidden character U+200B" in refusal
    assert "ui/panel.html" in refusal


def test_lead_and_solo_chats_are_never_gated(project: Path) -> None:
    _git(project, "init", "-q")
    (project / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert tick_gate.check_tick("lead-chat", {"id": "s1"}) is None
