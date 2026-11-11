"""The context meter keeps what it counts from a chat's messages per message revision.

It ran after every turn and every pause in typing and opened the whole chat each time
(a DPAPI call per row and part, then a token count per message): 11% of the app's CPU with
one 130 MB chat open (Nov 11 2026).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from frontend.chat_store import Conversation
from frontend.settings import PanelSettings
from frontend.ui_web import context_meter_chat as kept, context_tokens as ct
from frontend.ui_web.chats import conversations as chat_convs, saves as chat_saves


@pytest.fixture
def chat(tmp_path: Path):
    project = str(tmp_path / "Island")
    Path(project).mkdir()
    settings = PanelSettings.load()
    settings.uefn_project_root = project
    settings.save()
    conv = chat_convs.create_conversation(title="Metered", project_root=project, skill_snapshot="x")
    for i in range(6):
        role = "user" if i % 2 == 0 else "assistant"
        text = f"message number {i} " * 30
        row = {"role": role, "content": text, "text": text, "ts": time.time()}
        if role == "assistant":
            row["usage"] = {"input_tokens": 100 + i, "output_tokens": 10 + i}
        chat_saves.append_message(conv, row, project)
    assert chat_saves.flush_chat_saves(conv.id)
    kept.forget()
    return conv, project


@pytest.fixture
def loads(monkeypatch) -> list[str]:
    """Every time the meter opens a whole chat."""
    seen: list[str] = []
    real = ct.load_conversation
    monkeypatch.setattr(ct, "load_conversation", lambda cid, *a, **k: (seen.append(cid), real(cid, *a, **k))[1])
    return seen


def _read(conv_id: str) -> tuple[tuple, tuple[int, int], bool]:
    settings = PanelSettings.load()
    opened = ct._open_meter_chat(conv_id)
    report = ct._kept_conversation_report(
        opened, settings, model="m", provider="openai", tool_result_format="toon", keep_last=20,
        apply_stale_clear=True,
    )
    asked = opened.fact("skill_intent", lambda messages: any("number 4" in str(m.get("content")) for m in messages))
    return report, ct._message_usage(opened), asked


@pytest.fixture
def steady_settings(monkeypatch) -> None:
    """A count is kept per settings state. Another test's background thread saving a
    setting between two reads recounts (rightly) and failed these under a loaded run."""
    monkeypatch.setattr(kept, "settings_mark", lambda settings: 1)


def test_a_second_read_opens_no_message(chat, loads, steady_settings) -> None:
    conv, _ = chat
    first = _read(conv.id)
    assert loads == [conv.id]  # one whole load, shared by every number
    assert first[0][1] > 0 and len(first[0][3]) == 6
    assert first[1] == (100 + 1 + 100 + 3 + 100 + 5, 10 + 1 + 10 + 3 + 10 + 5) and first[2] is True
    for _ in range(5):
        assert _read(conv.id) == first
    assert loads == [conv.id]


def test_a_new_message_is_counted(chat, loads) -> None:
    conv, project = chat
    first = _read(conv.id)
    fresh = chat_convs.load_conversation(conv.id, project)
    chat_saves.append_message(fresh, {"role": "user", "content": "one more " * 50, "text": "x", "ts": time.time()}, project)
    assert chat_saves.flush_chat_saves(conv.id)
    second = _read(conv.id)
    assert loads == [conv.id, conv.id]
    assert len(second[0][3]) == 7 and second[0][1] > first[0][1]
    assert _read(conv.id) == second and loads == [conv.id, conv.id]


def test_what_is_handed_out_can_be_changed_without_changing_what_is_kept(chat, loads) -> None:
    conv, _ = chat
    first = _read(conv.id)
    first[0][3].clear()
    first[0][2].append({"label": "junk"})
    again = _read(conv.id)
    assert len(again[0][3]) == 6 and again[0][2] == []
    again[0][3][0]["tokens"] = -1
    assert _read(conv.id)[0][3][0]["tokens"] > 0


def test_other_settings_are_counted_apart(chat, loads, monkeypatch) -> None:
    conv, _ = chat
    settings = PanelSettings.load()
    counted: list[int] = []
    real = ct._conversation_report
    monkeypatch.setattr(ct, "_conversation_report", lambda messages, **kw: (counted.append(kw["keep_last"]), real(messages, **kw))[1])
    kwargs = dict(model="m", provider="openai", tool_result_format="toon", apply_stale_clear=True)
    for _ in range(3):
        ct._kept_conversation_report(ct._open_meter_chat(conv.id), settings, keep_last=20, **kwargs)
    assert counted == [20]
    for _ in range(3):
        ct._kept_conversation_report(ct._open_meter_chat(conv.id), settings, keep_last=2, **kwargs)
    assert counted == [20, 2] and loads == [conv.id, conv.id]
    # Any setting moves the mark, so a changed setting is counted again too.
    changed = PanelSettings.load()
    changed.memory_keep_last_messages = int(changed.memory_keep_last_messages or 0) + 1
    assert kept.settings_mark(changed) != kept.settings_mark(settings)
    ct._kept_conversation_report(ct._open_meter_chat(conv.id), changed, keep_last=20, **kwargs)
    assert counted == [20, 2, 20]


def test_the_chat_still_gives_its_messages_to_code_that_asks(chat, loads) -> None:
    """A helper that reads ``conv.messages`` gets the real rows, never an empty list."""
    conv, _ = chat
    opened = ct._open_meter_chat(conv.id)
    assert isinstance(opened.conv, Conversation) and opened.conv.title == "Metered"
    assert loads == []
    assert [m["role"] for m in opened.conv.messages] == ["user", "assistant"] * 3
    assert loads == [conv.id]
    assert len(opened.conv.messages) == 6 and loads == [conv.id]


def test_a_save_on_its_way_reads_the_whole_chat_and_keeps_nothing(chat, loads, monkeypatch) -> None:
    conv, project = chat
    queued = chat_convs.load_conversation(conv.id, project)
    monkeypatch.setattr(chat_saves, "_queued", lambda cid: queued)
    _read(conv.id)
    _read(conv.id)
    assert loads == [conv.id, conv.id]
    assert not kept._facts


def test_a_missing_chat_reads_as_none(loads) -> None:
    opened = ct._open_meter_chat("no-such-chat")
    assert opened.conv is None and opened.messages() == []


def test_only_the_last_chats_are_kept(chat, loads, monkeypatch) -> None:
    conv, _ = chat
    monkeypatch.setattr(kept, "MAX_CHATS", 1)
    _read(conv.id)
    with kept._lock:
        kept._facts[("elsewhere", "other")] = (1, time.monotonic(), {})
        while len(kept._facts) > kept.MAX_CHATS:
            kept._facts.popitem(last=False)
    _read(conv.id)
    assert loads == [conv.id, conv.id]
    monkeypatch.setattr(kept, "TTL_S", -1.0)  # kept too long ago: counted again
    _read(conv.id)
    assert loads == [conv.id, conv.id, conv.id]


def test_an_external_agent_report_reads_the_chat_once(chat, loads, steady_settings) -> None:
    conv, project = chat
    fresh = chat_convs.load_conversation(conv.id, project)
    fresh.coding_agent = "claude_code"
    chat_saves.save_conversation(fresh, project)
    assert chat_saves.flush_chat_saves(conv.id)
    kept.forget()
    loads.clear()

    def conversation_tokens(report: dict) -> int:
        return next(int(i["tokens"]) for i in report["breakdown"] if i["id"] == "conversation")

    first = ct.compute_context_report(conv.id, "claude-test")
    assert loads == [conv.id] and conversation_tokens(first) > 0
    again = ct.compute_context_report(conv.id, "claude-test", draft_text="typing something new")
    assert loads == [conv.id]
    assert conversation_tokens(again) == conversation_tokens(first)
    assert (again["input_tokens"], again["output_tokens"]) == (first["input_tokens"], first["output_tokens"])
