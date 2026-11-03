"""A release drafts the What's new window's short changes from its commits."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RELEASE = Path(__file__).resolve().parents[1] / "release"


def _load():
    spec = importlib.util.spec_from_file_location("publish_app_notes_test", RELEASE / "publish_app.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_commits_become_short_changes_with_a_kind():
    pub = _load()
    items, left_out = pub.short_note_items([
        "Plugins on a team copy no longer start over by themselves. Panels that subscribed …",
        "Closing a plugin's tab lets go of what you were editing in a team copy: the automatic locks …",
        "New Higgsfield plugin, so pictures and videos come from 83 models.",
        "The window showing your other UEFN Ducky is its own window now.",
    ])
    assert items == [
        {"kind": "fix", "text": "Plugins on a team copy no longer start over by themselves"},
        {"kind": "improved", "text": "Closing a plugin's tab lets go of what you were editing in a team copy"},
        {"kind": "new", "text": "New Higgsfield plugin"},
        {"kind": "improved", "text": "The window showing your other UEFN Ducky is its own window now"},
    ]
    assert left_out == []


def test_long_changes_and_more_than_five_stay_out_of_the_window():
    pub = _load()
    long = "On one account across several PCs a lock belongs to one PC and the others say so by name."
    subjects = [long] + [f"Change number {i} works now." for i in range(6)]
    items, left_out = pub.short_note_items(subjects)
    assert len(items) == 5
    assert [i["text"] for i in items] == [f"Change number {i} works now" for i in range(1, 6)]
    assert all(i["kind"] == "fix" for i in items)
    assert left_out == [long.rstrip("."), "Change number 0 works now"]


def test_internal_engine_commits_and_repeats_stay_out_of_the_notes():
    pub = _load()
    assert pub.public_subjects([
        "Add team_engine facade: core reaches the team-sync engine through the spaces provider",
        "General fixes and improvements",
        "Open chats with the newest 80 rows",
        "The sync engine moves elsewhere",
        "General fixes and improvements",
    ]) == ["General fixes and improvements", "Open chats with the newest 80 rows"]


def test_notes_file_is_read_and_checked(tmp_path):
    pub = _load()
    good = tmp_path / "notes.json"
    good.write_text(json.dumps([{"kind": "fix", "text": "Team plugins no longer reset by themselves"}]), encoding="utf-8")
    assert pub.load_note_items(good) == [{"kind": "fix", "text": "Team plugins no longer reset by themselves"}]

    for bad in ([], [{"kind": "bug", "text": "x"}], [{"kind": "fix", "text": "x" * 71}], [{"kind": "new", "text": f"{i}"} for i in range(6)]):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(SystemExit):
            pub.load_note_items(path)
