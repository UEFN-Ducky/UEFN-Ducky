import asyncio

from backend.agent.runner import _post_to_loop


def test_post_to_closed_loop_is_ignored():
    loop = asyncio.new_event_loop()
    queue = asyncio.Queue()
    assert _post_to_loop(loop, queue, "event") is True
    loop.close()
    assert _post_to_loop(loop, queue, "late") is False  # was RuntimeError: Event loop is closed
