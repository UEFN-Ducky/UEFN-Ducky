"""Beta updates: which feed the updater reads, and switching back to stable never downgrades."""

from __future__ import annotations

import io

import pytest

from frontend import app_stage, platform_info, version_check
from frontend.settings import PanelSettings

BASE = "https://store.example"
STABLE_URL = f"{BASE}/api/v1/plugins/uefn-ducky-store/collect/app-version"


@pytest.fixture
def sent(monkeypatch):
    """Run fetch_remote_payload offline; returns the requests it sent."""
    reqs = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        reqs.append(req)
        return _Resp(b'{"currentVersion": "9.9.9"}')

    monkeypatch.setattr(version_check, "update_base_url", lambda: BASE)
    monkeypatch.setattr(version_check.urllib.request, "urlopen", fake_urlopen)
    return reqs


def _use(monkeypatch, *, linux: bool, saved: str = "") -> None:
    monkeypatch.setattr(platform_info, "IS_LINUX", linux)
    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls, *a, **k: PanelSettings(update_channel=saved)))


def _shape(req):
    return (req.full_url, req.get_method(), req.data, sorted(req.header_items()))


def test_windows_default_request_is_byte_identical(monkeypatch, sent):
    _use(monkeypatch, linux=False)
    payload, err = version_check.fetch_remote_payload(attempts=1)
    assert err is None and payload == {"currentVersion": "9.9.9"}
    assert _shape(sent[0]) == (
        STABLE_URL,
        "POST",
        b"{}",
        sorted([
            ("Accept", "application/json"),
            ("Content-type", "application/json"),
            ("Origin", BASE),
            ("User-agent", f"UEFN-Ducky/{version_check.__version__}"),
        ]),
    )


def test_linux_default_reads_beta(monkeypatch, sent):
    _use(monkeypatch, linux=True)
    version_check.fetch_remote_payload(attempts=1)
    assert sent[0].full_url == STABLE_URL + "?channel=beta"


def test_beta_setting_adds_channel_on_windows(monkeypatch, sent):
    _use(monkeypatch, linux=False, saved="beta")
    version_check.fetch_remote_payload(attempts=1)
    assert sent[0].full_url == STABLE_URL + "?channel=beta"


def test_stable_setting_on_linux_reads_stable(monkeypatch, sent):
    _use(monkeypatch, linux=True, saved="stable")
    version_check.fetch_remote_payload(attempts=1)
    assert sent[0].full_url == STABLE_URL


def test_bad_saved_value_falls_back_to_default():
    s = PanelSettings(update_channel="nightly")
    s.validate()
    assert s.update_channel == ""


def test_switching_back_to_stable_never_downgrades():
    # On beta 2.0.1; the stable feed still says 2.0.0: no update, Setup or pack.
    assert not version_check.is_remote_newer("2.0.1", "2.0.0")
    stable = {"update": {"version": "2.0.0", "packUrl": "p", "manifestUrl": "m", "manifestSha256": "s"}}
    assert app_stage.feed_update(stable, current="2.0.1") is None
    # Once stable passes the beta, it updates.
    stable["update"]["version"] = "2.0.2"
    assert app_stage.feed_update(stable, current="2.0.1") is not None
