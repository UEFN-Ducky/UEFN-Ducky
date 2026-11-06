"""Archived chats stop working: their turns are stopped and their terminals closed.

Nov 5 2026: a team seat the person archived kept running its soak test in a sandbox terminal,
and the team keeper went on starting it on its open plan points. Archiving only moved the chat
to the Archive folder. Now it is a Stop too (``by_user``: nothing automatic starts it again until
someone sends it work), and every terminal it opened closes, running commands and all.
"""

from __future__ import annotations

import threading
from typing import Callable, Iterable


def _cancel(conv_id: str) -> None:
    from frontend.ui_web.agent_turns.state import cancel_agent

    cancel_agent(conv_id, by_user=True)


def _close_terminals(conv_ids: list[str]) -> list[str]:
    from frontend.ui_web.terminal.manager import get_terminal_manager

    return list(get_terminal_manager().close_chat_terminals(conv_ids, force=True))


def stop_archived(
    conv_ids: Iterable[str],
    *,
    cancel: Callable[[str], None] | None = None,
    close_terminals: Callable[[list[str]], list[str]] | None = None,
    wait: bool = False,
    timeout: float | None = None,
) -> None:
    """Stop these just-archived chats' turns and close their terminals. Runs off the caller's
    thread (a stop waits on the agent's process) unless ``wait``; never raises. ``wait`` with a
    ``timeout`` waits at most that long, then lets the stop finish on its own thread."""
    ids = sorted({str(c).strip() for c in conv_ids if str(c or "").strip()})
    if not ids:
        return
    cancel = cancel or _cancel
    close_terminals = close_terminals or _close_terminals

    def run() -> None:
        for conv_id in ids:
            try:
                cancel(conv_id)
            except Exception:
                pass
        try:
            close_terminals(ids)
        except Exception:
            pass

    if wait and timeout is None:
        run()
        return
    thread = threading.Thread(target=run, name="archive-stop", daemon=True)
    thread.start()
    if wait:
        thread.join(timeout)
