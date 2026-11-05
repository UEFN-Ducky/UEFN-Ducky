"""Rolling chat context summaries + shared project-memory index (private entries hidden)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("UEFN_DUCKY_PROJECT_ROOT", raising=False)
    return tmp_path


def test_index_is_shared_and_hides_only_private_entries(isolated_appdata, tmp_path):
    from backend.memory.project import index_markdown, read_entry, save_entry

    root = str(tmp_path / "MemProj")
    (tmp_path / "MemProj").mkdir()
    save_entry("shared-note", "Everyone sees this.", description="Shared fact", author="", project_root=root)
    save_entry("alice-note", "Alice wrote it.", description="Alice fact", author="Alice", project_root=root)
    save_entry("bob-note", "Bob wrote it.", description="Bob fact", author="Bob", project_root=root)
    save_entry("alice-diary", "Alice only.", description="Alice private", author="Alice", private=True, project_root=root)
    assert read_entry("alice-diary", root)["private"] is True
    assert read_entry("alice-diary", root)["author"] == "Alice"

    bob_idx = index_markdown(root, private_author="bob")
    assert "shared-note" in bob_idx and "bob-note" in bob_idx
    assert "alice-note" in bob_idx  # other authors' lessons are shared
    assert "alice-diary" not in bob_idx  # a private entry stays hidden from Bob

    alice_idx = index_markdown(root, private_author="ALICE")  # case-insensitive
    assert "alice-diary" in alice_idx and "bob-note" in alice_idx

    # author_filter is the deprecated alias of private_author.
    assert "alice-diary" in index_markdown(root, author_filter="Alice")
    assert "alice-diary" not in index_markdown(root)

    # Saving again without private= keeps the flag.
    save_entry("alice-diary", "Still private.", project_root=root)
    assert read_entry("alice-diary", root)["private"] is True


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
    from frontend.ui_web.chats import saves as chat_saves

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
    chat_saves.save_conversation(conv, root)

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


def test_memory_index_old_default_moves_to_new_default(isolated_appdata):
    from frontend.settings import MEMORY_INDEX_DEFAULT_CHARS, PanelSettings

    assert MEMORY_INDEX_DEFAULT_CHARS == 8_000
    assert PanelSettings().memory_index_max_chars == 8_000  # fresh install
    s = PanelSettings.load()
    s.memory_index_max_chars = 2_500  # the old default, saved by an older build
    s.save()
    assert PanelSettings.load().memory_index_max_chars == 8_000


def test_epoch_hysteresis_and_append_only_head(isolated_appdata, tmp_path, monkeypatch):
    from backend.agent.context_memory import (
        CONTEXT_MEMORY_PREFIX,
        build_compacted_messages,
        compress_conversation,
        should_compress,
    )
    from frontend.chat_store import Conversation
    from frontend.settings import PanelSettings
    from frontend.ui_web.chats import saves as chat_saves

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
    chat_saves.save_conversation(conv, root)
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
    from frontend.ui_web.chats import saves as chat_saves

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
    chat_saves.save_conversation(auto, root)
    chat_saves.save_conversation(manual, root)
    a = compress_conversation(auto, settings=s, project_root=root, force=False, use_llm=False)
    m = compress_conversation(manual, settings=s, project_root=root, force=True, use_llm=False)
    assert a["compressed"] and m["compressed"]
    assert auto.context_summary_through == manual.context_summary_through == 12 - 4


def test_a_big_fold_reaches_the_digest_in_order(monkeypatch):
    # Nov 3 2026: one summary call read the first 24k chars of a fold; at the 80k-token
    # high-water most turns of a big job never reached the digest.
    import asyncio

    from backend.agent import context_memory as cm
    from backend.agent import batch_backends
    from frontend.ui_web import plugin_llm

    seen: list[str] = []

    async def fake_complete(*, provider_name, model, system, user):
        seen.append(user)
        return f"digest after {len(seen)} folds"

    monkeypatch.setattr(cm, "_resolve_summary_model", lambda s: ("uefn_ducky", "ducky-ai"))
    monkeypatch.setattr(batch_backends, "supports_batch_complete", lambda p: True)
    monkeypatch.setattr(plugin_llm, "_complete_text", fake_complete)
    turns = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn-{i:03d} " + "x" * 3000} for i in range(60)]

    digest = asyncio.run(cm._llm_digest("old digest", turns, None, original_goal="Build Neon Orbit"))

    assert len(seen) > 1, "a big fold is read in chunks"
    assert len(seen) <= cm._FOLD_MAX_CHUNKS
    every = "\n".join(seen)
    for i in range(60):
        assert f"turn-{i:03d}" in every, f"turn {i} never reached the digest"
    assert "…[truncated]" not in every
    assert all("Build Neon Orbit" in u for u in seen), "the goal rides along with every chunk"
    assert "old digest" in seen[0] and "digest after 1 folds" in seen[1], "each chunk merges into the digest so far"
    order = [every.index(f"turn-{i:03d}") for i in range(60)]
    assert order == sorted(order)
    assert digest == f"digest after {len(seen)} folds"


def test_a_huge_fold_costs_at_most_the_chunk_cap(monkeypatch):
    from backend.agent import context_memory as cm

    turns = [{"role": "user", "content": "y" * 3900} for _ in range(400)]
    chunks = cm._fold_chunks(turns)
    assert len(chunks) <= cm._FOLD_MAX_CHUNKS
    assert sum(len(c) for c in chunks) == 400
