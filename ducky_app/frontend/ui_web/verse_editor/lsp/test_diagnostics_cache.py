"""Tests for in-memory Verse diagnostics stale tracking."""

from __future__ import annotations

from pathlib import Path

from frontend.ui_web.verse_editor.lsp import diagnostics_cache


def _make_project(tmp_path: Path, names: list[str]) -> Path:
    content = tmp_path / "Content"
    content.mkdir()
    for name in names:
        (content / name).write_text(f"# {name}\n", encoding="utf-8")
    return tmp_path


def test_empty_cache_marks_all_files_stale(tmp_path: Path):
    root = _make_project(tmp_path, ["a.verse", "b.verse"])
    diagnostics_cache.clear(str(root))
    stale = diagnostics_cache.stale_keys(str(root))
    assert sorted(stale) == ["content/a.verse", "content/b.verse"]


def test_apply_file_makes_entry_fresh(tmp_path: Path):
    root = _make_project(tmp_path, ["ok.verse"])
    diagnostics_cache.clear(str(root))
    abs_path = root / "Content" / "ok.verse"
    cache = diagnostics_cache.load(str(root))
    diagnostics_cache.apply_file(
        str(root),
        cache,
        "content/ok.verse",
        diagnostics_cache.fingerprint(abs_path),
        {"errors": 0, "warnings": 0, "items": []},
    )
    assert diagnostics_cache.stale_keys(str(root), cache) == []


def test_mtime_change_marks_stale(tmp_path: Path):
    root = _make_project(tmp_path, ["ok.verse"])
    diagnostics_cache.clear(str(root))
    abs_path = root / "Content" / "ok.verse"
    cache = diagnostics_cache.load(str(root))
    diagnostics_cache.apply_file(
        str(root),
        cache,
        "content/ok.verse",
        diagnostics_cache.fingerprint(abs_path),
        {"errors": 0, "warnings": 0, "items": []},
    )
    abs_path.write_text("# changed\n", encoding="utf-8")
    assert diagnostics_cache.stale_keys(str(root), cache) == ["content/ok.verse"]


def test_cached_errors_always_stale_even_when_mtime_fresh(tmp_path: Path):
    """False-positive Problems must not pin forever on an unchanged fingerprint."""
    root = _make_project(tmp_path, ["bad.verse", "ok.verse"])
    diagnostics_cache.clear(str(root))
    cache = diagnostics_cache.load(str(root))
    bad = root / "Content" / "bad.verse"
    ok = root / "Content" / "ok.verse"
    diagnostics_cache.apply_file(
        str(root),
        cache,
        "content/bad.verse",
        diagnostics_cache.fingerprint(bad),
        {
            "errors": 1,
            "warnings": 0,
            "items": [{"line": 1, "column": 1, "message": "ghost", "severity": "error"}],
        },
    )
    diagnostics_cache.apply_file(
        str(root),
        cache,
        "content/ok.verse",
        diagnostics_cache.fingerprint(ok),
        {"errors": 0, "warnings": 0, "items": []},
    )
    assert diagnostics_cache.stale_keys(str(root), cache) == ["content/bad.verse"]


def test_load_for_ui_prunes_deleted(tmp_path: Path):
    root = _make_project(tmp_path, ["keep.verse", "gone.verse"])
    diagnostics_cache.clear(str(root))
    cache = diagnostics_cache.load(str(root))
    for name in ("keep.verse", "gone.verse"):
        abs_path = root / "Content" / name
        diagnostics_cache.apply_file(
            str(root),
            cache,
            f"content/{name}",
            diagnostics_cache.fingerprint(abs_path),
            {"errors": 0, "warnings": 0, "items": []},
        )
    (root / "Content" / "gone.verse").unlink()
    ui = diagnostics_cache.load_for_ui(str(root))
    paths = {f["path"] for f in ui["files"]}
    assert paths == {"content/keep.verse"}
    assert ui["stale_count"] == 0


def test_disk_cache_survives_memory_clear(tmp_path: Path, monkeypatch):
    """Restart simulation: drop RAM, reload from disk — unchanged files stay fresh."""
    root = _make_project(tmp_path, ["ok.verse"])
    cache_root = tmp_path / "appdata"
    cache_root.mkdir()
    monkeypatch.setattr(
        "frontend.settings.default_app_data_dir",
        lambda: cache_root,
    )
    diagnostics_cache.clear(str(root))
    cache = diagnostics_cache.load(str(root))
    abs_path = root / "Content" / "ok.verse"
    diagnostics_cache.apply_file(
        str(root),
        cache,
        "content/ok.verse",
        diagnostics_cache.fingerprint(abs_path),
        {"errors": 0, "warnings": 0, "items": []},
        persist=True,
    )
    # Simulate process restart: wipe in-memory map only (disk stays).
    with diagnostics_cache._mem_lock:
        diagnostics_cache._MEM.clear()
    diagnostics_cache._legacy_purged = True
    assert diagnostics_cache.stale_keys(str(root)) == []
    ui = diagnostics_cache.load_for_ui(str(root))
    assert ui["stale_count"] == 0
    assert ui["files"][0]["path"] == "content/ok.verse"


def test_deleted_disk_cache_drops_memory_zombies(tmp_path: Path, monkeypatch):
    """If the on-disk cache file is removed, RAM must not keep resurrecting errors."""
    root = _make_project(tmp_path, ["ok.verse"])
    cache_root = tmp_path / "appdata"
    cache_root.mkdir()
    monkeypatch.setattr(
        "frontend.settings.default_app_data_dir",
        lambda: cache_root,
    )
    diagnostics_cache.clear(str(root))
    cache = diagnostics_cache.load(str(root))
    abs_path = root / "Content" / "ok.verse"
    diagnostics_cache.apply_file(
        str(root),
        cache,
        "content/ok.verse",
        diagnostics_cache.fingerprint(abs_path),
        {
            "errors": 1,
            "warnings": 0,
            "items": [{"line": 1, "column": 1, "message": "zombie", "severity": "error"}],
        },
        persist=True,
    )
    diagnostics_cache._disk_path(str(root)).unlink(missing_ok=True)
    ui = diagnostics_cache.load_for_ui(str(root))
    assert ui["files"] == []
    assert ui["stale_count"] == 1


def test_external_disk_rewrite_reloads_memory(tmp_path: Path, monkeypatch):
    """Another process writing a clean cache must win over poisoned RAM."""
    import json
    import time

    root = _make_project(tmp_path, ["ok.verse"])
    cache_root = tmp_path / "appdata"
    cache_root.mkdir()
    monkeypatch.setattr(
        "frontend.settings.default_app_data_dir",
        lambda: cache_root,
    )
    diagnostics_cache.clear(str(root))
    cache = diagnostics_cache.load(str(root))
    abs_path = root / "Content" / "ok.verse"
    diagnostics_cache.apply_file(
        str(root),
        cache,
        "content/ok.verse",
        diagnostics_cache.fingerprint(abs_path),
        {
            "errors": 1,
            "warnings": 0,
            "items": [{"line": 1, "column": 1, "message": "ghost", "severity": "error"}],
        },
        persist=True,
    )
    assert diagnostics_cache.load_for_ui(str(root))["files"][0]["errors"] == 1

    disk = diagnostics_cache._disk_path(str(root))
    time.sleep(0.02)  # ensure mtime_ns changes on Windows
    disk.write_text(
        json.dumps(
            {
                "v": diagnostics_cache.CACHE_VERSION,
                "files": {
                    "content/ok.verse": {
                        "mtime_ns": diagnostics_cache.fingerprint(abs_path)[0],
                        "size": diagnostics_cache.fingerprint(abs_path)[1],
                        "errors": 0,
                        "warnings": 0,
                        "items": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    ui = diagnostics_cache.load_for_ui(str(root))
    assert ui["files"][0]["errors"] == 0
    assert ui["stale_count"] == 0
