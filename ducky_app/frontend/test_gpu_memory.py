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
    monkeypatch.setattr(gpu_memory, "_table", lambda: {})
    monkeypatch.setattr(gpu_memory, "_names", lambda procs: {20: "UnrealEditorFortnite", 9001: "msedgewebview2",
                                                            9002: "msedgewebview2"})
    # 9002 is Ducky's own WebView2; 9001 belongs to another app and never counts as Ducky.
    monkeypatch.setattr(gpu_memory, "_mine", lambda procs: {9002})
    monkeypatch.setattr("frontend.notifications.alert", lambda *a, **k: state["alerts"].append((a, k)) or True)
    monkeypatch.setattr(low_memory, "_gpu_warned", False)
    monkeypatch.setattr(low_memory, "_gpu_calm_since", 0.0)
    monkeypatch.setattr(low_memory, "_gpu_last_alert", 0.0)
    monkeypatch.setattr(low_memory, "warnings_on", lambda: True)
    clock = [1000.0]
    monkeypatch.setattr(low_memory.time, "monotonic", lambda: clock[0])
    state["clock"] = clock
    return state


def _check(gpu, used_gb: float, after_s: float = 30.0) -> None:
    gpu["clock"][0] += after_s
    gpu["used"] = int(used_gb * GB)
    low_memory.check_gpu()


def test_read_names_the_card_and_what_fills_it(gpu):
    info = gpu_memory.read()
    assert info["total_gb"] == 8.0 and info["used_gb"] == 7.6 and info["share"] == 0.95
    assert info["ducky_gb"] == 0.1
    assert info["top"] == [{"name": "UnrealEditorFortnite", "gb": 7.1}, {"name": "msedgewebview2", "gb": 0.3}]
    assert gpu_memory.full(info)


def test_95_percent_gives_a_finding_naming_uefn(gpu):
    snap = {"app": {}, "pc": {"cpus": 8}, "gpu": gpu_memory.read()}
    found = [f for f in self_health.findings(snap) if f["what"] == gpu_memory.TITLE]
    assert len(found) == 1 and found[0]["severity"] == "high"
    assert found[0]["fix"] == ("UEFN 7.1 of 8 GB dedicated (Ducky 0.1 GB). "
                               "Lower Engine Scalability or unload regions.")


def test_describe_is_short_and_names_dedicated_and_ducky():
    base = {"total_gb": 8.0, "used_gb": 7.6, "ducky_gb": 0.1}
    for top in ({"name": "UnrealEditorFortnite-Win64-Shipping", "gb": 6.3}, {"name": "x" * 200, "gb": 5.0}):
        title, body = gpu_memory.describe({**base, "top": [top]})
        assert title == "UEFN is nearly out of graphics memory"
        assert len(body) < 160 and "dedicated" in body and "Ducky" in body
    assert gpu_memory.describe({**base, "top": [{"name": "UnrealEditorFortnite", "gb": 6.3}]})[1] == (
        "UEFN 6.3 of 8 GB dedicated (Ducky 0.1 GB). Lower Engine Scalability or unload regions.")


def test_one_calm_alert_per_spell_naming_uefn(gpu):
    _check(gpu, 7.6)
    _check(gpu, 7.6)
    assert len(gpu["alerts"]) == 1
    (title, status), extra = gpu["alerts"][0]
    assert title == gpu_memory.TITLE and status == "Graphics memory is almost full."
    assert extra["level"] != "error"
    assert extra["detail"].startswith("UEFN 7.1 of 8 GB dedicated (Ducky 0.1 GB).")
    _check(gpu, 7.1)  # 0.89: not a new spell
    _check(gpu, 7.4)  # 0.92
    assert len(gpu["alerts"]) == 1
    _check(gpu, 4.0)  # under 0.80, but not for 5 minutes yet
    _check(gpu, 7.6, after_s=60)
    assert len(gpu["alerts"]) == 1
    _check(gpu, 4.0)
    _check(gpu, 4.0, after_s=low_memory.GPU_CALM_S)  # the spell ended
    _check(gpu, 7.6)  # but the last alert was under 30 minutes ago
    assert len(gpu["alerts"]) == 1
    _check(gpu, 4.0, after_s=low_memory.GPU_EVERY_S)
    _check(gpu, 4.0, after_s=low_memory.GPU_CALM_S)
    _check(gpu, 7.6)
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
