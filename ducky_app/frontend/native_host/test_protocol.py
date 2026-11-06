"""protocol.py: round trip, unknown fields, mismatch, oversize — and the shared fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend.native_host import protocol as p

FIXTURES = Path(__file__).with_name("fixtures")


def _lines(name: str) -> list[str]:
    return [ln for ln in (FIXTURES / name).read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_version_constants():
    assert p.PROTOCOL_VERSION == 1
    assert (p.MIN_PROTOCOL, p.MAX_PROTOCOL) == (1, 1)
    assert p.MAX_LINE_BYTES == 1024 * 1024
    assert (p.EXIT_WEBVIEW2_MISSING, p.EXIT_PROTOCOL_MISMATCH, p.EXIT_CRASH_LOOP) == (20, 21, 22)
    assert (p.EXIT_NO_HELLO, p.EXIT_START_ERROR) == (23, 24)


@pytest.mark.parametrize(
    "msg",
    [
        p.hello(app_version="1.2.471", port=51234, profile_dir="C:\\P", pid=7),
        p.result_ok("c1", {"x": 1}),
        p.result_ok("c2"),
        p.result_error("c3", "unknown"),
        p.event("toast", {"text": "hi"}),
        p.event("quit"),
        p.call("c4", "get_version"),
        p.call("c5", "send_message", ["a", 1, {"b": [True, None]}]),
        p.bye("window closed"),
    ],
)
def test_round_trip(msg):
    line = p.encode(msg)
    assert line.endswith("\n") and line.count("\n") == 1
    assert p.decode(line) == msg
    assert p.decode(line.encode("utf-8")) == msg


def test_hello_builder_origin_matches_port():
    h = p.hello(app_version="1", port=9, profile_dir="d", pid=1)
    assert h["origin"] == "http://127.0.0.1:9" and h["protocol"] == 1


def test_valid_fixture_lines_decode_and_re_encode_identically():
    for line in _lines("valid.jsonl"):
        msg = p.decode(line)
        assert json.loads(p.encode(msg)) == json.loads(line)


def test_unknown_fields_are_ignored():
    for plain, extra in zip(_lines("valid.jsonl"), _lines("unknown_fields.jsonl"), strict=True):
        assert p.decode(extra) == p.decode(plain)
    assert "future" not in p.decode(_lines("unknown_fields.jsonl")[0])
    # encode drops them too
    assert json.loads(p.encode({**p.bye("x"), "junk": 1})) == {"t": "bye", "reason": "x"}


def test_optional_fields_get_defaults():
    assert p.decode('{"t":"call","id":"1","name":"x"}')["args"] == []
    assert p.decode('{"t":"event","name":"quit"}')["data"] == {}
    assert p.decode('{"t":"bye"}')["reason"] == ""
    assert p.decode('{"t":"result","id":"1","ok":true}')["value"] is None


@pytest.mark.parametrize("line", _lines("invalid.jsonl"))
def test_invalid_lines_raise(line):
    with pytest.raises(p.ProtocolError):
        p.decode(line)


def test_encode_refuses_invalid():
    with pytest.raises(p.ProtocolError):
        p.encode({"t": "call", "id": "1"})
    with pytest.raises(p.ProtocolError):
        p.encode({"t": "result", "id": "1", "ok": False})


def test_negotiate_accepts_1():
    hello = p.decode(_lines("valid.jsonl")[0])
    assert p.negotiate(hello) == 1
    assert p.negotiate(1) == 1


@pytest.mark.parametrize("line", _lines("mismatch.jsonl"))
def test_negotiate_mismatch(line):
    with pytest.raises(p.ProtocolMismatch):
        p.negotiate(p.decode(line))


@pytest.mark.parametrize("bad", [None, "1", True, 1.0, {}])
def test_negotiate_rejects_non_int(bad):
    with pytest.raises(p.ProtocolMismatch):
        p.negotiate(bad)


def test_oversize_refused_both_ways():
    big = "x" * p.MAX_LINE_BYTES
    with pytest.raises(p.ProtocolError, match="over"):
        p.encode(p.result_ok("c1", big))
    line = json.dumps({"t": "result", "id": "c1", "ok": True, "value": big})
    with pytest.raises(p.ProtocolError, match="over"):
        p.decode(line)


def test_just_under_limit_passes():
    overhead = len(p.encode(p.result_ok("c1", "")).rstrip("\n").encode("utf-8"))
    value = "x" * (p.MAX_LINE_BYTES - overhead)
    line = p.encode(p.result_ok("c1", value))
    assert len(line.rstrip("\n").encode("utf-8")) == p.MAX_LINE_BYTES
    assert p.decode(line)["value"] == value


def test_utf8_counts_bytes_not_chars():
    value = "é" * (p.MAX_LINE_BYTES // 2)  # 2 bytes each: over the limit in bytes
    with pytest.raises(p.ProtocolError):
        p.encode(p.result_ok("c1", value))


def test_decode_tolerates_crlf_and_rejects_bad_utf8():
    assert p.decode('{"t":"bye"}\r\n') == {"t": "bye", "reason": ""}
    with pytest.raises(p.ProtocolError):
        p.decode(b'{"t":"bye","reason":"\xff"}')
