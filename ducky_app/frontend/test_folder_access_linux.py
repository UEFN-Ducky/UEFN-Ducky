"""Linux side of folder_access (test_folder_access.py is Windows-only module-wide): no Defender
calls, and pickers through the XDG desktop portal over a fake session bus."""

from __future__ import annotations

import io
import os

import pytest

from frontend import folder_access as fa
from frontend import open_files

HANDLE = "/org/freedesktop/portal/desktop/request/1_42/t1"


class FakeMonitor:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


@pytest.fixture()
def portal(monkeypatch):
    state: dict = {"lines": [], "calls": [], "reply": f"(objectpath '{HANDLE}',)"}

    def monitor(dest, path=""):
        assert dest == fa.PORTAL
        state["monitor"] = FakeMonitor(state["lines"])
        return state["monitor"]

    monkeypatch.setattr(open_files, "gdbus_monitor", monitor)
    monkeypatch.setattr(open_files, "gdbus_call", lambda *a, **_k: state["calls"].append(a) or state["reply"])
    return state


def _response(code: int, uris: str, handle: str = HANDLE) -> str:
    return f"{handle}: org.freedesktop.portal.Request.Response (uint32 {code}, {{'uris': <[{uris}]>, 'choices': <@a(ss) []>}})"


def test_portal_folder_pick_gives_a_real_path(portal):
    portal["lines"] = [
        _response(0, "'file:///home/pat/Other'", handle="/org/freedesktop/portal/desktop/request/1_42/zz"),
        _response(0, "'file:///home/pat/Fortnite%20Projects/Island'"),
    ]
    assert fa.portal_pick("Add project") == "/home/pat/Fortnite Projects/Island"
    dest, path, method, parent, title, opts = portal["calls"][0]
    assert (dest, path, method) == (fa.PORTAL, fa.PORTAL_PATH, "org.freedesktop.portal.FileChooser.OpenFile")
    assert title == "'Add project'" and "'directory': <true>" in opts
    assert portal["monitor"].terminated


def test_portal_cancel_or_no_portal_is_none(portal, monkeypatch):
    portal["lines"] = [_response(1, "")]
    assert fa.portal_pick() is None
    portal["reply"] = None  # no portal service on the bus
    assert fa.portal_pick() is None
    monkeypatch.setattr(open_files, "gdbus_monitor", lambda *a, **_k: None)  # no gdbus
    assert fa.portal_pick() is None


@pytest.mark.skipif(os.name == "nt", reason="Linux/macOS: no Defender to ask")
def test_no_windows_calls_off_windows(monkeypatch):
    monkeypatch.setattr(fa.subprocess, "run", lambda *a, **k: pytest.fail("ran a Windows tool"))
    assert fa._blocks(120_000) == []
    assert fa.refusal_hint("/home/pat/x.txt") == ""
