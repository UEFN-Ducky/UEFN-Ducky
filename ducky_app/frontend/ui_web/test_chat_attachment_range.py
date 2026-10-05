from __future__ import annotations

import io

from frontend.ui_web import panel_httpd as h

MIB = 1024 * 1024


def _serve(path, range_header):
    status, headers, start, length = h._plan_file_range(path, range_header)
    sink = io.BytesIO()
    with path.open("rb") as fh:
        h._copy_file_range(fh, start, length, sink)
    return status, headers, sink.getvalue()


def test_regex_accepts_videos_and_frames():
    assert h._CHAT_ATTACHMENT_RE.match("chat-attachments/c1/1_0_bug.mp4")
    assert h._CHAT_ATTACHMENT_RE.match("chat-attachments/c1/1_0_bug.mp4.f20-01.jpg")
    assert not h._CHAT_ATTACHMENT_RE.match("chat-attachments/c1/evil.exe")


def test_no_range_streams_the_whole_file(tmp_path):
    f = tmp_path / "v.mp4"
    data = bytes(range(256)) * (3 * MIB // 256 + 5)  # > 2 copy chunks, not a multiple of 1 MiB
    f.write_bytes(data)
    status, headers, body = _serve(f, None)
    assert (status, headers) == (200, {"Accept-Ranges": "bytes"})
    assert body == data
    assert h._plan_file_range(f, None)[3] == len(data)


def test_explicit_small_range_unchanged(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"0123456789")
    status, headers, body = _serve(f, "bytes=2-4")
    assert (status, body) == (206, b"234")
    assert headers == {"Accept-Ranges": "bytes", "Content-Range": "bytes 2-4/10"}


def test_unsatisfiable_range_is_416(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"0123456789")
    status, headers, body = _serve(f, "bytes=50-60")
    assert (status, body, headers["Content-Range"]) == (416, b"", "bytes */10")


def test_open_ended_range_is_capped(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * (10 * MIB))
    status, headers, body = _serve(f, "bytes=0-")
    assert status == 206 and len(body) == 4 * MIB
    assert headers["Content-Range"] == f"bytes 0-{4 * MIB - 1}/{10 * MIB}"
    status, headers, body = _serve(f, f"bytes={9 * MIB}-")  # tail shorter than the cap
    assert len(body) == MIB and headers["Content-Range"] == f"bytes {9 * MIB}-{10 * MIB - 1}/{10 * MIB}"


def test_large_suffix_range_is_capped(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * (10 * MIB))
    status, headers, body = _serve(f, "bytes=-8388608")
    assert status == 206 and len(body) == 4 * MIB
    assert headers["Content-Range"].endswith(f"/{10 * MIB}")
