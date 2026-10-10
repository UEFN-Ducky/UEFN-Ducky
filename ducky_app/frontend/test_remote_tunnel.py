"""Remote tunnel status + cloudflared origin flags."""

from __future__ import annotations

import time
from pathlib import Path

from frontend import remote_tunnel as rt


def test_quick_tunnel_reuses_origin_keepalive():
    src = Path(rt.__file__).read_text(encoding="utf-8")
    assert "--proxy-keepalive-connections" in src
    assert '"8"' in src or "\n                        \"8\"," in src
    assert "--no-chunked-encoding" in src
    assert "named_reason" in src


def test_append_cloudflared_log_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(rt, "_LOG_MAX", 40)
    path = rt._cloudflared_log_path()
    rt._append_cloudflared_log("a" * 50)
    rt._append_cloudflared_log("b" * 20)
    data = path.read_text(encoding="utf-8")
    assert "bbbbbbbbbbbbbbbbbbbb" in data
    assert "aaaaaaaaaa" not in data


def test_named_cache_roundtrip(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr("backend.agent.secrets.get_key", lambda key: store.get(key))
    monkeypatch.setattr("backend.agent.secrets.set_key", lambda key, value: store.__setitem__(key, value))
    assert rt._load_named_cache() == {}
    rt._save_named_cache("u-abc.uefnducky.org", "tok")
    assert rt._load_named_cache() == {
        "hostname": "u-abc.uefnducky.org",
        "token": "tok",
        "mode": "named",
    }


def test_app_exit_stops_cloudflared(monkeypatch):
    """Windows does not end a child with its parent. Every exit with Remote access
    on left cloudflared running, keeping the tunnel pointed at a dead panel."""
    import subprocess
    import sys
    import threading

    from frontend import boot_report
    from frontend.ui_web import shutdown

    spawned: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(rt.subprocess, "Popen", popen)
    rt._STOP.clear()
    tunnel = threading.Thread(
        target=rt._run_cloudflared,
        args=(Path(sys.executable), ["-c", "import time; time.sleep(60)"]),
        daemon=True,
    )
    tunnel.start()
    try:
        deadline = time.monotonic() + 10
        while not spawned and time.monotonic() < deadline:
            time.sleep(0.02)
        assert spawned, "fake cloudflared never started"
        for name in (
            "_stop_all_agents", "_stop_cpu_workers", "_stop_all_terminals", "_stop_mcp_plugins",
            "release_panel_process", "_kill_sibling_processes", "_spawn_kill_uefn_processes",
            "_terminate_process",
        ):
            monkeypatch.setattr(shutdown, name, lambda *a, **k: None)
        monkeypatch.setattr(boot_report, "mark", lambda *a, **k: None)
        monkeypatch.setattr(shutdown, "_exit_started", False)
        shutdown.hard_exit()
        try:
            spawned[0].wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        assert spawned[0].poll() is not None, "cloudflared outlived the app"
    finally:
        rt._STOP.set()
        for proc in spawned:
            if proc.poll() is None:
                proc.kill()
        tunnel.join(timeout=5)
        rt._set_status(running=False, mode="", hostname="", error="")


def test_a_failing_tunnel_backs_off_and_sweeps_orphans_once(monkeypatch):
    """cloudflared dying right after start was retried every 3 s forever, each time
    with a PowerShell sweep for orphans, a site call and a new cloudflared."""
    import io
    from types import SimpleNamespace

    from frontend.settings import PanelSettings

    waits: list[float] = []

    class FakeStop:
        flag = False

        def is_set(self) -> bool:
            return self.flag

        def set(self) -> None:
            self.flag = True

        def clear(self) -> None:
            self.flag = False

        def wait(self, timeout: float | None = None) -> bool:
            waits.append(float(timeout or 0))
            self.flag = len(waits) >= 8
            return self.flag

    class ExitsAtOnce:
        def __init__(self, *args, **kwargs) -> None:
            self.stdout = io.StringIO("")

        def poll(self) -> int:
            return 1

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 1

    sweeps: list[int] = []
    monkeypatch.setattr(rt, "_STOP", FakeStop())
    monkeypatch.setattr(rt.subprocess, "Popen", ExitsAtOnce)
    monkeypatch.setattr(rt, "ensure_cloudflared", lambda: Path("cloudflared.exe"))
    monkeypatch.setattr(rt, "_kill_orphan_cloudflareds", lambda: sweeps.append(1))
    monkeypatch.setattr(rt, "_fetch_tunnel_token", lambda: {"mode": "named", "token": "t", "hostname": "h.test"})
    monkeypatch.setattr(rt, "_save_named_cache", lambda *args: None)
    monkeypatch.setattr(rt, "_append_cloudflared_log", lambda text: None)
    monkeypatch.setattr(PanelSettings, "load", lambda *args, **kwargs: SimpleNamespace(remote_access=True))
    try:
        rt._loop()
    finally:
        rt._set_status(running=False, mode="", hostname="", error="", named_reason="")
    assert sweeps == [1]
    assert waits == [3.0, 6.0, 12.0, 24.0, 48.0, 60.0, 60.0, 60.0]


def test_named_reason_survives_quick_status():
    rt._set_status(named_reason="cloudflare 403: zone", mode="quick", running=True, error="")
    try:
        st = rt.remote_tunnel_status()
        assert st["named_reason"] == "cloudflare 403: zone"
        rt._set_status(hostname="x.trycloudflare.com", running=True)
        assert rt.remote_tunnel_status()["named_reason"] == "cloudflare 403: zone"
        rt._set_status(named_reason="", mode="named")
        assert rt.remote_tunnel_status()["named_reason"] == ""
    finally:
        rt._set_status(
            running=False,
            mode="",
            hostname="",
            error="",
            named_reason="",
            site_update_pending=False,
        )
