"""Cross-process bridge lock serialization."""

from __future__ import annotations

import threading
import time

from backend.bridge_lock import CrossProcessLock


def test_cross_process_lock_serializes_threads() -> None:
    lock = CrossProcessLock(name="UEFNDuckyTestBridgeLock")
    order: list[int] = []
    barrier = threading.Barrier(2)

    def worker(n: int) -> None:
        barrier.wait(timeout=5.0)
        with lock:
            order.append(n)
            time.sleep(0.05)
            order.append(n)

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)
    assert not t1.is_alive() and not t2.is_alive()
    # Fully nested pairs — no interleaving of 1 and 2 mid-critical-section.
    assert order in ([1, 1, 2, 2], [2, 2, 1, 1])


def test_cross_process_lock_reentrant() -> None:
    lock = CrossProcessLock(name="UEFNDuckyTestBridgeLockRe")
    with lock:
        with lock:
            pass
