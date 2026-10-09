"""Authoritative modes, legacy compatibility, and task/thread lifetime."""
import asyncio
import pytest
from backend.agent import run_context
from backend.agent.runner import RunConfig

@pytest.mark.parametrize('mode', ['agent', 'ask', 'plan'])
def test_explicit_mode_context_and_boolean_compatibility(mode):
    before = run_context.current_mode()
    token = run_context.set_mode(mode)
    try:
        assert run_context.current_mode() == mode
        assert run_context.is_plan_only() is (mode == 'plan')
        legacy = run_context.set_plan_only(True)
        try:
            assert run_context.current_mode() == 'plan'
        finally:
            run_context.reset_plan_only(legacy)
        assert run_context.current_mode() == mode
    finally:
        run_context.reset_mode(token)
    assert run_context.current_mode() == before

@pytest.mark.parametrize('mode', ['agent', 'ask'])
def test_explicit_mode_cannot_lift_legacy_plan_restriction(mode):
    with pytest.raises(ValueError):
        RunConfig(mode=mode, plan_only=True)

def test_ask_context_isolated_between_tasks_after_cancellation():
    async def exercise():
        entered = asyncio.Event()
        async def child():
            token = run_context.set_mode('ask')
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                run_context.reset_mode(token)
                assert run_context.current_mode() == 'agent'
        task = asyncio.create_task(child())
        await entered.wait()
        assert run_context.current_mode() == 'agent'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert callable(getattr(run_context, 'set_mode', None)), 'explicit Ask context is missing'
    asyncio.run(exercise())


@pytest.mark.parametrize("kwargs,expected", [({}, "agent"), ({"plan_only": True}, "plan"), ({"plan_only": False}, "agent"), ({"mode": "ask"}, "ask"), ({"mode": "plan"}, "plan")])
def test_config_defaults_and_explicit_mode(kwargs, expected):
    config = RunConfig(**kwargs)
    assert config.mode == expected
    assert config.plan_only is (expected == "plan")


@pytest.mark.parametrize("kwargs", [{"mode": "plan", "plan_only": False}, {"mode": "bad"}, {"mode": 42}])
def test_invalid_and_contradictory_config(kwargs):
    with pytest.raises(ValueError):
        RunConfig(**kwargs)


def test_concurrent_modes_to_thread_and_explicit_thread_context():
    import contextvars
    import threading
    async def exercise():
        async def worker(mode):
            token = run_context.set_mode(mode)
            try:
                await asyncio.sleep(0)
                assert run_context.current_mode() == mode
                assert await asyncio.to_thread(run_context.current_mode) == mode
                seen = []
                ctx = contextvars.copy_context()
                thread = threading.Thread(target=ctx.run, args=(lambda: seen.append(run_context.current_mode()),))
                thread.start()
                await asyncio.to_thread(thread.join, 2)
                assert not thread.is_alive() and seen == [mode]
            finally:
                run_context.reset_mode(token)
        await asyncio.gather(*(worker(m) for m in ("ask", "plan", "agent")))
        assert run_context.current_mode() == "agent"
    asyncio.run(exercise())


@pytest.mark.parametrize("exit_kind", ["close", "error", "cancel"])
def test_runner_restores_prior_ask_on_all_exits(monkeypatch, exit_kind):
    from backend.agent.runner import AgentRunner, AgentEvent
    async def inner(self, *a, **kw):
        assert run_context.current_mode() == "plan"
        assert await asyncio.to_thread(run_context.current_mode) == "plan"
        if exit_kind == "error":
            raise RuntimeError("synthetic")
        if exit_kind == "cancel":
            raise asyncio.CancelledError()
        yield AgentEvent(kind="text_delta", text="partial")
    monkeypatch.setattr(AgentRunner, "_run_turn_inner", inner)
    async def exercise():
        token = run_context.set_mode("ask")
        try:
            turn = AgentRunner(RunConfig(mode="plan")).run_turn("test", [])
            if exit_kind == "close":
                await anext(turn)
                await turn.aclose()
            else:
                with pytest.raises(RuntimeError if exit_kind == "error" else asyncio.CancelledError):
                    await anext(turn)
            assert run_context.current_mode() == "ask"
        finally:
            run_context.reset_mode(token)
    asyncio.run(exercise())


def test_ask_naming_exemption_is_narrow(monkeypatch):
    from backend.agent.chat_title import self_naming_instruction, require_self_name
    from frontend.settings import PanelSettings
    from types import SimpleNamespace
    monkeypatch.setattr(PanelSettings, "load", lambda: SimpleNamespace(chat_auto_title=True))
    monkeypatch.setattr("frontend.ui_web.project_chats.load_conversation", lambda _: SimpleNamespace(title="New ducky1"))
    for mode in ("agent", "ask", "plan", "ask"):
        token = run_context.set_mode(mode)
        try:
            instruction = self_naming_instruction("New ducky1", "test")
            if mode == "ask":
                assert instruction == ""
                require_self_name("workspace_read_file", "test")
            else:
                assert "FIRST tool call" in instruction
                with pytest.raises(ValueError, match="rename_self"):
                    require_self_name("workspace_read_file", "test")
        finally:
            run_context.reset_mode(token)


def test_runner_setup_error_restores_context(monkeypatch):
    from backend.agent.runner import AgentRunner
    def fail():
        raise RuntimeError("setup failed")
    monkeypatch.setattr("backend.tools.core.web_lookup.begin_web_turn", fail)
    async def exercise():
        token = run_context.set_mode("ask")
        try:
            with pytest.raises(RuntimeError, match="setup failed"):
                await anext(AgentRunner(RunConfig(mode="plan")).run_turn("test", []))
            assert run_context.current_mode() == "ask"
        finally:
            run_context.reset_mode(token)
    asyncio.run(exercise())
