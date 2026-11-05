"""The embedded prompt carries every author's memory, from the repo the work is in."""

from __future__ import annotations

import pytest


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("UEFN_DUCKY_PROJECT_ROOT", raising=False)
    monkeypatch.setattr("backend.agent.prompt._chat_plan", lambda conv_id, root: None)
    return tmp_path


def _memory_text(**kw) -> str:
    from backend.agent.prompt import get_system_prompt_parts

    parts = get_system_prompt_parts(listener_port=4200, **kw)
    return "\n".join(str(v) for v in parts.values())


def test_another_authors_entry_is_in_the_prompt(isolated_appdata, tmp_path):
    from backend.memory.project import save_entry

    root = tmp_path / "Proj"
    root.mkdir()
    save_entry("bob-note", "Bob's lesson.", description="Bob fact", author="Bob", project_root=str(root))
    text = _memory_text(project_root=str(root), ducky_name="Alice")
    assert "bob-note" in text
    assert "All of this project's lessons, core rules first" in text


def test_plan_repo_line_picks_that_projects_memory(isolated_appdata, tmp_path, monkeypatch):
    from backend.memory.project import save_entry

    open_proj, repo = tmp_path / "Limo", tmp_path / "Release"
    open_proj.mkdir()
    repo.mkdir()
    save_entry("limo-note", "x", description="Limo fact", project_root=str(open_proj))
    save_entry("team-workflow/testing-and-release", "y", description="Release rules", project_root=str(repo))
    plan = {"title": "Memory fix", "body_markdown": f"**Repo:** `{repo}`", "nodes": []}
    monkeypatch.setattr("backend.agent.prompt._chat_plan", lambda conv_id, root: plan)
    monkeypatch.setattr("backend.workspace.lanes.project_roots", lambda: [str(open_proj), str(repo)])
    text = _memory_text(project_root=str(open_proj), conv_id="chat-1", ducky_name="Seat")
    assert "testing-and-release" in text
    assert "limo-note" not in text
    assert "(the repo this work is in)" in text


def test_the_8000_budget_holds(isolated_appdata, tmp_path, monkeypatch):
    from backend.agent import prompt

    seen: dict = {}

    def fake_index(root, **kw):
        seen.update(kw)
        return "\n".join(f"- e{i} — " + "d" * 90 for i in range(200))[: kw["max_chars"]]

    monkeypatch.setattr("backend.memory.project.index_markdown", fake_index)
    assert prompt.memory_index_budget() == 8_000
    text = _memory_text(project_root=str(tmp_path), ducky_name="Seat")
    assert seen["max_chars"] == 8_000
    assert "author_filter" not in seen
    block = text.split("### Index (names + descriptions only)\n", 1)[1]
    assert len(block.split("\n\n")[0]) <= 8_000
