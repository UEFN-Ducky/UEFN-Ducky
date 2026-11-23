"""tools/list off Windows lists UEFN-only tools only when some UEFN can run them."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

import backend.agent.tools as agent_tools
from backend.bridge import editor_presence, plugin_gate

NAMES = ["workspace_read_file", "ducky_launch_uefn", "web_search", "uefn_popups", "spawn_actor", "unreal__call_tool"]
UEFN_ONLY = {"uefn_popups", "spawn_actor", "unreal__call_tool"}
_NO_ROUTE = plugin_gate._no_uefn_route  # conftest turns it off for the other bridge tests


@pytest.fixture(autouse=True)
def _route_filter_on(monkeypatch):
    monkeypatch.setattr(plugin_gate, "_no_uefn_route", _NO_ROUTE)


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(agent_tools, "_panel_tool", lambda name: False)
    where = {"route": "none"}
    monkeypatch.setattr(editor_presence, "route", lambda: where["route"])
    return where


def _listed() -> list[str]:
    return [t.name for t in plugin_gate.lean_tool_list([SimpleNamespace(name=n) for n in NAMES])]


@pytest.mark.parametrize("route", ["local", "proton", "remote"])
def test_a_route_lists_every_tool(linux, route) -> None:
    linux["route"] = route
    assert _listed() == NAMES
    assert plugin_gate.no_uefn_route_error("spawn_actor") == ""


def test_no_route_leaves_out_uefn_only_tools(linux) -> None:
    assert _listed() == [n for n in NAMES if n not in UEFN_ONLY]
    assert "uefn_popups" not in _listed()


def test_a_direct_call_with_no_route_is_ok_false_naming_both_fixes(linux) -> None:
    out = json.loads(plugin_gate.no_uefn_route_error("uefn_popups"))
    assert out["ok"] is False and out["reason"] == "no_uefn_route"
    assert "Proton" in out["error"] and "Windows PC" in out["error"]
    assert plugin_gate.no_uefn_route_error("workspace_read_file") == ""


def test_windows_lists_everything_and_never_asks_the_route(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(editor_presence, "route", lambda: pytest.fail("Windows never asks"))
    assert _listed() == NAMES
    assert plugin_gate.no_uefn_route_error("uefn_popups") == ""


def test_route_order_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(editor_presence, "uefn_running", lambda: False)
    monkeypatch.setattr(editor_presence, "proton_uefn", lambda: True)
    monkeypatch.setattr(editor_presence, "linked_pc", lambda: pytest.fail("Proton comes first"))
    assert editor_presence.route() == "proton"
    monkeypatch.setattr(editor_presence, "uefn_running", lambda: True)
    assert editor_presence.route() == "local"
