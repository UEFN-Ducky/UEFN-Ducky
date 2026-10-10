"""The editor's grid / snap / panel settings live on disk and survive a reload."""

from backend.automations import editor_prefs


def test_saves_known_settings_and_merges(monkeypatch, tmp_path):
    monkeypatch.setattr(editor_prefs, "_path", lambda: tmp_path / "workflow_editor.json")
    assert editor_prefs.load() == {}
    editor_prefs.save({"grid": "dots", "snap": False, "junk": 1})
    editor_prefs.save({"panelZoom": {"list": 1.2}})
    assert editor_prefs.load() == {"grid": "dots", "snap": False, "panelZoom": {"list": 1.2}}
    (tmp_path / "workflow_editor.json").write_text("not json", encoding="utf-8")
    assert editor_prefs.load() == {}


def test_saving_unchanged_settings_does_not_rewrite_the_file(monkeypatch):
    """The Workflows view saves the settings it just loaded on every mount (and on
    every resize or zoom); each save wrote a backup, a temp file, fsynced and
    replaced the file even though nothing had changed."""
    from frontend import atomic_json

    editor_prefs.save({"grid": "dots", "snap": True, "panelWidths": {"list": 240}})
    path = editor_prefs._path()
    before = path.stat().st_mtime_ns
    writes: list[str] = []
    real_write = atomic_json.write_json_atomic

    def counting_write(target, data, **kwargs):
        writes.append(target.name)
        return real_write(target, data, **kwargs)

    monkeypatch.setattr(atomic_json, "write_json_atomic", counting_write)
    for _ in range(3):
        assert editor_prefs.save(editor_prefs.load()) == editor_prefs.load()
        editor_prefs.save({"grid": "dots"})
    assert writes == []
    assert path.stat().st_mtime_ns == before
    editor_prefs.save({"grid": "lines"})
    assert writes == ["workflow_editor.json"]
    assert editor_prefs.load()["grid"] == "lines"
