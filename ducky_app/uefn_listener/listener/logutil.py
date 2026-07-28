"""Output log + in-memory ring."""

import unreal

from listener import error_file
from listener.config import LOG_RING_SIZE
from listener.state import log_ring


def log_msg(msg: str, level: str = "info") -> None:
    entry = f"[MCP] {msg}"
    log_ring.append(entry)
    if len(log_ring) > LOG_RING_SIZE:
        log_ring.pop(0)
    if level == "error":
        unreal.log_error(entry)
        # Persist to appdata so the control panel's Errors window can show history.
        error_file.record_error("editor", msg)
    elif level == "warning":
        unreal.log_warning(entry)
    else:
        unreal.log(entry)
