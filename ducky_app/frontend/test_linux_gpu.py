"""Linux graphics memory: NVIDIA through NVML, AMD through sysfs, anything else unknown; a full
card turns light drawing on (pc_draw) with the same FULL_SHARE / ROOM_SHARE as Windows."""

from __future__ import annotations

import ctypes

import pytest

from frontend import busy_pc, gpu_memory, low_memory
from frontend.ui_web import webview_memory

GB = 1024**3


def _card(root, name: str, total: int, used: int) -> None:
    device = root / name / "device"
    device.mkdir(parents=True)
    (device / "mem_info_vram_total").write_text(f"{total}\n")
    (device / "mem_info_vram_used").write_text(f"{used}\n")


def test_amd_sysfs_picks_the_card_with_most_vram(tmp_path):
    _card(tmp_path, "card0", 512 * 1024**2, 100 * 1024**2)  # an iGPU carve-out
    _card(tmp_path, "card1", 8 * GB, int(7.6 * GB))
    (tmp_path / "card2-HDMI-A-1").mkdir()  # a connector, no device memory
    got = gpu_memory._amd(str(tmp_path))
    assert got["total"] == 8 * GB and got["used"] == int(7.6 * GB) and got["name"] == "AMD card1"
    assert gpu_memory._amd(str(tmp_path / "none")) is None


class _FakeNvml:
    """libnvidia-ml with two cards: a 4 GB one and a 12 GB one where UEFN holds 9 GB."""

    def __init__(self, init_rc: int = 0) -> None:
        self.init_rc, self.shut = init_rc, 0
        self.cards = [(4 * GB, 1 * GB, b"GTX small", []), (12 * GB, int(11.5 * GB), b"RTX 4070", [(4242, 9 * GB)])]

    def nvmlInit_v2(self):
        return self.init_rc

    def nvmlShutdown(self):
        self.shut += 1
        return 0

    def nvmlDeviceGetCount_v2(self, count):
        count.contents.value = len(self.cards)
        return 0

    def nvmlDeviceGetHandleByIndex_v2(self, i, handle):
        handle.contents.value = i + 1
        return 0

    def _of(self, handle):
        return self.cards[int(handle.value) - 1]

    def nvmlDeviceGetMemoryInfo(self, handle, mem):
        total, used, _, _ = self._of(handle)
        mem.contents.total, mem.contents.used, mem.contents.free = total, used, total - used
        return 0

    def nvmlDeviceGetName(self, handle, buf, size):
        ctypes.memmove(buf, self._of(handle)[2], len(self._of(handle)[2]))
        return 0

    def nvmlDeviceGetGraphicsRunningProcesses(self, handle, count, rows):
        procs = self._of(handle)[3]
        for i, (pid, used) in enumerate(procs):
            rows[i].pid, rows[i].usedGpuMemory = pid, used
        count.contents.value = len(procs)
        return 0

    def nvmlDeviceGetComputeRunningProcesses(self, handle, count, rows):
        count.contents.value = 0
        return 0


def test_nvml_reads_the_biggest_card_and_its_programs():
    lib = _FakeNvml()
    got = gpu_memory._nvml(lib)
    assert got == {"name": "RTX 4070", "total": 12 * GB, "used": int(11.5 * GB), "per_pid": {4242: 9 * GB}}
    assert lib.shut == 1
    assert gpu_memory._nvml(_FakeNvml(init_rc=9)) is None  # driver not loaded


def _linux(monkeypatch, card):
    monkeypatch.setattr(gpu_memory.sys, "platform", "linux")
    monkeypatch.setattr(gpu_memory, "_linux_card", lambda: card)
    monkeypatch.setattr(gpu_memory, "_table", lambda: {})


def test_read_on_linux_has_the_windows_shape(monkeypatch):
    _linux(monkeypatch, {"name": "AMD card1", "total": 8 * GB, "used": int(7.6 * GB), "per_pid": {}})
    info = gpu_memory.read()
    assert info["share"] == 0.95 and info["total_gb"] == 8.0 and info["used_gb"] == 7.6 and info["top"] == []
    assert gpu_memory.full(info)
    title, body = gpu_memory.describe(info)
    assert body.startswith("Graphics 7.6 of 8 GB dedicated")


def test_an_unknown_card_never_sets_light(monkeypatch):
    _linux(monkeypatch, None)
    assert gpu_memory.read() is None
    monkeypatch.setattr(busy_pc, "light", False)
    monkeypatch.delenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", raising=False)
    monkeypatch.setattr(busy_pc, "_tell_windows", lambda events: pytest.fail("told the window"))
    assert busy_pc.light_draw(None) is False


def test_amd_card_95_percent_full_draws_light(monkeypatch, tmp_path):
    _linux(monkeypatch, {"name": "AMD card1", "total": 8 * GB, "used": int(7.6 * GB), "per_pid": {}})
    told: list = []
    monkeypatch.setattr(busy_pc, "light", False)
    monkeypatch.delenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", raising=False)
    monkeypatch.setattr(busy_pc, "_tell_windows", told.extend)
    monkeypatch.setattr(busy_pc, "note_path", lambda: tmp_path / "gpu.json")
    monkeypatch.setattr(busy_pc, "raise_window_priority", lambda: 0)
    monkeypatch.setattr(low_memory, "_gpu_warned", True)  # the alert is test_low_memory's
    low_memory.check_gpu()
    busy_pc.on_heartbeat({"free_gb": 6.2})
    assert told[-1] == {"type": busy_pc.DRAW_EVENT, "light": True}
    # Light stays on above ROOM_SHARE and ends under it, as on Windows.
    assert busy_pc.light_draw(0.85) is True
    assert busy_pc.light_draw(busy_pc.ROOM_SHARE - 0.01) is False


def test_webview_memory_is_a_no_op_on_linux(monkeypatch):
    monkeypatch.setattr(webview_memory.sys, "platform", "linux")
    assert not webview_memory.enabled()
    assert webview_memory.trim_now(object()) is False
    assert webview_memory.trim_if_bloated(object(), {"webview_gb": 3.0}) is False
