"""The diff-baseline content cache holds recently used files, not every file of the session."""

from __future__ import annotations

from frontend.ui_web.verse_editor import io


def test_a_session_of_file_reads_keeps_only_recent_files(monkeypatch) -> None:
    monkeypatch.setattr(io, "_cache", io.ContentCache())
    io.seed_cache("Content/Verse/first.verse", "first")
    text = "x" * 100_000
    for n in range(1000):
        io.seed_cache(f"Content/Generated/file{n}.json", text + str(n))
    assert len(io._cache._by_path) <= 256
    assert io.get_cached("Content/Generated/file999.json") == text + "999"
    assert io.get_cached("Content/Verse/first.verse") is None


def test_big_generated_files_are_capped_by_size() -> None:
    cache = io.ContentCache()
    for n in range(20):
        cache.set(f"Content/Generated/big{n}.json", str(n) * 4_000_000)
    assert sum(len(text) for text in cache._by_path.values()) <= 32 * 1024 * 1024
    assert cache.get("Content/Generated/big19.json") == "19" * 4_000_000


def test_panel_save_returns_the_disk_stat_the_watcher_will_see(monkeypatch, tmp_path) -> None:
    import types

    from backend.workspace import runtime

    target = tmp_path / "Content" / "a.verse"
    target.parent.mkdir()
    pf = io.pf
    for name in ("_require_writable_content_path", "_require_not_locked", "_require_not_digest"):
        monkeypatch.setattr(pf, name, lambda *_a: None)
    monkeypatch.setattr(pf, "_require_under_content", lambda *_a: None)
    monkeypatch.setattr(pf, "is_editable_text_file", lambda _p: True)
    monkeypatch.setattr(pf, "_resolve_relative", lambda _p: target)
    monkeypatch.setattr(pf, "_project_root", lambda: tmp_path)

    class Writer:
        def write_text(self, rel, content, tool=""):
            (tmp_path / rel).write_text(content, encoding="utf-8")
            return types.SimpleNamespace(bytes_written=len(content.encode("utf-8")))

    monkeypatch.setattr(runtime, "get_writer", lambda: Writer())
    out = io.write_file("Content/a.verse", "hello")
    st = target.stat()
    assert out["path"] == "Content/a.verse"
    assert (out["mtime_ns"], out["size"]) == (st.st_mtime_ns, st.st_size)


def test_a_file_used_again_stays_cached() -> None:
    cache = io.ContentCache()
    cache.set("Content/Verse/main.verse", "baseline")
    for n in range(600):
        cache.set(f"Content/Generated/file{n}.json", "x")
        assert cache.get("Content/Verse/main.verse") == "baseline"
    cache.set("Content/Verse/main.verse", "after edit")
    assert cache.get("Content/Verse/main.verse") == "after edit"
    assert len(cache._by_path) <= 256
