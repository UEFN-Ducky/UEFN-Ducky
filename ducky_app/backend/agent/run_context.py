"""Per-agent-run context (plan mode, etc.) for tools that need it."""

from __future__ import annotations

from contextvars import ContextVar

_mode: ContextVar[str] = ContextVar("ducky_agent_mode", default="agent")


def validate_mode(value: str) -> str:
    if not isinstance(value, str) or value not in ("agent", "ask", "plan"):
        raise ValueError("Invalid agent mode")
    return value


def current_mode() -> str:
    return _mode.get()


def set_mode(value: str) -> object:
    return _mode.set(validate_mode(value))


def reset_mode(token: object) -> None:
    _mode.reset(token)  # type: ignore[arg-type]


def set_plan_only(value: bool) -> object:
    return set_mode("plan" if value else "agent")


def reset_plan_only(token: object) -> None:
    reset_mode(token)


def is_plan_only() -> bool:
    return current_mode() == "plan"
