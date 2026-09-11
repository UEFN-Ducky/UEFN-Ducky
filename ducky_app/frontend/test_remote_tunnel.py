"""Remote tunnel status + cloudflared origin flags."""

from __future__ import annotations

from pathlib import Path

from frontend import remote_tunnel as rt


def test_quick_tunnel_reuses_origin_keepalive():
    src = Path(rt.__file__).read_text(encoding="utf-8")
    assert "--proxy-keepalive-connections" not in src
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
