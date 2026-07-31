"""Rolling chat context summaries + author-filtered project-memory index."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("UEFN_DUCKY_PROJECT_ROOT", raising=False)
    return tmp_path


def test_index_author_filter_shared_and_self(isolated_appdata, tmp_path):
    from backend.memory.project import index_markdown, save_entry

    root = str(tmp_path / "MemProj")
    (tmp_path / "MemProj").mkdir()
    save_entry("shared-note", "Everyone sees this.", description="Shared fact", author="", project_root=root)
    save_entry("alice-note", "Alice only.", description="Alice fact", author="Alice", project_root=root)
    save_entry("bob-note", "Bob only.", description="Bob fact", author="Bob", project_root=root)

    alice_idx = index_markdown(root, author_filter="Alice")
    assert "shared-note" in alice_idx
    assert "alice-note" in alice_idx
    assert "bob-note" not in alice_idx

    bob_idx = index_markdown(root, author_filter="bob")  # case-insensitive
    assert "shared-note" in bob_idx
    assert "bob-note" in bob_idx
    assert "alice-note" not in bob_idx


def test_should_compress_thresholds(isolated_appdata):
    from backend.agent.context_memory import should_compress
    from frontend.settings import PanelSettings

    s = PanelSettings.load()
    s.memory_auto_compress = True
    s.memory_keep_last_messages = 5
    s.memory_compress_messages = 10
    s.memory_compress_tokens = 1_000_000
    s.save()

    short = SimpleNamespace(
        messages=[{"role": "user", "content": "hi"} for _ in range(8)],
        context_summary="",
        context_summary_through=0,
    )
    assert should_compress(short, settings=s) is False

    long = SimpleNamespace(
        messages=[{"role": "user", "content": f"msg {i}"} for i in range(12)],
        context_summary="",
        context_summary_through=0,
    )
    assert should_compress(long, settings=s) is True

    s.memory_auto_compress = False
    s.save()
    assert should_compress(long, settings=s) is False
    assert should_compress(long, settings=s, force=True) is True


def test_compact_with_summary_keeps_messages(isolated_appdata, tmp_path, monkeypatch):
    from backend.agent.context_memory import build_compacted_messages, compress_conversation
    from frontend.chat_store import Conversation
    from frontend.settings import PanelSettings
    from frontend.ui_web import project_chats

    root = str(tmp_path / "ChatProj")
    (tmp_path / "ChatProj").mkdir()
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", root)
    s = PanelSettings.load()
    s.uefn_project_root = root
    s.memory_keep_last_messages = 3
    s.memory_compress_messages = 5
    s.memory_auto_compress = True
    s.save()

    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(10)]
    conv = Conversation(id="ctx-mem-test", title="T", messages=list(msgs))
    project_chats.save_conversation(conv, root)

    before_len = len(conv.messages)
    result = compress_conversation(conv, settings=s, project_root=root, force=True, use_llm=False)
    assert result["compressed"] is True
    assert len(conv.messages) == before_len
    assert conv.context_summary_through == before_len - 3
    assert conv.context_summary.strip()

    compacted = build_compacted_messages(
        conv.messages,
        keep_last=3,
        context_summary=conv.context_summary,
        context_summary_through=conv.context_summary_through,
    )
    assert len(compacted) == 4  # summary head + 3 live
    assert "Context memory" in compacted[0]["content"]
    assert compacted[-1]["content"] == "turn 9"


def test_memory_settings_round_trip(isolated_appdata):
    from frontend.settings import PanelSettings

    s = PanelSettings.load()
    s.memory_auto_compress = False
    s.memory_keep_last_messages = 15
    s.memory_compress_messages = 55
    s.memory_compress_tokens = 90_000
    s.memory_index_max_chars = 1800
    s.memory_summary_model = "openai:gpt-4o-mini"
    s.save()

    loaded = PanelSettings.load()
    assert loaded.memory_auto_compress is False
    assert loaded.memory_keep_last_messages == 15
    assert loaded.memory_compress_messages == 55
    assert loaded.memory_compress_tokens == 90_000
    assert loaded.memory_index_max_chars == 1800
    assert loaded.memory_summary_model == "openai:gpt-4o-mini"
