"""Integrated terminal sessions (PTY + WebSocket bridge)."""

from frontend.ui_web.terminal.manager import TerminalManager, get_terminal_manager

__all__ = ["TerminalManager", "get_terminal_manager"]
