"""Gateways a plugin registers: LLM providers, coding agents, IDE hookups."""

from __future__ import annotations

import pytest

from backend.uefn_plugins.plugin_host import gateways, state


@pytest.fixture
def provider():
    gateways.register_llm_provider_factory(
        "gw-probe", "probe", lambda **k: None,
        model_ids=["probe-1"], model_aliases={"probe-old": "probe-1"}, billing="account",
    )
    yield "probe"
    with state._LOCK:
        state._LLM_PROVIDER_FACTORIES.pop("probe", None)


def test_provider_options_and_model_lookup(provider) -> None:
    assert gateways.provider_option(provider, "billing") == "account"
    assert gateways.provider_for_model("PROBE-1") == provider
    assert gateways.provider_for_model("probe-old") == provider
    assert gateways.current_model_id(provider, "probe-old") == "probe-1"
    assert gateways.get_llm_provider_registration(provider)["plugin_id"] == "gw-probe"


def test_a_provider_its_plugin_hides_has_no_registration() -> None:
    gateways.register_llm_provider_factory("gw-probe", "ghost", lambda **k: None, visible=lambda: False)
    try:
        assert gateways.provider_visible("ghost") is False
        assert gateways.get_llm_provider_registration("ghost") is None
    finally:
        with state._LOCK:
            state._LLM_PROVIDER_FACTORIES.pop("ghost", None)


def test_coding_agent_ids_are_normalized() -> None:
    gateways.register_coding_agent_factory("gw-probe", "Probe-Agent", lambda **k: None, aliases=["P-A"])
    try:
        row = gateways.get_coding_agent_registration("probe_agent")
        assert row["aliases"] == ["p_a"]
        assert "probe_agent" in gateways.registered_coding_agent_ids()
    finally:
        with state._LOCK:
            state._CODING_AGENT_FACTORIES.pop("probe_agent", None)


def test_ide_hookup_hides_while_its_plugin_is_off(monkeypatch) -> None:
    gateways.register_ide_hookup_factory("gw-probe", "probe-ide", label="Probe IDE")
    try:
        monkeypatch.setattr(gateways, "is_plugin_enabled", lambda pid: False)
        assert gateways.get_ide_hookup("probe_ide") is None
        monkeypatch.setattr(gateways, "is_plugin_enabled", lambda pid: True)
        assert gateways.get_ide_hookup("probe_ide")["label"] == "Probe IDE"
        with pytest.raises(ValueError):
            gateways.register_ide_hookup_factory("gw-probe", "bad kind!")
    finally:
        with state._LOCK:
            state._IDE_HOOKUPS.pop("probe_ide", None)
