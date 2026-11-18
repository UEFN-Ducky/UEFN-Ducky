"""Every module that keeps a file in the app data folder finds the same folder."""

from pathlib import Path

import pytest


def _folders() -> dict[str, Path]:
    from backend.bridge import bridge_version, dynamic_tools
    from backend.tools.panel import panel_ai_plugins
    from backend.tools.verse import verse_stats
    from frontend import native_crash, perf_trace
    from frontend.ui_web import window_bounds
    from frontend.ui_web.terminal import run_durations

    return {
        "bridge_version": bridge_version.registry_dir().parent,
        "dynamic_tools": dynamic_tools._manifest_cache_path().parent,
        "panel_ai_plugins": panel_ai_plugins._note_path("k").parent.parent,
        "verse_stats": verse_stats.stats_path().parent,
        "perf_trace": perf_trace._perf_dir().parent,
        "native_crash": native_crash._dir().parent,
        "run_durations": run_durations._path().parent,
        "window_bounds": window_bounds._path().parent,
    }


@pytest.mark.parametrize("local_set", [True, False])
def test_the_named_modules_agree_on_the_app_data_folder(monkeypatch, tmp_path, local_set):
    from backend.bridge import bridge_version
    from backend.util.file_lock import held_file_lock
    from backend.workspace.writer import _process_file_lock
    from frontend.app_paths import resolve_app_data_dir

    monkeypatch.delenv(bridge_version.DIR_ENV, raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if local_set:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    else:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)

    want = resolve_app_data_dir()
    assert want.is_relative_to(tmp_path)
    assert _folders() == {name: want for name in _folders()}

    # The two lock writers and backend.util.file_lock share one locks folder.
    with _process_file_lock(str(tmp_path / "f.txt")), held_file_lock("probe", 0):
        pass
    locks = want / "locks"
    assert any(p.suffix == ".lock" and p.stem != "probe" for p in locks.iterdir())
    assert (locks / "probe.lock").is_file()
