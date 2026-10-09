"""Opening Ducky closes a stuck copy that still holds the panel port."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from frontend import frozen_process


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only reclaim")
def test_only_a_stuck_ducky_on_the_panel_port_is_closed(monkeypatch) -> None:
    import psutil

    names = {101: "UEFN-Ducky.exe", 102: "UEFN-Ducky-Setup-1.2.360.exe", 103: "python.exe", 104: "UEFN-Ducky.exe"}
    conns = [
        SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=4199), pid=101),
        SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=4199), pid=102),
        SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=4199), pid=103),
        SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=5000), pid=104),
    ]
    monkeypatch.setattr(psutil, "net_connections", lambda kind="tcp": conns)
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(name=lambda: names[pid]))
    killed: list[int] = []
    monkeypatch.setattr(frozen_process, "_kill_pid_tree", lambda pid: killed.append(pid) or True)

    assert frozen_process.reclaim_panel_port(4199) is True
    assert killed == [101]  # never Setup, never a dev server, never another port
