"""Accept-queue and light-command policy (no Unreal)."""

from __future__ import annotations

import sys
from pathlib import Path

_LISTENER_ROOT = Path(__file__).resolve().parents[1]
if str(_LISTENER_ROOT) not in sys.path:
    sys.path.insert(0, str(_LISTENER_ROOT))

from listener.accept_queue import (  # noqa: E402
    ACCEPT_QUEUE_MAX,
    busy_payload_dict,
    can_accept_queued_command,
    is_light_command,
)


def test_light_commands_whitelist() -> None:
    assert is_light_command("ping")
    assert is_light_command("status")
    assert is_light_command("describe_commands")
    assert is_light_command("get_log")
    assert is_light_command("poll_screenshot_capture")
    assert not is_light_command("spawn_actor")
    assert not is_light_command("reload_listener")
    assert not is_light_command("")


def test_can_accept_up_to_max_queue() -> None:
    assert can_accept_queued_command(queue_size=0)
    assert can_accept_queued_command(queue_size=ACCEPT_QUEUE_MAX - 1)
    assert not can_accept_queued_command(queue_size=ACCEPT_QUEUE_MAX)
    assert not can_accept_queued_command(queue_size=ACCEPT_QUEUE_MAX + 2)


def test_busy_payload_mentions_not_offline() -> None:
    body = busy_payload_dict(queue_size=4)
    assert body["success"] is False
    assert "offline" in body["error"].lower()
    assert body["queue_size"] == 4
    assert body["max_queue"] == ACCEPT_QUEUE_MAX
