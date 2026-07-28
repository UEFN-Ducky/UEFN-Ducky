"""Per-agent-run context (plan mode, etc.) for tools that need it."""

from __future__ import annotations

from contextvars import ContextVar

_plan_only: ContextVar[bool] = ContextVar("ducky_agent_plan_only", default=False)


def set_plan_only(value: bool) -> object:
    return _plan_only.set(bool(value))


def reset_plan_only(token: object) -> None:
    _plan_only.reset(token)  # type: ignore[arg-type]


def is_plan_only() -> bool:
    return bool(_plan_only.get())
