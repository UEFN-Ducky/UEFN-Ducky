"""Building an agent prompt asks UEFN nothing and carries one fixed UEFN line."""

from __future__ import annotations

import socket
import threading

import pytest

from backend.agent.prompt import UEFN_LINE


@pytest.fixture
def listener_requests(monkeypatch) -> list[object]:
    """Probes made by the thread that builds the prompt.

    Importing the MCP server can start the plugin pool's own threads; they are not
    the prompt build and are counted by their own owners' tests.
    """
    seen: list[object] = []
    me = threading.get_ident()

    def _note(kind: str, what: object) -> None:
        if threading.get_ident() == me:
            seen.append((kind, what))

    monkeypatch.setattr("backend.bridge.listener_get_health", lambda *a, **k: _note("health", a))
    monkeypatch.setattr(
        "backend.bridge.status.fetch_listener_status", lambda *a, **k: _note("status", a) or {}
    )
    monkeypatch.setattr("backend.mcp_plugins.epic.probe_epic_mcp", lambda *a, **k: _note("epic", a) or {})
    real_connect = socket.create_connection

    def _counting_connect(address, *a, **k):
        _note("connect", address)
        return real_connect(address, *a, **k)

    monkeypatch.setattr(socket, "create_connection", _counting_connect)
    return seen


def test_agent_prompt_build_makes_zero_listener_requests(listener_requests) -> None:
    from backend.agent.prompt import assemble_system_prompt, get_system_prompt_parts

    for online in (False, True):
        parts = get_system_prompt_parts(
            listener_online=online,
            listener_port=4200,
            project_root="",
            uefn_project_name="OtherIsland",
            project_match=False,
        )
        text = assemble_system_prompt(parts)
        assert UEFN_LINE in text
        for gone in ("Listener: offline", "Listener: online", "No global UEFN gate",
                     "Listener offline — file work", "another island than the selected project", "Epic UEFN MCP: offline"):
            assert gone not in text, gone
        assert parts["offline_rules"] == ""
    assert listener_requests == []


def test_coding_agent_bootstrap_makes_zero_listener_requests(listener_requests, monkeypatch) -> None:
    from backend.agent.coding_agents.mcp_inject import bootstrap_system_prompt

    monkeypatch.setattr("frontend.project_kind.is_folder_project", lambda root: False)
    text = bootstrap_system_prompt(project_root="/tmp/p", conv_id="chat-a")
    assert text.count(UEFN_LINE) == 1
    assert "offline" not in text.split("Your agent/chat id")[0].lower()
    assert listener_requests == []


def test_uefn_line_is_one_line() -> None:
    assert "\n" not in UEFN_LINE
    assert UEFN_LINE == (
        "UEFN tools need UEFN open with the island; everything else (files, code, git, "
        "terminal, web, any project) works now."
    )
