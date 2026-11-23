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
        self.icon = icon
        if self.loads:
            for fn in self.window.events.loaded:
                fn()
        func()


def test_smoke_fails_when_the_page_never_starts() -> None:
    import pytest

    fake = _FakeWebview(loads=False)
    with pytest.raises(RuntimeError, match="did not start"):
        runtime_smoke._start_real_window(timeout=0.05, webview_mod=fake)
    assert fake.destroyed


def test_smoke_passes_with_the_real_icon_when_the_page_starts() -> None:
    from frontend.ui_web.webview_app import webview_icon_path

    fake = _FakeWebview(loads=True)
    assert runtime_smoke._start_real_window(timeout=5, webview_mod=fake) >= 0
    assert fake.icon == str(webview_icon_path()) and fake.destroyed
