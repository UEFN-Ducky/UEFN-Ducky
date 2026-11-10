"""Tests for Store-hosted app version-check payload parsing."""

from __future__ import annotations

from frontend.version_check import (
    absolute_installer_url,
    describe_clock_skew,
    extract_release_versions,
    feed_is_paused,
    is_remote_newer,
    parse_version_tuple,
    unwrap_collect_payload,
)


def test_parse_version_tuple() -> None:
    assert parse_version_tuple("1.0.441") == (1, 0, 441)
    assert parse_version_tuple("bad") is None


def test_describe_clock_skew() -> None:
    server = "Thu, 24 Sep 2026 17:30:00 GMT"
    real = 1790271000.0  # that instant
    assert describe_clock_skew(server, real + 60) is None
    assert "clock is wrong" in (describe_clock_skew(server, real + 400 * 86400) or "")
    assert "clock is wrong" in (describe_clock_skew(server, real - 3600) or "")
    assert describe_clock_skew(None, real) is None
    assert describe_clock_skew("garbage", real) is None


def test_is_remote_newer() -> None:
    assert is_remote_newer("1.0.440", "1.0.441") is True
    assert is_remote_newer("1.0.441", "1.0.441") is False
    assert is_remote_newer("1.0.442", "1.0.441") is False


def test_unwrap_collect_payload_nested() -> None:
    envelope = {
        "ok": True,
        "payload": {
            "handled": True,
            "payload": {
                "currentVersion": "1.0.500",
                "installerUrl": "/api/v1/files/x/content",
                "installerSha256": "abc",
                "releaseNotes": "notes",
            },
        },
    }
    # Host wraps plugin {handled,payload}; client may see either depth.
    # Our unwrap prefers the innermost object that has currentVersion.
    outer = envelope["payload"]
    assert unwrap_collect_payload({"payload": outer})["currentVersion"] == "1.0.500"
    assert unwrap_collect_payload(outer)["currentVersion"] == "1.0.500"
    assert unwrap_collect_payload(outer["payload"])["currentVersion"] == "1.0.500"


def test_unwrap_flat_legacy() -> None:
    flat = {"currentVersion": "1.0.1", "installerUrl": "https://example/setup.exe"}
    assert unwrap_collect_payload(flat)["currentVersion"] == "1.0.1"


def test_absolute_installer_url() -> None:
    base = "https://uefnducky.org"
    assert absolute_installer_url("/api/v1/files/abc/content", base_url=base) == (
        "https://uefnducky.org/api/v1/files/abc/content"
    )
    assert (
        absolute_installer_url("https://cdn.example/setup.exe", base_url=base)
        == "https://cdn.example/setup.exe"
    )
    assert absolute_installer_url(None, base_url=base) is None


def test_extract_release_versions() -> None:
    rows = extract_release_versions(
        {
            "currentVersion": "1.2.45",
            "releaseNotes": "latest",
            "versions": [
                {"version": "1.2.45", "changelog": "Effort dial", "created_at": "1"},
                {"version": "1.2.44", "changelog": "Remote kick", "created_at": "2"},
                {"version": "", "changelog": "skip"},
            ],
        }
    )
    assert [r["version"] for r in rows] == ["1.2.45", "1.2.44"]
    assert rows[0]["changelog"] == "Effort dial"
    fallback = extract_release_versions({"currentVersion": "1.2.40", "releaseNotes": "one"})
    assert fallback == [{"version": "1.2.40", "changelog": "one", "created_at": None}]


def test_extract_release_versions_keeps_short_changes() -> None:
    rows = extract_release_versions(
        {
            "versions": [
                {
                    "version": "1.2.412",
                    "changelog": "Long notes.",
                    "items": [
                        {"kind": "fix", "text": " Team plugins no longer reset by themselves "},
                        {"kind": "odd", "text": "Faster start"},
                        {"kind": "new", "text": ""},
                        "junk",
                    ],
                },
                {"version": "1.2.411", "changelog": "Old style only."},
            ]
        }
    )
    assert rows[0]["items"] == [
        {"kind": "fix", "text": "Team plugins no longer reset by themselves"},
        {"kind": "improved", "text": "Faster start"},
    ]
    assert "items" not in rows[1]


def test_feed_is_paused() -> None:
    assert feed_is_paused({"downloadsPaused": True, "currentVersion": "1.2.133"}) is True
    assert feed_is_paused({"downloadsPaused": False, "currentVersion": "1.2.135"}) is False
    assert feed_is_paused({"currentVersion": "1.2.135"}) is False


def test_update_channel_dev_blocks_store() -> None:
    from frontend.version_check import update_channel

    # Portable / installed classification without needing a frozen Dev EXE.
    assert update_channel(installed=True) in ("installed", "dev")
    assert update_channel(installed=False) in ("portable", "dev")


def _patch_net(monkeypatch, first_error: BaseException, fallback_error: BaseException | None = None) -> list:
    import io
    import urllib.request

    from frontend import net_access, version_check

    monkeypatch.setenv(version_check.UPDATE_BASE_URL_ENV, "https://feed.test")
    monkeypatch.setattr(version_check, "clock_skew_message", lambda base: None)
    monkeypatch.setattr(version_check.time, "sleep", lambda s: None)
    from frontend import update_network

    def no_doh(req, timeout):
        raise OSError("no DoH in tests")

    monkeypatch.setattr(update_network, "doh_open", no_doh)

    def direct(req, timeout):
        raise first_error

    opens = []

    class Opener:
        def open(self, req, timeout):
            opens.append(req.full_url)
            if fallback_error is not None:
                raise fallback_error
            return io.BytesIO(b'{"payload": {"currentVersion": "9.9.9"}}')

    monkeypatch.setattr(urllib.request, "urlopen", direct)
    monkeypatch.setattr(net_access, "build_opener", lambda *h: Opener())
    return opens


def test_tls_failure_gets_through_on_the_windows_store_retry(monkeypatch) -> None:
    """Antivirus HTTPS scanning: OpenSSL refuses its root, the Windows store has it."""
    import ssl
    import urllib.error

    from frontend import version_check

    opens = _patch_net(monkeypatch, urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer")))
    payload, error = version_check.fetch_remote_payload(attempts=2)
    assert error is None and payload == {"currentVersion": "9.9.9"}
    assert version_check.last_fallback == "tls" and len(opens) == 1


def test_dns_failure_gets_through_on_the_proxy_retry(monkeypatch) -> None:
    import socket
    import urllib.error

    from frontend import version_check

    opens = _patch_net(monkeypatch, urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")))
    payload, error = version_check.fetch_remote_payload(attempts=2)
    assert error is None and payload["currentVersion"] == "9.9.9"
    assert version_check.last_fallback == "dns" and len(opens) == 1


def test_failed_fallback_still_explains_the_first_stage(monkeypatch) -> None:
    import socket
    import urllib.error

    from frontend import version_check

    dns = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
    opens = _patch_net(monkeypatch, dns, fallback_error=dns)
    payload, error = version_check.fetch_remote_payload(attempts=2)
    assert payload is None and "DNS" in (error or "")
    assert version_check.last_net_stage == "dns" and version_check.last_fallback == "" and len(opens) == 1
    assert error.startswith("Can't reach the update server.")


def test_broken_dns_gets_through_on_the_doh_lookup(monkeypatch) -> None:
    """The PC's resolver and its proxy both fail: DoH finds the Store and the check still lands."""
    import io
    import socket
    import urllib.error

    from frontend import update_network, version_check

    dns = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
    real_doh_open = update_network.doh_open
    _patch_net(monkeypatch, dns, fallback_error=dns)
    hosts = []

    def fake_resolve(host, timeout=5.0):
        hosts.append(host)
        return "203.0.113.7"

    class Pinned:
        def __init__(self, ip):
            self.ip = ip

        def open(self, req, timeout):
            assert self.ip == "203.0.113.7"
            return io.BytesIO(b'{"payload": {"currentVersion": "1.2.488"}}')

    monkeypatch.setattr(update_network, "doh_open", real_doh_open)
    monkeypatch.setattr(update_network, "doh_resolve", fake_resolve)
    monkeypatch.setattr(update_network, "pinned_opener", Pinned)
    payload, error = version_check.fetch_remote_payload(attempts=2)
    assert error is None and payload["currentVersion"] == "1.2.488"
    assert hosts == ["feed.test"] and version_check.last_fallback == "doh"


def test_doh_resolve_reads_the_first_a_record(monkeypatch) -> None:
    import io
    import urllib.request

    from frontend import update_network

    urls = []

    def fake_urlopen(req, timeout):
        urls.append(req.full_url)
        if len(urls) == 1:
            raise OSError("1.1.1.1 blocked")
        return io.BytesIO(b'{"Answer": [{"type": 5, "data": "cname."}, {"type": 1, "data": "198.51.100.4"}]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert update_network.doh_resolve("uefnducky.org") == "198.51.100.4"
    assert urls[1].startswith("https://8.8.8.8/") and "name=uefnducky.org" in urls[1]


if __name__ == "__main__":
    test_parse_version_tuple()
    test_is_remote_newer()
    test_unwrap_collect_payload_nested()
    test_unwrap_flat_legacy()
    test_absolute_installer_url()
    test_extract_release_versions()
    test_feed_is_paused()
    test_update_channel_dev_blocks_store()
    print("ok")
