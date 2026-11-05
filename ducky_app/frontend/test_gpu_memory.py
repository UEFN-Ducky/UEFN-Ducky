"""Graphics memory is watched too (Nov 5 2026: UEFN died of 'Out of video memory' on an 8 GB card)."""

from __future__ import annotations

import pytest

from frontend import gpu_memory, low_memory, self_health

GB = 1024**3
LUID = "luid_0x00000000_0x0000f129"


@pytest.fixture
def gpu(monkeypatch):
    """A fake 8 GB card at 95%: UEFN 7.1 GB, WebView2 0.3 GB."""
    state = {"fail": False, "used": int(7.6 * GB), "alerts": []}

    def adapter():
        if state["fail"]:
            raise OSError("no DXGI")
        return {"name": "NVIDIA GeForce RTX 3070 Ti", "dedicated": 8 * GB, "budget": int(7.4 * GB), "luid": LUID}

    monkeypatch.setattr(gpu_memory, "_adapter", adapter)
    monkeypatch.setattr(gpu_memory, "_counters", lambda luid: (state["used"], {20: int(7.1 * GB), 9001: int(0.2 * GB),
                                                                             9002: int(0.1 * GB)}))
    monkeypatch.setattr(gpu_memory, "_names", lambda procs=None: {20: "UnrealEditorFortnite", 9001: "msedgewebview2",
                                                                  9002: "msedgewebview2"})
    monkeypatch.setattr("frontend.notifications.alert", lambda *a, **k: state["alerts"].append((a, k)) or True)
    monkeypatch.setattr(low_memory, "_gpu_warned", False)
    return state


def test_read_names_the_card_and_what_fills_it(gpu):
    info = gpu_memory.read()
    assert info["total_gb"] == 8.0 and info["used_gb"] == 7.6 and info["share"] == 0.95
    assert info["top"] == [{"name": "UnrealEditorFortnite", "gb": 7.1}, {"name": "msedgewebview2", "gb": 0.3}]
    assert gpu_memory.full(info)


def test_95_percent_gives_a_finding_naming_uefn(gpu):
    snap = {"app": {}, "pc": {"cpus": 8}, "gpu": gpu_memory.read()}
    found = [f for f in self_health.findings(snap) if f["what"].startswith("Graphics memory")]
    assert len(found) == 1 and found[0]["severity"] == "high"
    assert found[0]["what"] == "Graphics memory 7.6 / 8.0 GB: UnrealEditorFortnite 7.1 GB, msedgewebview2 0.3 GB."
    assert found[0]["fix"].startswith("UEFN uses 7.1 of 8 GB graphics memory: unload World Partition regions")


def test_one_alert_per_spell_naming_uefn(gpu):
    low_memory.check_gpu()
    low_memory.check_gpu()
    assert len(gpu["alerts"]) == 1
    (_, status), extra = gpu["alerts"][0]
    assert status == "Graphics memory is almost full."
    assert "UEFN uses 7.1 of 8 GB graphics memory" in extra["detail"] and "Engine Scalability" in extra["detail"]
    gpu["used"] = 4 * GB  # the spell ends
    low_memory.check_gpu()
    gpu["used"] = int(7.6 * GB)
    low_memory.check_gpu()
    assert len(gpu["alerts"]) == 2


def test_a_reader_failure_gives_nothing(gpu):
    gpu["fail"] = True
    assert gpu_memory.read() is None
    low_memory.check_gpu()
    assert gpu["alerts"] == []
    snap = {"app": {}, "pc": {"cpus": 8}, "gpu": gpu_memory.read()}
    assert not [f for f in self_health.findings(snap) if "Graphics memory" in f["what"]]


def test_a_card_with_room_gives_nothing(gpu):
    gpu["used"] = 3 * GB
    assert not gpu_memory.full(gpu_memory.read())
    low_memory.check_gpu()
    assert gpu["alerts"] == []
