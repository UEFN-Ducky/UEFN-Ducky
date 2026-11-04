"""Sensitive plugin tools reach only the models the person allowed."""

from __future__ import annotations

import pytest

from backend.uefn_plugins import host, model_gate
from backend.workspace import identity
from backend.workspace.identity import RunContext


class _T:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture
def gmail_plugin(tmp_path, monkeypatch):
    """A loaded 'gmailtest' plugin: gmail_send is sensitive, gmail_ping is not."""
    monkeypatch.setattr("backend.skills.discovery.appdata_dir", lambda: tmp_path)
    monkeypatch.setattr(host, "ensure_plugins_loaded", lambda timeout=None: True)
    monkeypatch.setattr(host, "is_plugin_enabled", lambda pid: pid == "gmailtest")
    monkeypatch.setattr(host, "_plugin_label", lambda pid: "Gmail Test")
    monkeypatch.setitem(host._CONTRIBUTIONS["agent_tools"], "gmailtest", {
        "category": "gmailtest",
        "tools": ["gmail_ping", "gmail_send"],
        "sensitive": ["gmail_send"],
    })
    for name in ("gmail_ping", "gmail_send"):
        monkeypatch.setitem(host._PLUGIN_TOOL_OWNER, name, "gmailtest")
    host.set_active_uefn_agent_plugin_ids(None)
    yield
    host.set_active_uefn_agent_plugin_ids(None)


def _as_model(model: str):
    return identity.bind(RunContext(conv_id="c1", run_id="r1", model=model))


def _listed() -> list[str]:
    return [t.name for t in host.filter_uefn_plugin_tools([_T("gmail_ping"), _T("gmail_send"), _T("spawn_actor")])]


def test_default_empty_list_hides_sensitive_tools(gmail_plugin):
    token = _as_model("claude-opus-5-5")
    try:
        assert _listed() == ["gmail_ping", "spawn_actor"]
        assert "Allowed models" in host.sensitive_tool_block_reason("gmail_send")
    finally:
        identity.reset(token)


def test_model_not_allowed_gets_no_sensitive_tools_and_call_refused(gmail_plugin):
    model_gate.set_allowed_models("gmailtest", ["anthropic/claude-opus-5-5"])
    token = _as_model("ollama/llama3")
    try:
        assert _listed() == ["gmail_ping", "spawn_actor"]
        reason = host.sensitive_tool_block_reason("gmail_send")
        assert "gmail_send" in reason and "ollama/llama3" in reason
        assert "Settings → Gmail Test → Allowed models" in reason
        assert host.sensitive_tool_block_reason("gmail_ping") == ""
    finally:
        identity.reset(token)


def test_allowed_model_gets_sensitive_tools(gmail_plugin):
    model_gate.set_allowed_models("gmailtest", ["anthropic/claude-opus-5-5"])
    token = _as_model("claude-opus-5-5")
    try:
        assert _listed() == ["gmail_ping", "gmail_send", "spawn_actor"]
        assert host.sensitive_tool_block_reason("gmail_send") == ""
    finally:
        identity.reset(token)


def test_person_without_a_run_is_never_gated(gmail_plugin, monkeypatch):
    monkeypatch.delenv("UEFN_DUCKY_MCP_BRIDGE", raising=False)
    monkeypatch.delenv(identity.ENV_CONV_ID, raising=False)
    monkeypatch.delenv(identity.ENV_RUN_ID, raising=False)
    assert host.sensitive_tool_block_reason("gmail_send") == ""


def test_unidentified_mcp_client_call_refused_but_catalog_kept(gmail_plugin, monkeypatch):
    monkeypatch.delenv(identity.ENV_CONV_ID, raising=False)
    monkeypatch.delenv(identity.ENV_RUN_ID, raising=False)
    shared = identity.mark_shared()
    try:
        assert "unknown" in host.sensitive_tool_block_reason("gmail_send")
        assert host.sensitive_tool_block_reason("gmail_send", listing=True) == ""
    finally:
        identity.reset_shared(shared)


def test_sensitive_true_covers_every_tool(gmail_plugin, monkeypatch):
    monkeypatch.setitem(host._CONTRIBUTIONS["agent_tools"]["gmailtest"], "sensitive", True)
    token = _as_model("ollama/llama3")
    try:
        assert _listed() == ["spawn_actor"]
    finally:
        identity.reset(token)


def test_api_tool_call_refused_with_clear_message(gmail_plugin):
    api = host._PluginApi("gmailtest")

    @api.tool(name="gmail_gate_probe_send", listener=False)
    def gmail_gate_probe_send() -> str:
        return "sent"

    host._CONTRIBUTIONS["agent_tools"]["gmailtest"]["sensitive"] = True
    from backend.server import mcp

    fn = mcp._tool_manager.get_tool("gmail_gate_probe_send").fn
    token = _as_model("ollama/llama3")
    try:
        with pytest.raises(ValueError, match="not on Gmail Test's Allowed models"):
            fn()
    finally:
        identity.reset(token)
    model_gate.set_allowed_models("gmailtest", ["ollama/llama3"])
    token = _as_model("ollama/llama3")
    try:
        assert fn() == "sent"
    finally:
        identity.reset(token)
        host._unregister_plugin_mcp_tools({"gmail_gate_probe_send"})
        host._PLUGIN_TOOL_OWNER.pop("gmail_gate_probe_send", None)
        host._API_TOOL_REGISTRY.pop("gmailtest", None)


def test_manifest_sensitive_is_parsed(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "_CONTRIBUTIONS", {k: (type(v)() if isinstance(v, (dict, list)) else v) for k, v in host._CONTRIBUTIONS.items()})
    monkeypatch.setattr(host, "_PLUGIN_TOOL_OWNER", {})
    monkeypatch.setattr(host, "_TOOL_LAST_OWNER", {})
    monkeypatch.setattr(host, "_PLUGIN_TOOL_NAMES", set())
    monkeypatch.setattr(host, "_PLUGIN_INTENT_ROWS", [])
    manifest = {"id": "gmailparse", "contributes": {"agent.tools": {
        "tools": ["gp_read", "gp_send"], "sensitive": ["gp_send", "not_a_tool_of_it"],
    }}}
    host._load_one("gmailparse", tmp_path, manifest, register=False)
    assert host._CONTRIBUTIONS["agent_tools"]["gmailparse"]["sensitive"] == ["gp_send", "not_a_tool_of_it"]
    assert host.sensitive_tool_plugin("gp_send") == "gmailparse"
    assert host.sensitive_tool_plugin("gp_read") == ""
