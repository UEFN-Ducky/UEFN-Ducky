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


def test_a_file_used_again_stays_cached() -> None:
    cache = io.ContentCache()
    cache.set("Content/Verse/main.verse", "baseline")
    for n in range(600):
        cache.set(f"Content/Generated/file{n}.json", "x")
        assert cache.get("Content/Verse/main.verse") == "baseline"
    cache.set("Content/Verse/main.verse", "after edit")
    assert cache.get("Content/Verse/main.verse") == "after edit"
    assert len(cache._by_path) <= 256
