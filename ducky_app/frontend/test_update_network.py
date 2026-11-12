"""The second storage host for update downloads: which links have one, the order they
are tried in, and what counts as a reason to try the other."""

from __future__ import annotations

import http.client
import ssl
import urllib.error

import pytest

from frontend import update_network as un


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    monkeypatch.setattr(un, "_alt_until", 0.0)
    counted: list[tuple[str, dict]] = []
    monkeypatch.setattr("frontend.usage_stats.track", lambda name, **props: counted.append((name, props)) or True)
    return counted


def test_only_the_sites_download_routes_have_a_second_host() -> None:
    base = "https://uefnducky.org"
    assert un.alt_url(f"{base}/api/v1/files/app-release/latest") == f"{base}/api/v1/files/app-release/latest?alt=1"
    assert un.alt_url(f"{base}/api/v1/files/0198/content?v=2") == f"{base}/api/v1/files/0198/content?v=2&alt=1"
    assert un.alt_url(f"{base}/api/v1/files/0198/content?alt=1") == "", "already asks for it"
    assert un.alt_url("https://bucket.fly.storage.tigris.dev/duckyos/x?X-Amz-Signature=abc") == "", "a direct link"
    assert un.alt_url(f"{base}/api/v1/plugins/x/collect/y") == ""
    assert un.alt_url("") == "" and un.alt_url("http://[bad") == ""


def test_a_cut_connection_tries_the_second_host_and_then_starts_there(_fresh) -> None:
    url = "https://uefnducky.org/api/v1/files/0198/content"
    cut = urllib.error.URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol"))
    tried: list[str] = []

    def run(link: str) -> str:
        tried.append(link)
        if "alt=1" not in link:
            raise cut
        return "bytes"

    assert un.with_alt(url, run) == "bytes"
    assert tried == [url, url + "?alt=1"]
    assert _fresh == [("update.download_fail", {"host": "main", "stage": "connect", "what": "pack"})]
    tried.clear()
    assert un.with_alt(url, run) == "bytes" and tried == [url + "?alt=1"], "the host that worked goes first for a day"
    # When the second host is the one that fails, the usual one is tried and takes over again.
    tried.clear()

    def other_way(link: str) -> str:
        tried.append(link)
        if "alt=1" in link:
            raise http.client.IncompleteRead(b"")
        return "bytes"

    assert un.with_alt(url, other_way, what="setup") == "bytes" and tried == [url + "?alt=1", url]
    assert _fresh[-1] == ("update.download_fail", {"host": "alt", "stage": "read", "what": "setup"})
    assert un.host_order(url) == [url, url + "?alt=1"]


def test_an_answer_from_the_server_or_a_bad_certificate_is_not_a_reason_to_switch(_fresh) -> None:
    url = "https://uefnducky.org/api/v1/files/0198/content"
    for exc in (urllib.error.HTTPError(url, 404, "Not Found", {}, None),
                urllib.error.URLError(ssl.SSLCertVerificationError("bad cert")), ValueError("bad URL")):
        tried: list[str] = []

        def run(link: str, exc: BaseException = exc) -> str:
            tried.append(link)
            raise exc

        with pytest.raises(type(exc)):
            un.with_alt(url, run)
        assert tried == [url], exc
    # A link with no second host fails as it did.
    with pytest.raises(urllib.error.URLError):
        un.with_alt("https://elsewhere.test/setup.exe", lambda link: (_ for _ in ()).throw(
            urllib.error.URLError(ConnectionResetError(10054, "reset"))))
    assert _fresh == []
    assert un.fail_stage(urllib.error.HTTPError(url, 503, "busy", {}, None)) == "http"
