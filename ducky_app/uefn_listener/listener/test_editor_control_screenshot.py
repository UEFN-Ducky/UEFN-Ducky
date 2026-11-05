"""Viewport capture must produce a PNG — no Unreal required.

Regression for the capture that "started" and never produced a file: the
listener asked for ``unreal.AutomationLibrary.take_screenshot`` (a name UEFN
does not have), fell through to the ``Shot`` console command (a game-viewport
command that writes nothing in the editor), and returned no path at all.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import struct
import sys
import tempfile
import types
import zlib
from pathlib import Path
from typing import Any

import pytest

MODULE = Path(__file__).resolve().parent / "registry" / "editor_control.py"

# Smallest valid PNG (1x1). Stands in for the base64 CaptureViewport returns.
PNG_BYTES = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeRegistry:
    """Stand-in for ``unreal.ToolsetRegistry`` (Epic's in-process toolset API)."""

    def __init__(self, *, available: bool = True, image_b64: str | None = None, error: str = ""):
        self._available = available
        self._image_b64 = image_b64
        self._error = error
        self.calls: list[tuple[str, str, str]] = []

    def is_available(self) -> bool:
        return self._available

    def execute_tool(self, toolset: str, tool: str, json_input: str) -> Any:
        self.calls.append((toolset, tool, json_input))
        payload: dict[str, Any] = {"returnValue": {}}
        if self._image_b64 is not None:
            payload["returnValue"]["image"] = {"mimeType": "image/png", "data": self._image_b64}
        outer = self

        class _Result:
            is_complete = True
            error = outer._error
            value = json.dumps(payload)

        return _Result()


class FakeAutomationLibrary:
    """UEFN's real AutomationLibrary: no ``take_screenshot`` attribute."""

    @staticmethod
    def take_automation_screenshot(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("automation screenshot path must not be used")


def _load(monkeypatch, tmp_path, *, registry: FakeRegistry | None) -> tuple[Any, list[str]]:
    console: list[str] = []
    unreal = types.ModuleType("unreal")

    class Paths:
        @staticmethod
        def project_saved_dir() -> str:
            return str(tmp_path / "project" / "Saved")

        @staticmethod
        def project_dir() -> str:
            return str(tmp_path / "project")

    class SystemLibrary:
        @staticmethod
        def execute_console_command(world, command):
            console.append(command)

    class EditorLevelLibrary:
        @staticmethod
        def get_editor_world():
            return object()

    unreal.Paths = Paths
    unreal.SystemLibrary = SystemLibrary
    unreal.EditorLevelLibrary = EditorLevelLibrary
    unreal.AutomationLibrary = FakeAutomationLibrary
    if registry is not None:
        unreal.ToolsetRegistry = registry

    dispatch = types.ModuleType("listener.dispatch")
    dispatch.register = lambda name: (lambda fn: fn)
    serialize = types.ModuleType("listener.serialize")
    serialize.serialize = lambda value: value
    package = types.ModuleType("listener")
    package.__path__ = [str(MODULE.parent.parent)]

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))
    (tmp_path / "temp").mkdir(exist_ok=True)
    monkeypatch.setitem(sys.modules, "unreal", unreal)
    monkeypatch.setitem(sys.modules, "listener", package)
    monkeypatch.setitem(sys.modules, "listener.dispatch", dispatch)
    monkeypatch.setitem(sys.modules, "listener.serialize", serialize)

    spec = importlib.util.spec_from_file_location("editor_control_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, console


def test_viewport_capture_writes_the_png_and_replies_with_its_path(monkeypatch, tmp_path):
    registry = FakeRegistry(image_b64=base64.b64encode(PNG_BYTES).decode("ascii"))
    module, console = _load(monkeypatch, tmp_path, registry=registry)

    out = module.take_high_res_screenshot(width=1280, height=720, filename="corner.png")

    assert out["ok"] is True
    assert "png_base64" not in out
    assert Path(out["path"]).read_bytes() == PNG_BYTES
    assert Path(out["path"]).parent == tmp_path / "temp" / "ducky_captures"
    assert (out["width"], out["height"]) == (1, 1)  # the PNG's real size
    assert (out["requested_width"], out["requested_height"]) == (1280, 720)
    assert not out.get("error")
    assert not out.get("await_path")
    assert not list(tmp_path.rglob("*.part"))


def test_capture_goes_into_the_chat_attachments_folder_the_host_passes(monkeypatch, tmp_path):
    registry = FakeRegistry(image_b64=base64.b64encode(PNG_BYTES).decode("ascii"))
    module, _ = _load(monkeypatch, tmp_path, registry=registry)
    attachments = tmp_path / "conversations" / "chat1" / "attachments"

    out = module.take_high_res_screenshot(save_dir=str(attachments))

    assert Path(out["path"]).parent == attachments
    assert Path(out["path"]).read_bytes() == PNG_BYTES


def _big_png(width: int, height: int, size: int) -> bytes:
    """A PNG header (signature + IHDR for ``width`` x ``height``) padded to ``size`` bytes."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    head = b"\x89PNG\r\n\x1a\n" + chunk
    return head + os.urandom(size - len(head))


def _limits(monkeypatch):
    monkeypatch.delenv("UEFN_DUCKY_MAX_REPLY_MB", raising=False)
    spec = importlib.util.spec_from_file_location("limits_under_test", MODULE.parent.parent / "limits.py")
    limits = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(limits)
    return limits


def test_a_capture_over_2mb_comes_back_ok_with_its_image_not_truncated(monkeypatch, tmp_path):
    raw = _big_png(1280, 720, int(2.4 * 1024 * 1024))
    registry = FakeRegistry(image_b64=base64.b64encode(raw).decode("ascii"))
    module, _ = _load(monkeypatch, tmp_path, registry=registry)
    limits = _limits(monkeypatch)

    out = module.take_high_res_screenshot(width=1280, height=720)
    reply = json.loads(limits.encode_reply({"success": True, "result": out}))["result"]

    assert len(base64.b64encode(raw)) > limits.max_reply_bytes()  # inline would have been cut
    assert not reply.get("truncated")
    assert reply["ok"] is True
    assert (reply["width"], reply["height"]) == (1280, 720)
    assert Path(reply["path"]).read_bytes() == raw
    assert reply["bytes"] == len(raw)


def test_a_census_over_2mb_is_still_truncated(monkeypatch, tmp_path):
    limits = _limits(monkeypatch)
    actors = [{"label": f"Actor_{i}", "path": "/Game/" + "x" * 800} for i in range(4000)]
    response = {"success": True, "result": {"actors": actors, "count": len(actors)}}
    assert len(json.dumps(response)) > 2 * 1024 * 1024

    raw = limits.encode_reply(response)
    out = json.loads(raw)["result"]

    assert len(raw) <= limits.max_reply_bytes()
    assert out["truncated"] is True and out["total"] == 4000
    assert 0 < out["returned"] == len(out["actors"]) < 4000


def test_viewport_capture_never_falls_back_to_the_shot_console_command(monkeypatch, tmp_path):
    registry = FakeRegistry(image_b64=base64.b64encode(PNG_BYTES).decode("ascii"))
    module, console = _load(monkeypatch, tmp_path, registry=registry)

    module.take_high_res_screenshot()

    assert console == [], f"'Shot' writes nothing in the UEFN editor: {console}"
    assert registry.calls, "capture must go through the toolset registry"
    toolset, tool, _ = registry.calls[0]
    assert tool == "CaptureViewport"
    assert "EditorAppToolset" in toolset


def test_capture_never_writes_into_the_uefn_project_folder(monkeypatch, tmp_path):
    registry = FakeRegistry(image_b64=base64.b64encode(PNG_BYTES).decode("ascii"))
    module, _ = _load(monkeypatch, tmp_path, registry=registry)

    out = module.take_high_res_screenshot()

    assert Path(out["path"]).is_file()
    assert not list((tmp_path / "project").rglob("*.png"))


def test_missing_registry_reports_an_error_instead_of_a_silent_no_op(monkeypatch, tmp_path):
    module, console = _load(monkeypatch, tmp_path, registry=None)

    with pytest.raises(RuntimeError) as excinfo:
        module.take_high_res_screenshot()

    assert "capture" in str(excinfo.value).lower()
    assert console == []
