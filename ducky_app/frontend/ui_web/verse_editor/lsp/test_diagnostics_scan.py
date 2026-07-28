"""Tests that incremental scans skip when nothing is stale."""

from __future__ import annotations

from pathlib import Path

from frontend.ui_web.verse_editor.lsp import diagnostics_cache
from frontend.ui_web.verse_editor.lsp.diagnostics_scan import scan_project_verse_diagnostics


def test_incremental_scan_skips_when_cache_fresh(tmp_path: Path, monkeypatch):
    content = tmp_path / "Content"
    content.mkdir()
    verse = content / "ok.verse"
    verse.write_text("using { /Fortnite.com/Devices }\n", encoding="utf-8")

    diagnostics_cache.clear(str(tmp_path))
    cache = diagnostics_cache.load(str(tmp_path))
    diagnostics_cache.apply_file(
        str(tmp_path),
        cache,
        "content/ok.verse",
        diagnostics_cache.fingerprint(verse),
        {"errors": 0, "warnings": 0, "items": []},
    )

    def _boom(*_a, **_k):
        raise AssertionError("ephemeral verse-lsp must not start when nothing is stale")

    monkeypatch.setattr(
        "frontend.ui_web.verse_editor.lsp.diagnostics_scan.LspStdioSession",
        _boom,
    )

    events: list[dict] = []

    def on_progress(**kwargs):
        events.append(dict(kwargs))

    result = scan_project_verse_diagnostics(str(tmp_path), full=False, on_progress=on_progress)
    assert result["scanned"] == 0
    assert result["files"]
    assert events == []


def test_full_scan_still_announces_started(tmp_path: Path, monkeypatch):
    content = tmp_path / "Content"
    content.mkdir()
    (content / "ok.verse").write_text("x\n", encoding="utf-8")
    diagnostics_cache.clear(str(tmp_path))

    class FakeSession:
        def __init__(self, *_a, **_k):
            pass

        def start(self):
            return None

        def request(self, *_a, **_k):
            raise RuntimeError("stop after started")

        def notify(self, *_a, **_k):
            return None

        def stop(self):
            return None

        def is_alive(self):
            return False

        def file_uri(self, rel: str = "") -> str:
            return f"file:///{rel}"

    monkeypatch.setattr(
        "frontend.ui_web.verse_editor.lsp.diagnostics_scan.LspStdioSession",
        FakeSession,
    )
    monkeypatch.setattr(
        "frontend.ui_web.verse_editor.lsp.diagnostics_scan.discover_verse_workspace",
        lambda _root: {"workspace_folders": [{"path": str(tmp_path), "name": "t"}], "watch_files": []},
    )

    events: list[str] = []

    def on_progress(**kwargs):
        events.append(str(kwargs.get("phase") or ""))

    try:
        scan_project_verse_diagnostics(str(tmp_path), full=True, on_progress=on_progress)
    except RuntimeError:
        pass
    assert "started" in events
