"""A coding agent's repeat read of an unchanged file in one run answers "unchanged"."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.tools.core import read_ledger
from backend.workspace import identity


@pytest.fixture
def run():
    read_ledger._runs.clear()
    token = identity.bind(identity.RunContext(run_id="run-1", conv_id="c", coding_agent="claude_code"))
    yield
    identity.reset(token)
    read_ledger._runs.clear()


def test_second_read_is_unchanged_until_the_file_changes(run, tmp_path: Path) -> None:
    f = tmp_path / "shop.verse"
    f.write_text("a\n", encoding="utf-8")
    assert read_ledger.repeat_note("workspace_read_file", str(f), (1, 40)) == ""
    assert "Unchanged" in read_ledger.repeat_note("workspace_read_file", str(f), (1, 40))
    assert read_ledger.repeat_note("workspace_read_file", str(f), (41, 80)) == ""  # other lines
    f.write_text("a\nb\n", encoding="utf-8")  # an edit, from any tool or a shell
    assert read_ledger.repeat_note("workspace_read_file", str(f), (1, 40)) == ""


def test_force_reads_again_and_paths_are_one_key(run, tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    assert read_ledger.repeat_note("view_image", str(f)) == ""
    other_spelling = os.path.join(str(tmp_path), ".", "a.txt")
    assert "Unchanged" in read_ledger.repeat_note("view_image", other_spelling)
    assert read_ledger.repeat_note("view_image", str(f), force=True) == ""


def test_new_run_person_and_embedded_ducky_always_get_the_text(run, tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    read_ledger.repeat_note("workspace_read_file", str(f))
    token = identity.bind(identity.RunContext(run_id="run-2", conv_id="c", coding_agent="claude_code"))
    try:
        assert read_ledger.repeat_note("workspace_read_file", str(f)) == ""  # next turn
    finally:
        identity.reset(token)
    token = identity.bind(identity.RunContext(run_id="run-1", coding_agent="ducky"))
    try:
        assert read_ledger.repeat_note("workspace_read_file", str(f)) == ""
        assert read_ledger.repeat_note("workspace_read_file", str(f)) == ""
    finally:
        identity.reset(token)


@pytest.mark.parametrize("chars,added,removed,want", [
    (10_000, 2, 2, True),      # 2 of ~400 lines: should have been an edit
    (10_000, 200, 200, False),  # half the file changed: a rewrite is fine
    (10_000, 400, 0, False),   # a new file
    (2_000, 1, 1, False),      # small file: not worth a note
])
def test_a_big_rewrite_of_a_few_lines_gets_the_edit_tip(chars, added, removed, want) -> None:
    from backend.tools.core.system import rewrite_tip

    content = ("x" * 24 + "\n") * (chars // 25)
    assert bool(rewrite_tip(content, added, removed)) is want


def test_workspace_read_file_answers_unchanged_then_force(run, tmp_path: Path, monkeypatch) -> None:
    from backend.tools.core import system

    f = tmp_path / "big.py"
    f.write_text("print('hi')\n" * 50, encoding="utf-8")
    monkeypatch.setattr(system, "resolve_workspace_path", lambda rel: str(f))
    first = system.workspace_read_file(path="big.py")
    assert "print('hi')" in first
    again = system.workspace_read_file(path="big.py")
    assert again.startswith("Unchanged") and "print" not in again
    assert "print('hi')" in system.workspace_read_file(path="big.py", force=True)
