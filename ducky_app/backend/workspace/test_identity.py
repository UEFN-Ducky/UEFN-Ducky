"""RunContext binding, env round-trip, and visibility inside worker threads."""

from __future__ import annotations

import contextvars
import threading

import anyio

from backend.workspace import identity
from backend.workspace.identity import RunContext


def test_bind_reset_current() -> None:
    assert identity.current() is None
    token = identity.bind(RunContext(run_id="r1", conv_id="c1", ducky_name="Hacker"))
    try:
        assert identity.current() is not None
        assert identity.current().ducky_name == "Hacker"
    finally:
        identity.reset(token)
    assert identity.current() is None


def test_env_round_trip() -> None:
    ctx = RunContext(
        run_id="r1",
        conv_id="c1",
        profile_id="hacker",
        ducky_name="Hacker",
        model="anthropic:x",
        group_id="g1",
        leader_conv_id="l1",
        coding_agent="claude_code",
    )
    back = identity.from_env(ctx.to_env())
    assert back is not None
    assert back.run_id == "r1" and back.conv_id == "c1" and back.profile_id == "hacker"
    assert back.group_id == "g1" and back.leader_conv_id == "l1"
    assert back.coding_agent == "claude_code"


def test_from_env_is_none_without_identity() -> None:
    assert identity.from_env({}) is None
    assert identity.from_env({"PATH": "x"}) is None


def test_current_writer_falls_back_to_user(monkeypatch) -> None:
    for key in RunContext().to_env():
        monkeypatch.delenv(key, raising=False)
    writer = identity.current_writer(tool="panel_save")
    assert writer["source"] == identity.SOURCE_USER
    assert writer["tool"] == "panel_save"
    assert writer["run_id"] == ""


def test_current_writer_prefers_bound_context_over_env(monkeypatch) -> None:
    monkeypatch.setenv(identity.ENV_CONV_ID, "env-conv")
    token = identity.bind(RunContext(run_id="r", conv_id="bound-conv"))
    try:
        assert identity.current_writer()["conv_id"] == "bound-conv"
    finally:
        identity.reset(token)
    assert identity.current_writer()["conv_id"] == "env-conv"


def test_context_is_visible_inside_anyio_worker_thread() -> None:
    """FastMCP runs sync tools via anyio.to_thread; the ContextVar must survive that hop."""

    async def main() -> RunContext | None:
        token = identity.bind(RunContext(run_id="r1", conv_id="c1"))
        try:
            return await anyio.to_thread.run_sync(identity.current)
        finally:
            identity.reset(token)

    seen = anyio.run(main)
    assert seen is not None and seen.run_id == "r1"


def test_raw_thread_needs_copy_context() -> None:
    """Documents why bind happens on the agent thread: raw threads do not inherit."""
    token = identity.bind(RunContext(run_id="r1"))
    seen: list[RunContext | None] = []
    try:
        t = threading.Thread(target=lambda: seen.append(identity.current()))
        t.start()
        t.join()
        assert seen == [None]
        ctx = contextvars.copy_context()
        t2 = threading.Thread(target=lambda: seen.append(ctx.run(identity.current)))
        t2.start()
        t2.join()
        assert seen[-1] is not None and seen[-1].run_id == "r1"
    finally:
        identity.reset(token)
