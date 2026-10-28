"""revision_push / revision_pull: lore_cli decides what to do, the listener does it."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.tools.uefn import level_viewport


def fake_lore(*, paths=None, states=None, both=None, marked=None):
    states = list(states or [{"ok": True, "in_sync": True, "behind": 0, "ahead": 0}])
    calls = []

    def repo_state(root, *, timeout=60.0):
        calls.append(("repo_state", root))
        return states.pop(0) if len(states) > 1 else states[0]

    def pending_paths(root, *, timeout=60.0):
        calls.append(("pending_paths", root))
        return {"ok": True, "paths": list(paths or []), "error": ""}

    def overlap(root, *, state=None, timeout=60.0):
        calls.append(("overlap", root))
        return both or {"ok": True, "files": [], "error": ""}

    def conflict_marked(files):
        calls.append(("conflict_marked", len(files)))
        return [f for f in files if f in (marked or [])]

    return SimpleNamespace(repo_state=repo_state, pending_paths=pending_paths, overlap=overlap,
                           conflict_marked=conflict_marked, calls=calls)


@pytest.fixture(autouse=True)
def others_locks():
    """Files the other PC holds, by normalised path (none unless a test adds some)."""
    from backend.bridge import island_locks

    held: dict[str, str] = {}
    watch = SimpleNamespace(others_locks=lambda island: dict(held),
                            norm_path=lambda p: p.replace("\\", "/").lower())
    with patch.object(island_locks, "_watch", return_value=watch):
        yield held


@pytest.fixture
def root():
    with patch.object(level_viewport, "_island_root", return_value="C:/Islands/MyIsland"):
        yield "C:/Islands/MyIsland"


def test_push_checks_in_the_pending_files_then_confirms(root) -> None:
    lore = fake_lore(paths=["C:/Islands/MyIsland/Content/A.uasset"])
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        send.return_value = {"ok": True, "checked_in": ["a", "b"], "saved": ["b"], "error": ""}
        out = json.loads(level_viewport.revision_push("moved the cube"))
    assert send.call_args.args == (
        "revision_check_in",
        {"files": ["C:/Islands/MyIsland/Content/A.uasset"], "description": "moved the cube"},
    )
    assert lore.calls == [("repo_state", root), ("pending_paths", root), ("conflict_marked", 1), ("repo_state", root)]
    assert out == {"ok": True, "checked_in": 2, "saved": 1, "in_sync": True, "behind": 0, "ahead": 0, "error": ""}


def test_push_names_a_pending_file_the_other_pc_holds_and_checks_nothing_in(root, others_locks) -> None:
    lore = fake_lore(paths=["C:/Islands/MyIsland/Content/A.uasset", "C:/Islands/MyIsland/Content/B.uasset"])
    others_locks["c:/islands/myisland/content/b.uasset"] = "Mich ou"
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        out = json.loads(level_viewport.revision_push("moved the cube"))
    send.assert_not_called()
    assert out["ok"] is False
    assert out["locked_by_other"] == [{"file": "C:/Islands/MyIsland/Content/B.uasset", "owner": "Mich ou"}]
    assert out["error"].startswith("B.uasset is being edited on the other PC by Mich ou")


def test_push_refuses_an_empty_description(root) -> None:
    with patch.object(level_viewport, "send_command") as send:
        out = json.loads(level_viewport.revision_push("  "))
    assert out["ok"] is False and "description" in out["error"]
    send.assert_not_called()


def test_push_stops_when_pending_files_cannot_be_listed(root) -> None:
    lore = fake_lore()
    lore.pending_paths = lambda r, timeout=60.0: {"ok": False, "paths": [], "error": "lore missing"}
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        out = json.loads(level_viewport.revision_push("d"))
    assert out == {"ok": False, "error": "lore missing"}
    send.assert_not_called()


def test_pull_up_to_date_never_touches_uefn(root) -> None:
    lore = fake_lore()
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        out = json.loads(level_viewport.revision_pull())
    assert out["ok"] is True and out["up_to_date"] is True
    send.assert_not_called()


def test_pull_syncs_when_behind_and_confirms(root) -> None:
    lore = fake_lore(states=[
        {"ok": True, "in_sync": False, "behind": 2, "ahead": 0},
        {"ok": True, "in_sync": True, "behind": 0, "ahead": 0},
    ])
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        send.return_value = {"ok": True, "synced": True, "reloaded": True, "seconds": 3.7, "error": ""}
        out = json.loads(level_viewport.revision_pull())
    assert send.call_args.args == ("revision_sync", {"force": False})
    assert out == {"ok": True, "reloaded": True, "seconds": 3.7, "in_sync": True, "behind": 0, "ahead": 0, "error": ""}


def test_pull_with_unsaved_work_asks_instead(root) -> None:
    lore = fake_lore(states=[{"ok": True, "in_sync": False, "behind": 1, "ahead": 0}])
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        send.return_value = {"ok": False, "needs_save": ["/MyIsland/X"], "synced": False}
        out = json.loads(level_viewport.revision_pull())
    assert out["ok"] is False and out["needs_save"] == ["/MyIsland/X"]
    assert "ask the person" in out["error"]
    assert lore.calls == [("repo_state", root), ("overlap", root)]


def test_no_island_open() -> None:
    with patch.object(level_viewport, "_island_root", return_value=""), \
            patch.object(level_viewport, "send_command") as send:
        assert json.loads(level_viewport.revision_pull())["ok"] is False
        assert json.loads(level_viewport.revision_push("d"))["ok"] is False
    send.assert_not_called()


def test_island_root_is_the_island_uefn_has_open() -> None:
    # Ducky's selected project can be another island: Lore must read the open one.
    with patch.object(level_viewport, "send_command", return_value={"island": "C:/Islands/Open/", "dirty": []}):
        assert level_viewport._island_root() == r"C:\Islands\Open"
    with patch.object(level_viewport, "send_command", return_value={"dirty": []}):
        assert level_viewport._island_root() == ""


def test_push_retries_a_check_in_that_fails_right_after_saving(root) -> None:
    lore = fake_lore()
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport.time, "sleep") as sleep, \
            patch.object(level_viewport, "send_command") as send:
        send.side_effect = [
            {"ok": False, "targets": ["/I/A"], "checked_in": [], "saved": ["/I/A"], "error": "check-in failed"},
            {"ok": True, "targets": ["/I/A"], "checked_in": ["/I/A"], "saved": [], "error": ""},
        ]
        out = json.loads(level_viewport.revision_push("moved the cube"))
    assert send.call_args.args == ("revision_check_in", {"files": ["/I/A"], "description": "moved the cube"})
    assert sleep.call_count == 1
    assert out["ok"] is True and out["checked_in"] == 1 and out["saved"] == 1


def test_push_does_not_retry_when_nothing_was_saved(root) -> None:
    lore = fake_lore()
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport.time, "sleep") as sleep, \
            patch.object(level_viewport, "send_command") as send:
        send.return_value = {"ok": False, "targets": ["/I/A"], "checked_in": [], "saved": [], "error": "remote said no"}
        out = json.loads(level_viewport.revision_push("d"))
    assert send.call_count == 1 and sleep.call_count == 0
    assert out["ok"] is False and out["error"] == "remote said no"


def test_push_refuses_while_the_island_is_behind(root) -> None:
    lore = fake_lore(states=[{"ok": True, "in_sync": False, "behind": True, "ahead": False}])
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        out = json.loads(level_viewport.revision_push("moved the cube"))
    assert out["ok"] is False and out["behind"] is True and "revision_pull" in out["error"]
    assert lore.calls == [("repo_state", root)]
    send.assert_not_called()


VERSE = {"rel": "Content/x.verse", "path": "C:/Islands/MyIsland/Content/x.verse", "local": "modified", "remote": "M"}
BEHIND = [{"ok": True, "in_sync": False, "behind": True, "ahead": False},
          {"ok": True, "in_sync": True, "behind": False, "ahead": False}]


def test_pull_refuses_when_a_file_changed_on_both_pcs(root) -> None:
    lore = fake_lore(states=BEHIND, both={"ok": True, "files": [VERSE], "error": ""})
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        out = json.loads(level_viewport.revision_pull())
    send.assert_not_called()
    assert out == {
        "ok": False,
        "both_changed": [{"rel": "Content/x.verse", "local": "modified", "remote": "M"}],
        "error": "1 file changed on both PCs (Content/x.verse): nothing was pulled. Ask the person what to do "
        "with each: keep this PC's version, take the other PC's, or merge by hand; then pull with allow_both_changed=true",
    }


def test_pull_refuses_when_the_overlap_cannot_be_read(root) -> None:
    lore = fake_lore(states=BEHIND, both={"ok": False, "files": [], "error": "lore revision timed out"})
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        out = json.loads(level_viewport.revision_pull())
    send.assert_not_called()
    assert out["ok"] is False and "lore revision timed out" in out["error"] and "allow_both_changed=true" in out["error"]


def test_forced_pull_over_both_changed_files_names_the_conflicted_ones(root) -> None:
    other = dict(VERSE, rel="Content/B.uasset", path="C:/Islands/MyIsland/Content/B.uasset")
    lore = fake_lore(states=BEHIND, both={"ok": True, "files": [VERSE, other], "error": ""})
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        send.side_effect = [
            {"ok": True, "synced": True, "reloaded": True, "seconds": 4.0, "error": ""},
            {"conflicted": [r"C:\Islands\MyIsland\Content\x.verse"], "error": ""},
        ]
        out = json.loads(level_viewport.revision_pull(allow_both_changed=True))
    assert send.call_args_list[0].args == ("revision_sync", {"force": False})
    assert send.call_args_list[1].args == (
        "revision_conflicts", {"paths": [VERSE["path"], other["path"]]})
    assert out["ok"] is True and out["conflicted"] == ["Content/x.verse"]
    assert [f["rel"] for f in out["both_changed"]] == ["Content/x.verse", "Content/B.uasset"]
    assert '"Resolve Conflict" ("Résoudre conflit")' in out["hint"] and "Résolution des conflits" in out["hint"]


def test_forced_pull_with_an_older_listener_still_warns(root) -> None:
    lore = fake_lore(states=BEHIND, both={"ok": True, "files": [VERSE], "error": ""})
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        send.side_effect = [{"ok": True, "synced": True, "reloaded": True}, RuntimeError("Unknown command")]
        out = json.loads(level_viewport.revision_pull(allow_both_changed=True))
    assert out["ok"] is True and out["conflicted"] == []
    assert "Unknown command" in out["hint"] and "check each file" in out["hint"]


def test_force_alone_does_not_pull_over_files_changed_on_both_pcs(root) -> None:
    # force is for unsaved work: it must not also wave a both-changed file through.
    lore = fake_lore(states=BEHIND, both={"ok": True, "files": [VERSE], "error": ""})
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        out = json.loads(level_viewport.revision_pull(force=True))
    assert out["ok"] is False and out["both_changed"] and "allow_both_changed=true" in out["error"]
    send.assert_not_called()


# Oct 27, live: Lore merged the Verse file, UEFN then held the editor behind this dialog, and
# the host's revision_sync call failed with "UEFN listener is not running".
CONFLICT_POPUP = {"hwnd": 77, "title": "Modifications en conflit", "rect": {}, "blocking": True, "enabled": True}


@pytest.fixture
def popups_on_screen():
    from backend.tools.core import uefn_popups

    shown: list[dict] = []
    with patch.object(uefn_popups, "list_popups", side_effect=lambda: [dict(p) for p in shown]), \
            patch.object(uefn_popups, "press_button") as press:
        yield shown
    press.assert_not_called()  # the person answers it, never Ducky


@pytest.mark.parametrize("sync", [ConnectionError("UEFN listener is not running"), TimeoutError("timed out"),
                                  {"ok": False, "synced": False, "error": "sync failed"}])
def test_pull_behind_unreals_conflict_dialog_explains_it(root, popups_on_screen, sync) -> None:
    popups_on_screen.append(CONFLICT_POPUP)
    lore = fake_lore(states=BEHIND, both={"ok": True, "files": [VERSE], "error": ""})
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        send.side_effect = [sync]
        out = json.loads(level_viewport.revision_pull(allow_both_changed=True))
    assert send.call_count == 1
    assert out == {"ok": False, "conflict_dialog": True,
                   "both_changed": [{"rel": "Content/x.verse", "local": "modified", "remote": "M"}],
                   "hint": level_viewport.CONFLICT_DIALOG_HINT}
    hint = out["hint"]
    assert hint.startswith('UEFN is showing its "Conflicting Changes" dialog ("Modifications en conflit")')
    for name in ("never press it", "Examiner les conflits", "Résolution des conflits", "Tout ce qui est actuel",
                 "Tout ce qui est entrant", "Résoudre dans VS Code", "Résoudre conflit", "revision_push"):
        assert name in hint


def test_a_failed_pull_without_the_dialog_keeps_the_error(root, popups_on_screen) -> None:
    popups_on_screen.append(dict(CONFLICT_POPUP, title="Message Log"))
    lore = fake_lore(states=BEHIND)
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command", side_effect=ConnectionError("UEFN listener is not running")):
        out = json.loads(level_viewport.revision_pull())
    assert out["ok"] is False and "conflict_dialog" not in out
    assert out["error"] == "UEFN listener is not running" and out["reloaded"] is False


def test_push_refuses_a_file_that_still_has_conflict_markers(root) -> None:
    merged = "C:/Islands/MyIsland/Content/conflict_test.verse"
    lore = fake_lore(paths=["C:/Islands/MyIsland/Content/A.uasset", merged], marked=[merged])
    with patch.object(level_viewport, "_lore_cli", return_value=lore), \
            patch.object(level_viewport, "send_command") as send:
        out = json.loads(level_viewport.revision_push("merged"))
    send.assert_not_called()
    assert out == {
        "ok": False,
        "conflict_markers": ["Content/conflict_test.verse"],
        "error": "Content/conflict_test.verse still has conflict markers from a merge: resolve it "
        "(UEFN's Conflict resolution tab or VS Code) before pushing",
    }
