"""The runtime-smoke report is written even when the smoke fails."""

from __future__ import annotations

import json
import sys

from frontend import runtime_smoke


def test_report_is_written_even_when_the_smoke_fails_later(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "backend.agent.hard_rules", None)  # its import now raises
    out = tmp_path / "smoke.json"
    assert runtime_smoke.run(str(out)) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ok"] is False and "native_launcher" not in report


class _FakeWebview:
    """pywebview stand-in: a window whose page loads only when ``loads`` is true."""

    def __init__(self, loads: bool) -> None:
        self.loads, self.destroyed, self.icon = loads, False, None

    def create_window(self, *args, **kwargs):
        fake = self

        class Handlers(list):
            def __iadd__(self, fn):
                self.append(fn)
                return self

        class Window:
            events = type("Events", (), {"loaded": Handlers()})()

            def destroy(self) -> None:
                fake.destroyed = True

        self.window = Window()
        return self.window

    def start(self, func, gui, icon) -> None:
        self.icon, self.gui = icon, gui
        if self.loads:
            for fn in self.window.events.loaded:
                fn()
        func()


def test_tk_check_skips_tk_window_without_display(monkeypatch) -> None:
    import tkinter

    def no_window():
        raise AssertionError("Tk() needs DISPLAY; pure Wayland has none")

    monkeypatch.setattr(tkinter, "Tk", no_window)
    assert runtime_smoke._check_tk(environ={"WAYLAND_DISPLAY": "wayland-0"}) == tkinter.TkVersion
    import pytest

    with pytest.raises(AssertionError):
        runtime_smoke._check_tk(environ={"DISPLAY": ":0"})


def test_smoke_fails_when_the_page_never_starts() -> None:
    import pytest

    fake = _FakeWebview(loads=False)
    with pytest.raises(RuntimeError, match="did not start"):
        runtime_smoke._start_real_window(timeout=0.05, webview_mod=fake)
    assert fake.destroyed


class _FakeFoundation:
    """Foundation stand-in: NSBundle answers for WebKit.framework (``None`` = not loaded)."""

    def __init__(self, version: str | None) -> None:
        bundle = None
        if version is not None:
            bundle = type("B", (), {"objectForInfoDictionaryKey_": lambda self, key: version})()
        self.NSBundle = type("NSBundle", (), {"bundleWithIdentifier_": staticmethod(lambda ident: bundle)})


def test_darwin_cocoa_window_reports_webkit_and_starts_the_page() -> None:
    fake = _FakeWebview(loads=True)
    got = runtime_smoke._check_cocoa_window(webview_mod=fake, cocoa_mod=object(),
                                            foundation=_FakeFoundation("20620.1.16.11.8"))
    assert got["webkit"] == "620.1" and got["page_started_seconds"] >= 0
    assert fake.gui == "cocoa" and fake.destroyed


def test_darwin_smoke_fails_without_webkit_or_a_page() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="WebKit"):
        runtime_smoke._check_cocoa_window(webview_mod=_FakeWebview(True), cocoa_mod=object(), foundation=_FakeFoundation(None))
    assert runtime_smoke.webkit_version("19618.1.15.11.14") == "618.1"


def test_darwin_run_writes_window_and_webkit(tmp_path, monkeypatch) -> None:
    from frontend import platform_info

    monkeypatch.setattr(platform_info, "IS_LINUX", False)
    monkeypatch.setattr(platform_info, "IS_MAC", True)
    monkeypatch.setattr(runtime_smoke, "_check_cocoa_window", lambda: {"webkit": "620.1", "page_started_seconds": 0.4})
    out = tmp_path / "smoke.json"
    code = runtime_smoke.run(str(out))
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0, report.get("error")
    assert report["ok"] is True and report["window"] is True and report["webkit"] == "620.1"


def test_smoke_passes_with_the_real_icon_when_the_page_starts() -> None:
    from frontend.ui_web.webview_app import webview_icon_path

    fake = _FakeWebview(loads=True)
    assert runtime_smoke._start_real_window(timeout=5, webview_mod=fake) >= 0
    assert fake.icon == str(webview_icon_path()) and fake.destroyed
