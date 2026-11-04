"""Plugins that keep declared folders: a move is noticed, and the app restarts it once."""

from __future__ import annotations

import types

from backend.uefn_plugins.plugin_host import folders, state


def test_a_plugin_without_folders_never_reloads() -> None:
    assert folders._folders_moved("no-folders-probe") is False
    assert folders.reload_if_folders_moved("no-folders-probe") is False


def test_a_moved_epoch_waits_out_the_grace_then_restarts_once(monkeypatch) -> None:
    import backend.store.repos.folder_files as folder_files
    import backend.uefn_plugins.team_folders as team_folders

    restarted: list[str] = []
    monkeypatch.setattr(folder_files, "reload_epoch", lambda pid: 2)
    monkeypatch.setattr(team_folders, "live_calls", lambda pid: 0)
    monkeypatch.setattr(folders, "threading", types.SimpleNamespace(
        Thread=lambda target, args, **kw: types.SimpleNamespace(start=lambda: restarted.append(args[0]))))
    state._FOLDER_EPOCH["fold-probe"] = 1
    try:
        assert folders.reload_moved_plugins(now=100.0) == []  # first sight of the move
        assert folders.reload_moved_plugins(now=100.0 + state.FOLDER_RELOAD_GRACE_S / 2) == []
        assert folders.reload_moved_plugins(now=100.0 + state.FOLDER_RELOAD_GRACE_S) == ["fold-probe"]
        assert restarted == ["fold-probe"] and "fold-probe" not in state._FOLDER_EPOCH
    finally:
        state._FOLDER_EPOCH.pop("fold-probe", None)
        state._FOLDER_MOVED.pop("fold-probe", None)
