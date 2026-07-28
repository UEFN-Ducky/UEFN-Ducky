"""Unit tests for pure screenshot path / capture-store helpers."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Listener package lives under uefn_listener/ (sys.path entry used by UEFN).
_LISTENER_ROOT = Path(__file__).resolve().parents[1]
if str(_LISTENER_ROOT) not in sys.path:
    sys.path.insert(0, str(_LISTENER_ROOT))

from listener.screenshot_util import (  # noqa: E402
    CAPTURE_TIMEOUT_SEC,
    CaptureStore,
    list_screenshot_candidate_paths,
    resolve_screenshot_path,
    unique_screenshot_filename,
)


def test_unique_screenshot_filename_sanitizes_and_uniques():
    a = unique_screenshot_filename("city 00/foundation")
    b = unique_screenshot_filename("city 00/foundation")
    assert a.endswith(".png")
    assert b.endswith(".png")
    assert a != b
    assert " " not in a
    assert "/" not in a
    assert "\\" not in a


def test_unique_preserves_png_stem():
    name = unique_screenshot_filename("city_00_foundation.png")
    assert name.startswith("city_00_foundation_")
    assert name.endswith(".png")


def test_resolve_prefers_fresh_direct_file(tmp_path: Path):
    root = tmp_path / "Screenshots"
    root.mkdir()
    stale = root / "shot.png"
    stale.write_bytes(b"old")
    old = time.time() - 600
    os.utime(stale, (old, old))

    direct = root / "shot.png"
    direct.write_bytes(b"new-bytes-here")
    started = time.time()
    found = resolve_screenshot_path("shot.png", [str(root)], started_at=started)
    assert found == str(direct)


def test_resolve_one_level_subdir_only(tmp_path: Path):
    root = tmp_path / "Screenshots"
    deep = root / "a" / "b"
    deep.mkdir(parents=True)
    deep_file = deep / "deep.png"
    deep_file.write_bytes(b"too-deep")
    mid = root / "a" / "mid.png"
    mid.write_bytes(b"ok-mid")
    cands = list_screenshot_candidate_paths("mid.png", [str(root)])
    assert any(Path(p).resolve() == mid.resolve() for p in cands)
    deep_cands = list_screenshot_candidate_paths("deep.png", [str(root)])
    assert not any(Path(p).resolve() == deep_file.resolve() for p in deep_cands)
    found = resolve_screenshot_path("mid.png", [str(root)], started_at=time.time())
    assert Path(found).resolve() == mid.resolve()


def test_resolve_rejects_empty_file(tmp_path: Path):
    root = tmp_path / "Screenshots"
    root.mkdir()
    empty = root / "empty.png"
    empty.write_bytes(b"")
    assert resolve_screenshot_path("empty.png", [str(root)], started_at=time.time()) == ""


def test_resolve_absolute_path(tmp_path: Path):
    f = tmp_path / "abs.png"
    f.write_bytes(b"hello")
    assert resolve_screenshot_path(str(f), [], started_at=time.time()) == str(f)


def test_capture_store_prune_expired():
    store = CaptureStore()
    store.put("old", {"started_at": time.time() - CAPTURE_TIMEOUT_SEC * 3, "filename": "a.png"})
    store.put("new", {"started_at": time.time(), "filename": "b.png"})
    store.prune_expired()
    assert store.get("old") is None
    assert store.get("new") is not None
    assert store.pop("new")["filename"] == "b.png"
    assert store.get("new") is None
