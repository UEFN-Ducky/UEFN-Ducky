"""Panel calls into a plugin backend: registered methods run, unknown and disabled ones say why."""

from __future__ import annotations

import pytest

from backend.uefn_plugins.plugin_host import panel_rpc, state


@pytest.fixture
def rpc(monkeypatch):
    monkeypatch.setattr(panel_rpc, "is_plugin_enabled", lambda pid: pid == "rpc-probe")
    panel_rpc.register_panel_rpc("rpc-probe", "echo", lambda **kw: {"got": kw})
    yield
    with state._LOCK:
        state._PANEL_RPC.pop("rpc-probe", None)


def test_a_registered_method_gets_its_params(rpc) -> None:
    assert panel_rpc.call_panel_rpc("rpc-probe", "echo", {"a": 1}) == {"got": {"a": 1}}


def test_a_disabled_plugin_says_so(rpc) -> None:
    with pytest.raises(ValueError, match="disabled"):
        panel_rpc.call_panel_rpc("rpc-other", "echo")


def test_registering_needs_a_name_and_a_callable() -> None:
    with pytest.raises(ValueError):
        panel_rpc.register_panel_rpc("rpc-probe", "", lambda: None)


def test_panel_note_joins_dict_and_text_results(monkeypatch) -> None:
    import backend.uefn_plugins.panel_presence as presence

    monkeypatch.setattr(presence, "panel_note_once", lambda pid: "Open its panel.")
    assert panel_rpc._with_panel_note("p", {"ok": True}) == {"ok": True, "panel_note": "Open its panel."}
    assert panel_rpc._with_panel_note("p", "done") == "done\n\nOpen its panel."
    assert panel_rpc._with_panel_note("p", 3) == 3
