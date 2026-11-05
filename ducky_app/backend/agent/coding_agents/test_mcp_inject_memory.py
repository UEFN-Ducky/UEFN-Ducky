"""Claude Code / Codex get the project's memory index in their bootstrap."""

from __future__ import annotations

import pytest

_HEAD = "### Index (all of this project's lessons; pull the ones that match your task before you start)"


@pytest.fixture
def mem_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("UEFN_DUCKY_PROJECT_ROOT", raising=False)
    monkeypatch.setattr("backend.agent.prompt._chat_plan", lambda conv_id, root: None)
    monkeypatch.setattr("frontend.project_kind.is_folder_project", lambda root: True)
    root = tmp_path / "Proj"
    root.mkdir()
    return str(root)


def _bootstrap(root: str, task: str = "") -> str:
    from backend.agent.coding_agents.mcp_inject import bootstrap_system_prompt

    return bootstrap_system_prompt(project_root=root, conv_id="chat-a", task=task)


def _index(text: str) -> list[str]:
    assert _HEAD in text
    body = text.split(_HEAD, 1)[1].strip("\n")
    return [ln for ln in body.splitlines() if ln.startswith("- ")]


def test_three_entries_listed_core_first(mem_root):
    from backend.memory.project import save_entry

    save_entry("zz-notes", "a", description="Misc notes", project_root=mem_root)
    save_entry("aa-ideas", "b", description="Ideas", project_root=mem_root)
    save_entry("team-workflow/git-and-worktrees", "c", description="Pull before ANY git", project_root=mem_root)
    lines = _index(_bootstrap(mem_root))
    names = [ln[2:].split(" — ", 1)[0] for ln in lines if not ln.startswith("- …")]
    assert set(names) >= {"zz-notes", "aa-ideas", "team-workflow"} or set(names) >= {
        "zz-notes", "aa-ideas", "team-workflow/git-and-worktrees"}
    assert names[0].startswith("team-workflow")


def test_empty_memory_adds_no_index(mem_root):
    assert _HEAD not in _bootstrap(mem_root)


def test_matching_task_pulls_entry_up(mem_root):
    from backend.memory.project import save_entry

    for i in range(3):
        save_entry(f"aa-note-{i}", "x", description=f"General note {i}", project_root=mem_root)
    save_entry("zz-render-trim", "y", description="Render trim crashes when UEFN is in the background",
               project_root=mem_root)
    plain = [ln for ln in _index(_bootstrap(mem_root)) if "render-trim" in ln or "aa-note" in ln]
    ranked = [ln for ln in _index(_bootstrap(mem_root, task="fix the render trim crash")) if
              "render-trim" in ln or "aa-note" in ln]
    assert "render-trim" in ranked[0]
    assert "render-trim" not in plain[0]
