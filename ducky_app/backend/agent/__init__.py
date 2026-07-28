"""Embedded UEFN chat agent — tool loop, providers, secrets."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agent.runner import AgentRunner, RunConfig

__all__ = ["AgentRunner", "RunConfig"]


def __getattr__(name: str):
    if name == "AgentRunner":
        from backend.agent.runner import AgentRunner

        return AgentRunner
    if name == "RunConfig":
        from backend.agent.runner import RunConfig

        return RunConfig
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
