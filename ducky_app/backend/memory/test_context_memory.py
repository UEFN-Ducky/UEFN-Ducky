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
    # Auto-off still epochs at high-water (mechanical emergency bound).
    assert should_compress(long, settings=s) is True
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


def test_epoch_hysteresis_and_append_only_head(isolated_appdata, tmp_path, monkeypatch):
    from backend.agent.context_memory import (
        CONTEXT_MEMORY_PREFIX,
        build_compacted_messages,
        compress_conversation,
        should_compress,
    )
    from frontend.chat_store import Conversation
    from frontend.settings import PanelSettings
    from frontend.ui_web import project_chats

    root = str(tmp_path / "ChatProj2")
    (tmp_path / "ChatProj2").mkdir()
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", root)
    s = PanelSettings.load()
    s.uefn_project_root = root
    s.memory_keep_last_messages = 5
    s.memory_compress_messages = 10
    s.memory_compress_tokens = 1_000_000
    s.memory_auto_compress = True
    s.save()

    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(12)]
    conv = Conversation(id="epoch-hyst", title="T", messages=list(msgs))
    project_chats.save_conversation(conv, root)
    assert should_compress(conv, settings=s) is True
    result = compress_conversation(conv, settings=s, project_root=root, force=True, use_llm=False)
    assert result["compressed"] is True
    assert conv.context_summary_through == 7
    assert should_compress(conv, settings=s) is False

    head = build_compacted_messages(
        conv.messages,
        keep_last=5,
        context_summary=conv.context_summary,
        context_summary_through=conv.context_summary_through,
    )[0]["content"]
    assert head.startswith(CONTEXT_MEMORY_PREFIX)
    conv.messages.append({"role": "user", "content": "turn 12"})
    head2 = build_compacted_messages(
        conv.messages,
        keep_last=5,
        context_summary=conv.context_summary,
        context_summary_through=conv.context_summary_through,
    )[0]["content"]
    assert head2 == head
    assert should_compress(conv, settings=s) is False


def test_no_epoch_view_is_full_history():
    from backend.agent.context_memory import build_compacted_messages

    msgs = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    view = build_compacted_messages(msgs, keep_last=5)
    assert view == msgs


def test_high_water_clamps_to_small_model_window(isolated_appdata):
    from backend.agent.context_memory import token_high_water
    from frontend.settings import PanelSettings

    s = PanelSettings.load()
    s.memory_compress_tokens = 80_000
    s.save()
    hw = token_high_water(s, context_limit=8_192)
    assert hw == int(8_192 * 0.65)
    hw32 = token_high_water(s, context_limit=32_768)
    assert hw32 == int(32_768 * 0.65)
    assert token_high_water(s, context_limit=None) == 80_000
    from backend.agent.context_memory import OUTPUT_HEADROOM_TOKENS, epoch_num_ctx

    assert epoch_num_ctx(32_768, s) == min(32_768, hw32 + OUTPUT_HEADROOM_TOKENS)
    assert epoch_num_ctx(8_192, s) == 8_192


def test_estimator_counts_full_tool_result_bytes():
    from backend.agent.context_memory import estimate_messages_tokens

    payload = "x" * 8000
    msgs = [
        {
            "role": "assistant",
            "content": "ok",
            "blocks": [
                {
                    "type": "tool_call",
                    "name": "read_file",
                    "result": {"data": payload},
                }
            ],
        }
    ]
    est = estimate_messages_tokens(msgs)
    assert est >= 2000


def test_compress_now_same_low_water(isolated_appdata, tmp_path, monkeypatch):
    from backend.agent.context_memory import compress_conversation
    from frontend.chat_store import Conversation
    from frontend.settings import PanelSettings
    from frontend.ui_web import project_chats

    root = str(tmp_path / "ChatProj3")
    (tmp_path / "ChatProj3").mkdir()
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", root)
    s = PanelSettings.load()
    s.uefn_project_root = root
    s.memory_keep_last_messages = 4
    s.memory_compress_messages = 8
    s.save()

    msgs = [{"role": "user", "content": f"t{i}"} for i in range(12)]
    auto = Conversation(id="auto-ep", title="A", messages=list(msgs))
    manual = Conversation(id="man-ep", title="M", messages=list(msgs))
    project_chats.save_conversation(auto, root)
    project_chats.save_conversation(manual, root)
    a = compress_conversation(auto, settings=s, project_root=root, force=False, use_llm=False)
    m = compress_conversation(manual, settings=s, project_root=root, force=True, use_llm=False)
    assert a["compressed"] and m["compressed"]
    assert auto.context_summary_through == manual.context_summary_through == 12 - 4
