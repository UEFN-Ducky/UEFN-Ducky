"""Schedule work onto the UE game thread (next tick)."""

from typing import Any, Callable

from listener.state import main_queue


def run_on_main_thread(fn: Callable[[], Any]) -> None:
    main_queue.put(fn)
