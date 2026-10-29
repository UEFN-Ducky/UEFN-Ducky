"""Ducky's edit tools write in the person's other Ducky projects too, journaled in that
project; a team lane is a part of the open project, so a laned agent stays there."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from backend.workspace import events, identity
from backend.workspace.identity import RunContext
from backend.workspace.lanes import MODE_ENFORCE, LanePolicy
from backend.workspace.writer import ProjectWriter, WriteDenied, WriteRecord


class Journal:
    def __init__(self) -> None:
        self.records: list[WriteRecord] = []

    def record(self, record: WriteRecord) -> dict[str, Any]:
        self.records.append(record)
        return {}

    def note_read(self, path: str, content_hash: str, project_root: str = "") -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_sinks():
    events.reset_for_tests()
    yield
    events.reset_for_tests()


@pytest.fixture
def roots(tmp_path: Path) -> tuple[str, str]:
    home, other = tmp_path / "Home", tmp_path / "Other"
    for root in (home, other):
        (root / "Content" / "Verse").mkdir(parents=True)
    return os.path.realpath(home), os.path.realpath(other)


def _writer(roots: tuple[str, str], journal: Journal, *, others: bool = True, policies=()) -> ProjectWriter:
    home, other = roots

    def resolve(rel: str) -> str:
        full = os.path.realpath(rel if os.path.isabs(rel) else os.path.join(home, rel))
        for root in (home, other):
            if os.path.commonpath([full, root]) == root:
                return full
        raise ValueError("outside")

    return ProjectWriter(
        root_resolver=lambda: home, path_resolver=resolve, journal=journal, policies=policies,
        other_roots=(lambda: [other]) if others else tuple,
    )


def test_a_file_in_another_project_is_written_and_journaled_there(roots) -> None:
    journal = Journal()
    target = os.path.join(roots[1], "Content", "Verse", "shop.verse")
    _writer(roots, journal).write_text(target, "shop := class {}\n", op="create", tool="workspace_write_file")
    assert Path(target).read_text(encoding="utf-8") == "shop := class {}\n"
    record = journal.records[-1]
    assert record.project_root == roots[1]
    assert record.path == "Content/Verse/shop.verse"


def test_without_other_projects_the_open_one_is_the_only_place(roots) -> None:
    target = os.path.join(roots[1], "Content", "Verse", "shop.verse")
    with pytest.raises(ValueError, match="outside the active project"):
        _writer(roots, Journal(), others=False).write_text(target, "x", op="create")
    assert not os.path.exists(target)


def test_a_laned_agent_stays_in_the_open_project(roots) -> None:
    journal = Journal()
    writer = _writer(roots, journal, policies=[LanePolicy(mode=lambda: MODE_ENFORCE)])
    token = identity.bind(RunContext(run_id="r1", conv_id="c1", lane=("Content/**",)))
    try:
        writer.write_text("Content/Verse/home.verse", "ok", op="create")
        with pytest.raises(WriteDenied):
            writer.write_text(os.path.join(roots[1], "Content", "Verse", "home.verse"), "no", op="create")
    finally:
        identity.reset(token)
    assert not os.path.exists(os.path.join(roots[1], "Content", "Verse", "home.verse"))
    assert [r.project_root for r in journal.records] == [roots[0]]


def test_a_move_between_projects_is_refused(roots) -> None:
    writer = _writer(roots, Journal())
    writer.write_text("Content/Verse/a.verse", "a", op="create")
    with pytest.raises(ValueError, match="between projects"):
        writer.path_op(
            "move", os.path.join(roots[1], "Content", "Verse", "a.verse"),
            source="Content/Verse/a.verse", perform=lambda: {},
        )


def test_ducky_notes_land_beside_content_never_inside(tmp_path: Path) -> None:
    island = os.path.realpath(tmp_path / "Island")
    content = os.path.join(island, "Content")
    os.makedirs(content)

    def resolve(rel: str) -> str:
        full = os.path.realpath(rel if os.path.isabs(rel) else os.path.join(content, rel))
        if os.path.commonpath([full, island]) != island:
            raise ValueError("outside")
        return full

    journal = Journal()
    writer = ProjectWriter(root_resolver=lambda: content, path_resolver=resolve, journal=journal)
    writer.write_text(".ducky/swarms/s1/swarm.json", "{}", op="create")
    assert Path(island, ".ducky", "swarms", "s1", "swarm.json").read_text(encoding="utf-8") == "{}"
    assert not os.path.exists(os.path.join(content, ".ducky"))
    assert (journal.records[-1].project_root, journal.records[-1].path) == (island, ".ducky/swarms/s1/swarm.json")
