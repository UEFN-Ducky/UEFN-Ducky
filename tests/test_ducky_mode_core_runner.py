"""Mode propagation without launching a CLI or contacting the live app."""

from types import SimpleNamespace

import pytest

from backend.agent.coding_agents import base, runner


class ModeAdapter:
    label = "Test adapter"
    capabilities = base.CodingAgentCapabilities(
        resume=True, supported_modes=("ask", "plan", "agent")
    )

    def __init__(self):
        self.calls = []
        self.reported_mode = None

    def detect(self, settings):
        return SimpleNamespace(available=True)

    def launch(self, *, mode="agent", **kwargs):
        self.calls.append({"mode": mode, **kwargs})
        return base.CodingAgentLaunchResult(
            ok=True, reply_text="Finished", upstream_session_id="session-1",
            effective_mode=mode if self.reported_mode is None else self.reported_mode,
        )


class LegacyAdapter(ModeAdapter):
    capabilities = SimpleNamespace(resume=True)

    def launch(self, **kwargs):
        assert "mode" not in kwargs
        self.calls.append(kwargs)
        return base.CodingAgentLaunchResult(ok=True, reply_text="Finished")


@pytest.fixture
def harness(monkeypatch, tmp_path):
    from backend import bridge
    from backend.bridge import status
    from backend.uefn_plugins import host
    from backend.workspace import ai_ignore
    from frontend.settings import PanelSettings
    from frontend.ui_web import agent_modes, project_chats, workspace_bootstrap
    from backend.agent.coding_agents import mcp_inject

    settings = PanelSettings.load()
    settings.uefn_project_root = str(tmp_path)
    monkeypatch.setattr(PanelSettings, "load", lambda: settings)
    monkeypatch.setattr(ai_ignore, "current_policy", lambda: SimpleNamespace(strict=False))
    monkeypatch.setattr(runner, "apply_workspace_env", lambda *_: None)
    monkeypatch.setattr(runner, "normalize_coding_agent", lambda _: "test_adapter")
    monkeypatch.setattr(runner, "coding_agent_cfg", lambda *_: {"enabled": True})
    monkeypatch.setattr(runner, "_normalize_launch_model", lambda *args: "test-model")
    monkeypatch.setattr(runner, "_thinking_env", lambda *_: {})
    monkeypatch.setattr(runner, "record_coding_agent_usage", lambda *a, **kw: None)
    monkeypatch.setattr(bridge, "set_port_override", lambda *_: None)
    monkeypatch.setattr(status, "fetch_listener_status", lambda *a, **kw: {"online": False})
    monkeypatch.setattr(host, "get_coding_agent_registration", lambda *_: {})
    monkeypatch.setattr(mcp_inject, "deployed_skill_packs", lambda *_: ("", []))
    monkeypatch.setattr(runner, "bootstrap_system_prompt", lambda **kw: "Test rules")
    monkeypatch.setattr(runner, "collect_image_paths", lambda *_: [])
    monkeypatch.setattr(runner, "launch_env", lambda **kw: dict(kw["extra"]))
    monkeypatch.setattr(workspace_bootstrap, "build_run_context", lambda *a, **kw: SimpleNamespace(as_writer=lambda: {}))
    monkeypatch.setattr(workspace_bootstrap, "record_external_edits", lambda *a: None)
    monkeypatch.setattr(agent_modes, "close_changeset_run", lambda *a: None)

    def temp_file(name):
        path = tmp_path / name
        path.write_text("test", encoding="utf-8")
        return path

    monkeypatch.setattr(runner, "write_uefn_mcp_config", lambda **kw: temp_file("mcp.json"))
    monkeypatch.setattr(runner, "write_prompt_file", lambda *a, **kw: temp_file("prompt.txt"))
    conv = project_chats.create_conversation(settings, "test-model", project_root=str(tmp_path))
    conv.coding_agent = "test_adapter"
    conv.messages = [{"role": "user", "content": "Inspect", "text": "Inspect", "ts": 1}]
    project_chats.save_conversation(conv, str(tmp_path))
    adapter = ModeAdapter()
    monkeypatch.setattr(runner, "get_adapter", lambda _: adapter)
    events = []

    def run(**kwargs):
        return runner.run_coding_agent_message(
            conv, "Inspect", model="test-model", push=events.append, **kwargs
        )

    return SimpleNamespace(conv=conv, adapter=adapter, events=events, run=run, root=tmp_path)


@pytest.mark.parametrize("mode", ["ask", "plan", "agent"])
@pytest.mark.parametrize("session", ["", "test_adapter:existing"])
def test_fresh_and_resume_propagate_and_persist(harness, mode, session):
    from frontend.ui_web.project_chats import load_conversation

    harness.conv.upstream_session_id = session
    outcome = harness.run(mode=mode)
    call = harness.adapter.calls[-1]
    assert call["mode"] == mode
    assert call["session_id"] == ("existing" if session else "")
    assert call["env"]["DUCKY_REQUESTED_MODE"] == mode
    assert outcome["ok"]
    assert outcome["requested_mode"] == outcome["effective_mode"] == mode
    saved = load_conversation(harness.conv.id, project_root=str(harness.root))
    assert saved.messages[-1]["requested_mode"] == mode
    assert saved.messages[-1]["effective_mode"] == mode
    stopped = [e for e in harness.events if e["type"] == "agent_stopped"][-1]
    assert stopped["effective_mode"] == mode
    assert not (harness.root / "mcp.json").exists()


def test_mode_switch_uses_current_request_on_same_session(harness):
    for mode in ("agent", "plan", "ask", "agent"):
        assert harness.run(mode=mode)["ok"]
    assert [c["mode"] for c in harness.adapter.calls] == ["agent", "plan", "ask", "agent"]
    assert [c["session_id"] for c in harness.adapter.calls] == ["", "session-1", "session-1", "session-1"]


def test_default_agent_mode(harness):
    assert harness.run()["effective_mode"] == "agent"
    assert harness.adapter.calls[-1]["mode"] == "agent"


def test_legacy_agent_default_remains_compatible(harness, monkeypatch):
    adapter = LegacyAdapter()
    monkeypatch.setattr(runner, "get_adapter", lambda _: adapter)
    result = harness.run()
    assert result["ok"] and result["effective_mode"] == "agent"
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("mode", ["ask", "plan"])
def test_legacy_restricted_mode_rejected_before_launch(harness, monkeypatch, mode):
    adapter = LegacyAdapter()
    monkeypatch.setattr(runner, "get_adapter", lambda _: adapter)
    outcome = harness.run(mode=mode)
    assert not outcome["ok"]
    assert "does not support" in outcome["error"]
    assert outcome["requested_mode"] == mode and outcome["effective_mode"] == ""
    assert adapter.calls == []
    assert not (harness.root / "mcp.json").exists()
    assert harness.events[-1]["type"] == "agent_stopped"


@pytest.mark.parametrize("mode", ["invalid", "read-only", 42])
def test_invalid_mode_never_launches(harness, mode):
    result = harness.run(mode=mode)
    assert not result["ok"] and "Invalid" in result["error"]
    assert harness.adapter.calls == []


def test_kwargs_only_adapter_cannot_claim_restricted_mode(harness, monkeypatch):
    adapter = LegacyAdapter()
    adapter.capabilities = ModeAdapter.capabilities
    monkeypatch.setattr(runner, "get_adapter", lambda _: adapter)
    result = harness.run(mode="plan")
    assert not result["ok"] and "mode-aware" in result["error"]
    assert adapter.calls == []


@pytest.mark.parametrize("effective", ["", "agent", "invalid"])
def test_missing_or_mismatched_effective_mode_is_not_success(harness, effective):
    harness.adapter.reported_mode = effective
    result = harness.run(mode="plan")
    assert not result["ok"] and "did not confirm" in result["error"]
    assert result["requested_mode"] == "plan"
    assert result["effective_mode"] == effective
    assert len(harness.adapter.calls) == 1  # Never retry a potentially mutating run.


def test_launch_result_serializes_mode_metadata():
    result = base.CodingAgentLaunchResult(ok=True, requested_mode="plan", effective_mode="plan")
    assert result.to_dict()["requested_mode"] == "plan"
    assert result.to_dict()["effective_mode"] == "plan"


@pytest.mark.parametrize("mode", ["ask", "plan", "agent"])
@pytest.mark.parametrize("resume", [False, True])
def test_ui_forwards_mode_to_external_runner(harness, monkeypatch, mode, resume):
    from frontend.ui_web import agent_modes as am

    calls = []
    monkeypatch.setattr(base, "normalize_coding_agent", lambda _: "test_adapter")
    monkeypatch.setattr(am, "load_conversation", lambda _: harness.conv)
    monkeypatch.setattr(am, "save_conversation", lambda *a, **kw: None)
    monkeypatch.setattr(am, "append_message", lambda conv, msg: conv.messages.append(msg))
    monkeypatch.setattr(am, "is_agent_running", lambda _: False)
    monkeypatch.setattr(am, "_note_run_starter", lambda *a: None)
    monkeypatch.setattr(am, "_make_broker_tap", lambda push, _: push)
    monkeypatch.setattr(am, "_backfill_video_frames", lambda *a: None)
    monkeypatch.setattr(am, "get_active_conv_id", lambda: None)
    monkeypatch.setattr(am, "apply_workspace_env", lambda *_: None)
    session = am.AgentSession()
    monkeypatch.setattr(am, "_session", lambda _: session)
    monkeypatch.setattr(session, "start", lambda target, _: target())
    monkeypatch.setattr(runner, "run_coding_agent_message", lambda *a, **kw: calls.append(kw))
    rid = am.run_message(harness.conv.id, "Inspect", mode, "test-model", push=harness.events.append, resume=resume, _local=True)
    assert rid
    assert len(calls) == 1, harness.events
    assert calls[0]["mode"] == mode
