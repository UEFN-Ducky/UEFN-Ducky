"""Canvas HTTP helpers — no live network."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from backend.util.http import HttpError, encode_image, http_json, poll, resolve_image


def test_encode_and_resolve_image(tmp_path) -> None:
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    uri = encode_image(str(p))
    assert uri.startswith("data:image/png;base64,")
    assert resolve_image(str(p)).startswith("data:image/png;")
    assert resolve_image("https://example.com/a.png") == "https://example.com/a.png"
    assert resolve_image(uri) == uri
    assert resolve_image("") == ""


def test_http_json_ok() -> None:
    payload = json.dumps({"ok": True}).encode()
    resp = MagicMock()
    resp.read.return_value = payload
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    with patch("backend.util.http.urllib.request.urlopen", return_value=resp):
        assert http_json("GET", "https://example.com/x") == {"ok": True}


def test_http_json_http_error() -> None:
    err = HTTPError("https://example.com/x", 401, "no", hdrs=None, fp=io.BytesIO(b'{"detail":"nope"}'))
    with patch("backend.util.http.urllib.request.urlopen", side_effect=err):
        try:
            http_json("GET", "https://example.com/x")
        except HttpError as exc:
            assert exc.status == 401
            assert exc.detail == {"detail": "nope"}
        else:
            raise AssertionError("expected HttpError")


def test_http_json_network_error() -> None:
    with patch(
        "backend.util.http.urllib.request.urlopen",
        side_effect=URLError("offline"),
    ):
        try:
            http_json("GET", "https://example.com/x")
        except HttpError as exc:
            assert exc.status is None
            assert "network error" in str(exc)
        else:
            raise AssertionError("expected HttpError")


def test_poll_stops_when_done() -> None:
    calls = {"n": 0}

    def get_status() -> dict[str, str]:
        calls["n"] += 1
        return {"status": "FINISHED" if calls["n"] >= 2 else "RUNNING"}

    with patch("backend.util.http.time.sleep"):
        out = poll(
            get_status,
            done=lambda r: r["status"] == "FINISHED",
            failed=lambda r: r["status"] == "FAILED",
            interval=1,
            max_attempts=5,
        )
    assert out["status"] == "FINISHED"
    assert calls["n"] == 2


def test_poll_timeout() -> None:
    with patch("backend.util.http.time.sleep"):
        try:
            poll(
                lambda: {"status": "RUNNING"},
                done=lambda r: False,
                failed=lambda r: False,
                interval=1,
                max_attempts=3,
            )
        except TimeoutError:
            return
        raise AssertionError("expected TimeoutError")
