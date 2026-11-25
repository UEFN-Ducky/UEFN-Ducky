"""Every copy reads the stable feed: there is no Beta updates choice, on any OS."""

from __future__ import annotations

import io

import pytest

from frontend import platform_info, version_check
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


@pytest.mark.parametrize("linux", [False, True])
def test_every_os_reads_the_stable_feed_with_todays_exact_request(monkeypatch, sent, linux):
    monkeypatch.setattr(platform_info, "IS_LINUX", linux)
    payload, err = version_check.fetch_remote_payload(attempts=1)
    assert err is None and payload == {"currentVersion": "9.9.9"}
    assert (sent[0].full_url, sent[0].get_method(), sent[0].data, sorted(sent[0].header_items())) == (
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


def test_an_old_saved_beta_choice_is_dropped():
    # Settings saved by a version with the switch: the key is ignored, the feed stays stable.
    assert "update_channel" not in PanelSettings.__dataclass_fields__
