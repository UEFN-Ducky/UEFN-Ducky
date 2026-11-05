"""uefn_memory_growth_limit_gb is settable by agents and the guard reads it on every heavy call."""

from __future__ import annotations

import json

import pytest

from backend.bridge import editor_gateway
from frontend.settings import PanelSettings

GB = 1024 ** 3


@pytest.fixture
def uefn(monkeypatch: pytest.MonkeyPatch, tmp_path) -> dict:
    """A fake UEFN process (pid, start, private bytes) and 32 GB free on the PC."""
    state = {"proc": (100, 5.0, 21 * GB)}
    monkeypatch.setattr(editor_gateway, "_uefn_memory", lambda: state["proc"])
    monkeypatch.setattr(editor_gateway, "_pc_free_bytes", lambda: 32 * GB)
    monkeypatch.setattr(editor_gateway, "_pc_total_bytes", lambda: 64 * GB)
    monkeypatch.setattr(editor_gateway, "_baseline_path", lambda: tmp_path / "uefn_memory_baseline.json")
    monkeypatch.setattr(editor_gateway, "_lease_path", lambda: tmp_path / "editor_lease.json")
    import frontend.duckyos_account as account

    monkeypatch.setattr(account, "effective_allow_settings_write", lambda value: True)
    return state


def _set(patch: dict) -> dict:
    from backend.tools.panel.panel_settings import ducky_settings_set

    return json.loads(ducky_settings_set(patch))


def test_schema_lists_the_limit_as_a_settable_agent_float() -> None:
    from frontend.settings_schema import settings_schema

    entry = settings_schema()["fields"]["uefn_memory_growth_limit_gb"]
    assert entry["type"] == "float"
    assert entry["default"] == 4.0
    assert entry["settable"] is True
    assert entry["tab"] == "Agent"
    assert (entry["min"], entry["max"]) == (0.1, 64.0)


@pytest.mark.parametrize("backend", ["db", "files"])
def test_lowering_the_limit_refuses_the_next_heavy_call(uefn, monkeypatch, backend) -> None:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", backend)
    PanelSettings().save()
    assert editor_gateway.call("", "compile_verse", True, lambda: "built") == "built"  # baseline 21 GB
    uefn["proc"] = (100, 5.0, int(21.3 * GB))
    assert editor_gateway.call("", "compile_verse", True, lambda: "built") == "built"  # 0.3 GB < 4 GB

    out = _set({"uefn_memory_growth_limit_gb": 0.1})
    assert "error" not in out, out
    assert out["after"] == {"uefn_memory_growth_limit_gb": 0.1}

    ran: list[str] = []
    with pytest.raises(
        editor_gateway.EditorMemoryLow,
        match=r"UEFN grew 0\.3 GB since the island loaded \(limit 0\.1 GB, RAM 32\.0 GB of 64 GB free\): save and restart UEFN",
    ):
        editor_gateway.call("", "compile_verse", True, lambda: ran.append("x"))
    assert ran == []
    assert editor_gateway.uefn_growth() == {"grown_gb": 0.3, "limit_gb": 0.1}

    assert "error" not in _set({"uefn_memory_growth_limit_gb": 8})
    assert editor_gateway.call("", "compile_verse", True, lambda: "built") == "built"


@pytest.mark.parametrize("value", [0.0, 0.05, 65, "lots"])
def test_out_of_range_or_bad_values_are_refused(uefn, value) -> None:
    out = _set({"uefn_memory_growth_limit_gb": value})
    assert "error" in out


def test_a_stored_value_out_of_range_is_clamped_on_validate() -> None:
    settings = PanelSettings(uefn_memory_growth_limit_gb=500.0)
    settings.validate()
    assert settings.uefn_memory_growth_limit_gb == 64.0
