"""HTTP Range header parsing for the /project-media/ route (video seeking)."""

from __future__ import annotations

import pytest

from frontend.ui_web.panel_httpd import _parse_range_header


def test_no_range_header_returns_none():
    assert _parse_range_header(None, 1000) is None
    assert _parse_range_header("", 1000) is None


def test_simple_start_end_range():
    assert _parse_range_header("bytes=0-499", 1000) == (0, 499)
    assert _parse_range_header("bytes=500-999", 1000) == (500, 999)


def test_open_ended_range_goes_to_eof():
    assert _parse_range_header("bytes=900-", 1000) == (900, 999)


def test_suffix_range_returns_last_n_bytes():
    assert _parse_range_header("bytes=-500", 1000) == (500, 999)
    # Suffix longer than the file clamps to byte 0.
    assert _parse_range_header("bytes=-5000", 1000) == (0, 999)


def test_end_beyond_file_size_is_clamped():
    assert _parse_range_header("bytes=0-9999", 1000) == (0, 999)


def test_unsatisfiable_range_raises():
    with pytest.raises(ValueError):
        _parse_range_header("bytes=1000-1999", 1000)
    with pytest.raises(ValueError):
        _parse_range_header("bytes=500-100", 1000)
    with pytest.raises(ValueError):
        _parse_range_header("bytes=-0", 1000)


def test_non_bytes_unit_is_ignored():
    assert _parse_range_header("items=0-5", 1000) is None


def test_zero_size_file_returns_none():
    assert _parse_range_header("bytes=0-10", 0) is None
