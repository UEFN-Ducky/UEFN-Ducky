"""Who is writing: one immutable ``RunContext`` per agent run.

Bound with a ``ContextVar`` (not ``threading.local``): FastMCP runs sync tools
through ``anyio.to_thread``, which copies context variables into the worker
thread. Precedent: ``backend.agent.hammer_guard``.

External coding agents (Claude Code, Codex, Cursor) run in their own process,
so their identity travels as ``DUCKY_*`` environment variables and is read back
with :func:`from_env`.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

SOURCE_AGENT = "agent"
SOURCE_USER = "user"
SOURCE_REVERT = "revert"

# Environment keys used to hand identity to external coding-agent processes.
ENV_RUN_ID = "DUCKY_RUN_ID"
ENV_CONV_ID = "DUCKY_CONV_ID"
ENV_PROFILE_ID = "DUCKY_PROFILE_ID"
ENV_DUCKY_NAME = "DUCKY_DUCKY_NAME"
ENV_MODEL = "DUCKY_MODEL"
ENV_GROUP_ID = "DUCKY_GROUP_ID"
ENV_LEADER_CONV_ID = "DUCKY_LEADER_CONV_ID"
ENV_CODING_AGENT = "DUCKY_CODING_AGENT"

WRITER_KEYS = (
    "source",
    "run_id",
    "conv_id",
    "profile_id",
    "ducky_name",
    "model",
    "group_id",
    "coding_agent",
    "tool",
)


@dataclass(frozen=True)
class RunContext:
    run_id: str = ""
    conv_id: str = ""
    profile_id: str = ""
    ducky_name: str = ""
    model: str = ""
    provider: str = ""
    coding_agent: str = "ducky"
    group_id: str = ""
    leader_conv_id: str = ""
    is_leader: bool = False
    # None = unrestricted; () = read-only member. Set by the lane resolver (M3).
    lane: tuple[str, ...] | None = None
    source: str = SOURCE_AGENT
    extra: Mapping[str, Any] = field(default_factory=dict)

    def with_lane(self, lane: tuple[str, ...] | None) -> RunContext:
        return replace(self, lane=lane)

    def as_writer(self, *, tool: str = "") -> dict[str, Any]:
        """Flat attribution dict stored on history entries and journal rows."""
        return {
            "source": self.source or SOURCE_AGENT,
            "run_id": self.run_id,
            "conv_id": self.conv_id,
            "profile_id": self.profile_id,
            "ducky_name": self.ducky_name,
            "model": self.model,
            "group_id": self.group_id,
            "coding_agent": self.coding_agent,
            "tool": tool,
        }

    def to_env(self) -> dict[str, str]:
        """Environment block for a spawned coding-agent process."""
        return {
            ENV_RUN_ID: self.run_id,
            ENV_CONV_ID: self.conv_id,
            ENV_PROFILE_ID: self.profile_id,
            ENV_DUCKY_NAME: self.ducky_name,
            ENV_MODEL: self.model,
            ENV_GROUP_ID: self.group_id,
            ENV_LEADER_CONV_ID: self.leader_conv_id,
            ENV_CODING_AGENT: self.coding_agent or "ducky",
        }


_ctx: ContextVar[RunContext | None] = ContextVar("ducky_run_context", default=None)


def bind(ctx: RunContext) -> Token:
    return _ctx.set(ctx)


def reset(token: Token) -> None:
    _ctx.reset(token)


def current() -> RunContext | None:
    """The context bound on this task/thread, or None."""
    return _ctx.get()


def from_env(environ: Mapping[str, str] | None = None) -> RunContext | None:
    """Identity handed to an external coding-agent process, or None when absent."""
    env = os.environ if environ is None else environ
    conv_id = (env.get(ENV_CONV_ID) or "").strip()
    run_id = (env.get(ENV_RUN_ID) or "").strip()
    if not conv_id and not run_id:
        return None
    return RunContext(
        run_id=run_id,
        conv_id=conv_id,
        profile_id=(env.get(ENV_PROFILE_ID) or "").strip(),
        ducky_name=(env.get(ENV_DUCKY_NAME) or "").strip(),
        model=(env.get(ENV_MODEL) or "").strip(),
        coding_agent=(env.get(ENV_CODING_AGENT) or "").strip() or "ducky",
        group_id=(env.get(ENV_GROUP_ID) or "").strip(),
        leader_conv_id=(env.get(ENV_LEADER_CONV_ID) or "").strip(),
    )


def resolve_context() -> RunContext | None:
    """Bound context first, then the process environment. None means a human."""
    return current() or from_env()


def user_writer(*, tool: str = "") -> dict[str, Any]:
    writer = {key: "" for key in WRITER_KEYS}
    writer["source"] = SOURCE_USER
    writer["tool"] = tool
    return writer


def current_writer(*, tool: str = "") -> dict[str, Any]:
    """Attribution for the caller: agent context, env identity, else the user."""
    ctx = resolve_context()
    if ctx is None:
        return user_writer(tool=tool)
    return ctx.as_writer(tool=tool)
