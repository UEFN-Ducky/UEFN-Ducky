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


def test_a_topic_label_gives_the_sentence_after_it_not_one_word():
    """1.2.492's What's new showed only "Bridge", "API keys", "Release tests", "perf"."""
    pub = _load()
    items, _ = pub.short_note_items([
        "Bridge: a lost reply is a timeout that may still be running",
        "API keys: an empty key row never wins over a saved key. Google's key sat …",
        "Release tests: the failed-test re-run uses pytest --lf",
        "perf: reduce backend retention and chat payloads (#27).",
        "Release desktop 1.2.491.",
        "Fix release tests: app_popups tools lacked a group prefix",
    ])
    assert [i["text"] for i in items] == [
        "A lost reply is a timeout that may still be running",
        "An empty key row never wins over a saved key",
        "Reduce backend retention and chat payloads",
    ]


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


def test_commits_marked_internal_or_about_the_apps_own_counting_stay_out_of_the_notes():
    pub = _load()
    subjects = pub.public_subjects([
        "Usage: a new PC's first open is sent right away, not a minute in",
        "Internal: Setup says whether the app came up after \"Open UEFN Ducky\"",
        "analytics : the first start is counted once",
        "Memory usage: the chat list no longer keeps closed chats",
        "Teams: members get the model the person asked for, not another program",
    ])
    assert subjects == [
        "Memory usage: the chat list no longer keeps closed chats",
        "Teams: members get the model the person asked for, not another program",
    ]
    assert "sent right away" not in pub.first_sentences(subjects)
    assert all("sent right away" not in item["text"] for item in pub.short_note_items(subjects)[0])


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


def test_notes_written_for_this_version_replace_the_drafts_and_no_other_version(tmp_path):
    """The release workflow's upload step takes no notes: a file names them for one version."""
    pub = _load()
    path = tmp_path / "release_notes.json"
    items = [{"kind": "fix", "text": "Agents keep their Ducky tools after Ducky restarts"}]
    path.write_text(json.dumps({"version": "1.2.499", "notes": "Agents keep their tools.", "items": items}), encoding="utf-8")
    assert pub.written_notes("1.2.499", path) == ("Agents keep their tools.", items)
    assert pub.written_notes("1.2.500", path) is None  # left behind: the next release drafts its own
    assert pub.written_notes("1.2.499", tmp_path / "missing.json") is None
    path.write_text("not json", encoding="utf-8")
    assert pub.written_notes("1.2.499", path) is None

    for bad in ({"notes": "", "items": items}, {"notes": "two\nlines", "items": items}, {"notes": "ok", "items": []},
                {"notes": "ok", "items": [{"kind": "fix", "text": "x" * 71}]}):
        path.write_text(json.dumps({"version": "1.2.499", **bad}), encoding="utf-8")
        with pytest.raises(SystemExit):
            pub.written_notes("1.2.499", path)
