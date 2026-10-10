"""Public embedded Ask turns use evidence tools, without editor/provider IO."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from backend.agent import runner, run_context, tools
from backend.agent.providers.base import StreamEvent as E, StreamEventKind as K, ToolCallRequest as Call
from frontend.ui_web import agent_modes as am
from test_ducky_mode_dispatch import dispatch, QUALIFIED_CALLS, ASK_REFUSAL, qualified_call


def tool(name, annotations=None):
    return NS(name=name, description=name, inputSchema={"type": "object", "properties": {}}, annotations=annotations)


@pytest.fixture
def public(monkeypatch, tmp_path, unrestricted_tools):
    from frontend.settings import PanelSettings
    from frontend.ui_web import project_chats as pc
    from backend.uefn_plugins import host
    from backend.skills import store
    from backend.mcp_plugins import store as mcp_store
    from backend.bridge import status
    settings = PanelSettings.load()
    settings.uefn_project_root = str(tmp_path)
    settings.memory_auto_compress = False
    monkeypatch.setattr(PanelSettings, "load", lambda **kw: settings)
    conv = pc.create_conversation(settings, "test-model", project_root=str(tmp_path))
    conv.coding_agent = "ducky"
    conv.provider = "anthropic"
    conv.title = conv.ducky_name = "New ducky1"
    monkeypatch.setattr(am, "load_conversation", lambda _: conv)
    monkeypatch.setattr(pc, "load_conversation", lambda *a, **kw: conv)
    monkeypatch.setattr(am, "is_agent_running", lambda _: False)
    monkeypatch.setattr(am, "_note_run_starter", lambda *a: None)
    monkeypatch.setattr(am, "_make_broker_tap", lambda push, _: push)
    monkeypatch.setattr(am, "_backfill_video_frames", lambda *a: None)
    monkeypatch.setattr(am, "get_active_conv_id", lambda: None)
    monkeypatch.setattr(am, "apply_workspace_env", lambda *_: None)
    monkeypatch.setattr(am, "close_changeset_run", lambda *a, **kw: None)
    monkeypatch.setattr(am, "_push_token_usage", lambda *a, **kw: None)
    monkeypatch.setattr(am, "_log_agent_crash", lambda *a, **kw: None)
    monkeypatch.setattr(host, "resolve_gateway_credential", lambda _: "test-key")
    monkeypatch.setattr(host, "ensure_plugins_loaded", lambda: None)
    monkeypatch.setattr(store, "seed_skill_packs", lambda: None)
    monkeypatch.setattr(store, "build_skill_prompt", lambda *a: "")
    monkeypatch.setattr(mcp_store, "seed_mcp_plugins", lambda: None)
    monkeypatch.setattr("frontend.duckyos_account.fetch_agent_caps", lambda: {})
    monkeypatch.setattr(status, "fetch_listener_status", lambda *a, **kw: {"online": False})
    monkeypatch.setattr(runner, "get_system_prompt_parts", lambda **kw: {"mode_suffix": kw["mode_suffix"]})
    monkeypatch.setattr("backend.agent.prompt.get_system_prompt_parts", lambda **kw: {"mode_suffix": kw["mode_suffix"]})
    session = am.AgentSession()
    monkeypatch.setattr(am, "_session", lambda _: session)
    monkeypatch.setattr(session, "start", lambda target, _: target())
    catalog = [tool(n) for n in ("workspace_read_file", "web_search", "ducky_get_tools", "ducky_call_tool", "workspace_write_file", "ducky_create_plan", "ducky_rename_self")]
    seen, executed, events, thread_modes = [], [], [], []
    async def listing():
        return catalog
    monkeypatch.setattr(runner, "list_mcp_tools", listing)
    monkeypatch.setattr(tools, "list_mcp_tools", listing)
    async def execute(name, arguments=None, **kw):
        executed.append((name, arguments))
        return tools.ToolCallResult(ok=True, tool=name, data='{"evidence":"read result"}')
    monkeypatch.setattr(runner, "execute_tool", execute)
    def run(name="workspace_read_file", args=None, mode="ask", resume=False, attachments=None, failure="", calls=None):
        seen.clear(); executed.clear(); events.clear()
        if resume and not conv.messages:
            conv.messages = [{"role": "user", "content": "Inspect", "text": "Inspect"}, {"role": "assistant", "content": "Earlier"}]
        class Provider:
            async def stream_turn(self, **kw):
                seen.append(kw)
                thread_modes.append(run_context.current_mode())
                if failure:
                    yield E(kind=K.TEXT_DELTA, text="Partial evidence")
                    if failure == "error":
                        yield E(kind=K.ERROR, error="synthetic provider error")
                    else:
                        yield E(kind=K.DONE, stop_reason="end_turn")
                    return
                if len(seen) == 1:
                    yield E(kind=K.TOOL_CALLS, tool_calls=calls or [Call(id="read", name=name, arguments={} if args is None else args)])
                    yield E(kind=K.DONE, stop_reason="tool_use")
                else:
                    yield E(kind=K.TEXT_DELTA, text="Evidence answer")
                    yield E(kind=K.DONE, stop_reason="end_turn", usage={"input_tokens": 3, "output_tokens": 2})
        monkeypatch.setattr(runner, "make_provider", lambda *a, **kw: Provider())
        monkeypatch.setattr(am, "make_provider", lambda *a, **kw: Provider(), raising=False)
        def push(event):
            events.append(event)
            if failure == "cancel" and event["type"] == "text_delta":
                session._cancel.set()
        rid = am.run_message(conv.id, "Inspect evidence", mode, "test-model", push=push, resume=resume, attachments=attachments, _local=True)
        assert rid, events
        if not failure:
            assert not [e for e in events if e["type"] == "error"], str(events)
        return rid
    return NS(run=run, seen=seen, executed=executed, events=events, conv=conv, catalog=catalog, session=session, thread_modes=thread_modes)


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("name", ["workspace_read_file", "web_search", "ducky_get_tools"])
def test_public_ask_read_then_answer(public, name, resume):
    public.run(name, resume=resume)
    assert public.executed == [(name, {})], public.events
    assert len(public.seen) == 2
    assert public.seen[0]["tools"]
    assert "Do not call tools" not in public.seen[0]["system"]
    assert public.conv.messages[-1]["content"] == "Evidence answer"
    assert public.conv.messages[-1]["usage"]["output_tokens"] == 2
    assert any(e["type"] == "agent_stopped" and e["reason"] == "done" for e in public.events)
    assert public.thread_modes == ["ask", "ask"]
    assert run_context.current_mode() == "agent"


@pytest.mark.parametrize("name,args", [
    ("workspace_write_file", {}),
    ("ducky_create_plan", {}),
    ("ducky_rename_self", {}),
    ("ducky_call_tool", {"name": "workspace_write_file", "arguments": {}}),
    ("mcp__uefn__workspace_write_file", {}),
])
def test_public_ask_denial_is_normal_tool_result(public, name, args):
    public.run(name, args)
    assert public.executed == []
    assert len(public.seen) == 2, public.events
    assert "blocked in Ask mode" in str(public.seen[1]["messages"])
    assert public.conv.messages[-1]["content"] == "Evidence answer"


def test_public_mode_switch_drops_cached_mutators(public):
    for mode in ("agent", "ask", "plan", "ask"):
        public.run("ducky_create_plan", mode=mode)
        if mode == "ask":
            assert not public.executed
            assert "ducky_create_plan" not in str(public.seen[0]["tools"])
            assert "[Mode: Agent]" not in public.seen[0]["system"]
            assert "[Mode: Plan]" not in public.seen[0]["system"]
        else:
            assert public.executed


def test_public_attachments_and_resume_history(public):
    public.run(attachments=[{"kind": "file", "name": "evidence.txt", "text": "ATTACHED_EVIDENCE"}])
    assert "ATTACHED_EVIDENCE" in str(public.seen[0]["messages"])
    public.run(resume=True)
    assert "ATTACHED_EVIDENCE" in str(public.seen[0]["messages"])
    assert "Evidence answer" in str(public.seen[0]["messages"])
    assert len([m for m in public.conv.messages if m["role"] == "user"]) == 1


@pytest.mark.parametrize("failure", ["error", "cancel"])
def test_public_partial_error_cancel_checkpoint(public, failure):
    public.run(failure=failure)
    assert not public.executed
    assert any(e["type"] == "agent_stopped" and e["reason"] == ("error" if failure == "error" else "cancelled") for e in public.events)
    assert "Partial evidence" in str(public.conv.messages[-1])
    assert public.conv.messages[-1]["incomplete"]
    assert run_context.current_mode() == "agent"


def test_real_destructive_classifier_cannot_ask_for_approval(public, monkeypatch):
    from backend.agent.toolsets import is_destructive
    assert is_destructive("destroy_entity")
    public.catalog.append(tool("destroy_entity"))
    def forbidden(*a, **kw):
        raise AssertionError("Ask denial must precede destructive approval")
    monkeypatch.setattr(runner, "allow_destructive_execution", forbidden)
    public.run("destroy_entity")
    assert not public.executed and len(public.seen) == 2
    assert "blocked in Ask mode" in str(public.seen[1]["messages"])


@pytest.mark.parametrize("args", [[], {"name": "workspace_read_file", "arguments": []}, {"name": "missing", "arguments": {}}])
def test_public_malformed_dispatch_is_failed_tool_round(public, args):
    public.run("ducky_call_tool", args)
    assert not public.executed and len(public.seen) == 2
    assert "blocked in Ask mode" in str(public.seen[1]["messages"])


def test_public_cyclic_dispatch_is_not_echoed_to_provider_or_checkpoint(public):
    import json
    args = {"name": "ducky_call_tool"}
    args["arguments"] = args
    public.run("ducky_call_tool", args)
    assert not public.executed and len(public.seen) == 2
    assert "blocked in Ask mode" in str(public.seen[1]["messages"])
    json.dumps(public.conv.messages)


def test_public_placeholder_naming_rules_follow_mode_switch(public, monkeypatch):
    from backend.agent.chat_title import self_naming_instruction
    from frontend.settings import PanelSettings
    PanelSettings.load().chat_auto_title = True
    def parts(**kw):
        return {"mode_suffix": kw["mode_suffix"], "mcp": self_naming_instruction(kw["ducky_name"], kw["conv_id"])}
    monkeypatch.setattr(runner, "get_system_prompt_parts", parts)
    for mode in ("agent", "ask", "plan", "ask"):
        public.run(mode=mode)
        assert ("ducky_rename_self" in public.seen[0]["system"]) == (mode != "ask")
        assert public.executed == [("workspace_read_file", {})]


def test_duplicate_provider_ids_do_not_replace_denial(public, monkeypatch):
    public.catalog.append(tool("destroy_entity"))
    def forbidden(*a, **kw):
        raise AssertionError("duplicate IDs must not reach destructive approval")
    monkeypatch.setattr(runner, "allow_destructive_execution", forbidden)
    public.run(calls=[Call(id="same", name="destroy_entity", arguments={}), Call(id="same", name="workspace_read_file", arguments={})])
    assert public.executed == [("workspace_read_file", {})]
    assert "blocked in Ask mode" in str(public.seen[1]["messages"])


@pytest.mark.parametrize("name,args,route", QUALIFIED_CALLS)
def test_public_qualified_mutator_refusal_followup(public, dispatch, monkeypatch, name, args, route):
    import json
    dispatch.catalog[name] = tool(name, {"readOnlyHint": True, "destructiveHint": False})
    async def listing():
        return list(dispatch.catalog.values())
    monkeypatch.setattr(runner, "list_mcp_tools", listing)
    monkeypatch.setattr(runner, "execute_tool", tools.execute_tool)
    monkeypatch.setattr(runner, "allow_destructive_execution", dispatch.forbidden)
    monkeypatch.setattr("backend.agent.coding_agents.plans.plan_mutator_block_reason", dispatch.forbidden)
    monkeypatch.setattr("backend.agent.chat_title.require_self_name", dispatch.forbidden)
    monkeypatch.setattr("backend.tools.verse.skill_tool.seed_skill_packs", dispatch.forbidden)
    monkeypatch.setattr("backend.agent.hammer_guard.note_failure", dispatch.forbidden)
    monkeypatch.setattr(tools, "_record_tool_failure", dispatch.forbidden)
    public.run(*qualified_call(name, args, route))
    assert dispatch.reached == []
    assert len(public.seen) == 2
    assert ASK_REFUSAL in str(public.seen[1]["messages"])
    assert public.conv.messages[-1]["content"] == "Evidence answer"
    assert not any(e["type"] == "approval_needed" for e in public.events)
    json.dumps(public.conv.messages, allow_nan=False)


@pytest.mark.parametrize("name", ["docs__read_page", "mcp__uefn__workspace_read_file"])
@pytest.mark.parametrize("route", ["direct", "nested"])
def test_public_literal_qualified_read_transport_followup(public, dispatch, monkeypatch, name, route):
    dispatch.catalog[name] = tool(name, {"readOnlyHint": True, "destructiveHint": False})
    async def listing():
        return list(dispatch.catalog.values())
    monkeypatch.setattr(runner, "list_mcp_tools", listing)
    monkeypatch.setattr(runner, "execute_tool", tools.execute_tool)
    public.run(*qualified_call(name, {"path": "mock"}, route))
    assert dispatch.reached == [(name, {"path": "mock"})]
    assert len(public.seen) == 2
    assert "evidence" in str(public.seen[1]["messages"])
    assert public.conv.messages[-1]["content"] == "Evidence answer"
