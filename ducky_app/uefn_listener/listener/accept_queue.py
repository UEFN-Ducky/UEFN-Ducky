"""Accept-queue policy for the UEFN HTTP listener (pure, no Unreal import)."""

from __future__ import annotations

# Pure-Python / file-only commands — safe to run on the HTTP worker thread so
# they never occupy the editor in-flight slot or steal a heavy command's turn.
LIGHT_COMMANDS = frozenset(
    {
        "ping",
        "status",
        "describe_commands",
        "get_log",
        "poll_screenshot_capture",
    }
)

# Max queued editor commands awaiting the Slate tick (not counting in-flight).
ACCEPT_QUEUE_MAX = 4


def is_light_command(command: str) -> bool:
    return (command or "").strip() in LIGHT_COMMANDS


def can_accept_queued_command(
    *,
    queue_size: int,
    max_queue: int = ACCEPT_QUEUE_MAX,
) -> bool:
    """True when a non-light command may be enqueued for the editor tick."""
    return int(queue_size) < int(max_queue)


def busy_payload_dict(*, queue_size: int, max_queue: int = ACCEPT_QUEUE_MAX) -> dict:
    return {
        "success": False,
        "error": (
            "Listener queue full — another editor command is still running or queued. "
            "Wait for the previous MCP tool to finish; do NOT assume the listener is offline "
            "(Verse compile / save / PIE can stall the editor tick)."
        ),
        "queue_size": int(queue_size),
        "max_queue": int(max_queue),
    }
