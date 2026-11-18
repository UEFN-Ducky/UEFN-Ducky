"""The host's batcher loop sleeps until a turn event arrives instead of ticking 20 times a second."""

from __future__ import annotations

import threading
import time

from frontend.agent_host import main
from frontend.agent_host.batching import Batcher


class _Counting(Batcher):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.wakeups = 0

    def flush_due(self) -> None:
        self.wakeups += 1
        super().flush_due()


def _run(batcher: Batcher) -> tuple[threading.Event, threading.Thread]:
    done = threading.Event()
    thread = threading.Thread(target=main.pump, args=(batcher, done), daemon=True)
    thread.start()
    return done, thread


def _stop(batcher: Batcher, done: threading.Event, thread: threading.Thread) -> None:
    done.set()
    batcher.wake.set()
    thread.join(2.0)
    assert not thread.is_alive()


def test_an_idle_host_wakes_at_most_twice_a_second():
    batcher = _Counting(lambda conv_id, events: None)
    done, thread = _run(batcher)
    time.sleep(2.1)
    _stop(batcher, done, thread)
    assert batcher.wakeups <= 4  # 2.1 s idle: was 42


def test_a_queued_event_is_sent_within_60_ms():
    sent: list[float] = []
    batcher = Batcher(lambda conv_id, events: sent.append(time.monotonic()), per_second=1000.0)
    done, thread = _run(batcher)
    time.sleep(0.2)  # the loop is in its idle wait
    fed = time.monotonic()
    batcher.feed({"conv_id": "c1", "run_id": "r1", "type": "text_delta", "text": "hi"})
    deadline = time.monotonic() + 1.0
    while not sent and time.monotonic() < deadline:
        time.sleep(0.005)
    _stop(batcher, done, thread)
    assert sent and sent[0] - fed <= 0.06
