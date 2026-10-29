"""A running agent's checkpoint costs the same on a long chat as on a short one."""

from __future__ import annotations

import statistics
import time

from backend.store.repos import chats as repo
from frontend.settings import PanelSettings
from frontend.ui_web.project_chats import (
    create_conversation,
    flush_chat_saves,
    load_conversation,
    save_conversation,
    upsert_in_flight_assistant,
)


def _chat(root: str, n: int):
    conv = create_conversation(PanelSettings.load(), "", title=f"chat {n}", project_root=root)
    result = "line of a tool result\n" * 60
    conv.messages = [
        {"role": "user", "text": f"q{i}", "content": f"q{i}", "ts": i}
        if i % 2 == 0
        else {"role": "assistant", "content": "a", "text": "a", "ts": i, "run_id": f"r{i}", "blocks": [
            {"type": "tool_call", "id": f"c{i}", "name": "Read", "status": "success",
             "arguments": {"path": f"f{i}.py"}, "result": {"ok": True, "data": result}}]}
        for i in range(n)
    ]
    conv.messages.append({"role": "user", "text": "go", "content": "go", "ts": n})
    save_conversation(conv, root)
    return conv


def _turn(i: int) -> dict:
    blocks = [{"type": "tool_call", "id": f"t{k}", "name": "Read", "status": "success"} for k in range(i % 5 + 1)]
    return {"role": "assistant", "content": f"step {i}", "text": f"step {i}", "incomplete": True, "blocks": blocks}


def _checkpoint_ms(conv, root: str, calls: int = 30) -> float:
    times = []
    for i in range(calls):
        t0 = time.perf_counter()
        upsert_in_flight_assistant(conv, _turn(i), run_id="live", project_root=root)
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def test_checkpoint_cost_does_not_grow_with_the_chat(tmp_path) -> None:
    root = str(tmp_path)
    cost = {n: _checkpoint_ms(_chat(root, n), root) for n in (200, 1000, 3000)}
    print("checkpoint ms by chat size:", {n: round(ms, 2) for n, ms in cost.items()})
    assert cost[3000] <= 3 * max(cost[200], 1.0)


def test_a_checkpoint_writes_one_row_and_opens_none(tmp_path, monkeypatch) -> None:
    root = str(tmp_path)
    conv = _chat(root, 400)
    upsert_in_flight_assistant(conv, _turn(0), run_id="live", project_root=root)
    flush_chat_saves()
    opened, written = [], []
    real_message, real_write = repo._message, repo._write_row
    monkeypatch.setattr(repo, "_message", lambda *a: opened.append(1) or real_message(*a))
    monkeypatch.setattr(repo, "_write_row", lambda *a, **kw: written.append(a[2]) or real_write(*a, **kw))
    for i in range(1, 6):
        upsert_in_flight_assistant(conv, _turn(i), run_id="live", project_root=root)
        flush_chat_saves(conv.id)  # queued checkpoints coalesce: write each one
    assert opened == [] and written == [401] * 5  # 400 rows, the user's "go", then this run
    stored = load_conversation(conv.id, root)
    assert stored is not None and stored.messages[-1]["content"] == "step 5"
    assert len(stored.messages) == 402 and stored.messages == conv.messages
    assert stored.tool_call_count == 200 + 1 + 5 % 5


def test_a_row_added_elsewhere_is_kept_and_seen(tmp_path) -> None:
    root = str(tmp_path)
    conv = _chat(root, 10)
    upsert_in_flight_assistant(conv, _turn(0), run_id="live", project_root=root)
    other = load_conversation(conv.id, root)
    other.messages.append({"role": "user", "text": "steer", "content": "steer", "ts": 99})
    save_conversation(other, root)
    upsert_in_flight_assistant(conv, _turn(1), run_id="live", project_root=root)
    stored = load_conversation(conv.id, root)
    # The steer is the last user row now, so the run's next checkpoint starts a new row after it.
    assert [m["content"] for m in stored.messages[-3:]] == ["step 0", "steer", "step 1"]
    # The writer saw the store move; the copy takes their tail on its next checkpoint.
    upsert_in_flight_assistant(conv, _turn(2), run_id="live", project_root=root)
    stored = load_conversation(conv.id, root)
    assert [m["content"] for m in stored.messages[-3:]] == ["step 0", "steer", "step 2"]
    assert conv.messages == stored.messages
