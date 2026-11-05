"""admission — every turn starts at once; low memory posts one warning and holds nothing."""

from __future__ import annotations

import pytest

from frontend.ui_web import admission as adm


@pytest.fixture
def pc(monkeypatch):
    free = {"gb": 3.0}
    roles: dict[str, str] = {}
    monkeypatch.setattr(adm, "_running", set())
    monkeypatch.setattr(adm, "_warned", {"low": False})
    monkeypatch.setattr(adm, "min_free_gb", lambda: 6.0)
    monkeypatch.setattr(adm, "_read_free", lambda: free["gb"])
    monkeypatch.setattr(adm, "_role", lambda cid: roles.get(cid, "member"))
    monkeypatch.setattr(adm, "biggest_users", lambda n=5: [{"name": "UnrealEditorFortnite", "gb": 12.0},
                                                           {"name": "UEFN Ducky + agents", "gb": 3.0}])
    monkeypatch.setattr(adm, "_total_gb", lambda: 16.0)
    monkeypatch.setattr("frontend.low_memory._uefn_growth", lambda: None)
    return {"free": free, "roles": roles}


def test_ten_members_under_the_floor_all_start_and_one_warning_is_posted(pc):
    pushed: list[dict] = []
    assert all(adm.may_start(f"m{i}", "agent", push=pushed.append) for i in range(10))
    assert all(adm.holds(f"m{i}") for i in range(10))
    assert len(pushed) == 1
    warning = pushed[0]
    assert warning["type"] == "low_memory_warning"
    assert "Waiting" not in warning["text"]
    assert warning["text"].startswith("Low RAM: 3.0 GB of 16 GB free. Biggest users: "
                                      "UnrealEditorFortnite 12 GB, UEFN Ducky + agents 3 GB.")
    assert warning["top"] == [{"name": "UnrealEditorFortnite", "gb": 12.0}, {"name": "UEFN Ducky + agents", "gb": 3.0}]
    assert adm.snapshot()["waiting"] == []


def test_a_group_members_turn_starts_at_once_under_the_floor(pc):
    pushed: list[dict] = []
    pc["free"]["gb"] = 0.5
    assert adm.may_start("member", "agent", start=lambda: pytest.fail("never started later"), push=pushed.append)
    assert adm.holds("member") and len(pushed) == 1


def test_enough_memory_posts_nothing_and_a_new_low_spell_warns_again(pc):
    pushed: list[dict] = []
    pc["free"]["gb"] = 20.0
    assert adm.may_start("a", "agent", push=pushed.append)
    assert pushed == []
    pc["free"]["gb"] = 2.0
    adm.may_start("b", "agent", push=pushed.append)
    adm.may_start("c", "agent", push=pushed.append)
    assert len(pushed) == 1
    pc["free"]["gb"] = 20.0  # recovered
    adm.may_start("d", "agent", push=pushed.append)
    pc["free"]["gb"] = 2.0  # low again: one more warning
    adm.may_start("e", "agent", push=pushed.append)
    assert len(pushed) == 2


def test_people_top_leads_and_solo_chats_take_no_slot(pc):
    pc["roles"].update({"lead": "top_lead", "solo": "solo", "sub": "sub_lead"})
    assert adm.may_start("m", "person") and not adm.holds("m")
    assert adm.may_start("lead", "agent") and not adm.holds("lead")
    assert adm.may_start("solo", "agent") and not adm.holds("solo")
    assert adm.may_start("sub", "agent") and adm.holds("sub")
    adm.release("sub")
    assert not adm.holds("sub")


def test_free_memory_reads_something():
    assert adm.free_memory_gb() > 0


@pytest.mark.parametrize("total, floor", [(8, 1.5), (16, 2.4), (32, 4.8), (64, 6.0), (97, 6.0), (4, 1.5)])
def test_the_default_floor_scales_with_the_pcs_ram(total, floor):
    assert adm.default_min_free_gb(total) == floor


def test_the_setting_and_env_still_set_the_warning_line(monkeypatch):
    # The soak harness's --min-free-gb sets the env var: it now only moves the warning line.
    from frontend.settings import PanelSettings
    from frontend.ui_web import seat_queue

    clock = {"t": 1000.0}
    monkeypatch.setattr(adm, "_now", lambda: clock["t"])
    monkeypatch.setattr(adm, "_floor_cache", {"gb": 0.0, "at": float("-inf")})
    monkeypatch.setattr(seat_queue, "total_memory_gb", lambda: 16.0)
    monkeypatch.delenv("UEFN_DUCKY_AGENT_MIN_FREE_RAM_GB", raising=False)
    settings = type("S", (), {"agent_min_free_ram_gb": None})()
    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls: settings))
    assert adm.min_free_gb() == 2.4  # the 16 GB default
    settings.agent_min_free_ram_gb = 3.0
    clock["t"] += adm.FLOOR_EVERY_S
    assert adm.min_free_gb() == 3.0
    monkeypatch.setenv("UEFN_DUCKY_AGENT_MIN_FREE_RAM_GB", "1")
    clock["t"] += adm.FLOOR_EVERY_S
    assert adm.min_free_gb() == 1.0
