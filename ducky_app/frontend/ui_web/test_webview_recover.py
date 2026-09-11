"""WebView2 ProcessFailed recovery — blank pane must Reload, not stay dead."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.ui_web import webview_recover as wr


class _Evt:
    def __init__(self) -> None:
        self.subs: list[object] = []

    def __iadd__(self, fn: object) -> _Evt:
        self.subs.append(fn)
        return self


class _Core:
    def __init__(self) -> None:
        self.reloads = 0
        self.navigated: list[str] = []
        self.Source = "http://127.0.0.1:4199/"
        self._handlers: list[object] = []
        self.ProcessFailed = _Evt()

    def Reload(self) -> None:
        self.reloads += 1

    def Navigate(self, src: str) -> None:
        self.navigated.append(src)


class _FailingReload(_Core):
    def Reload(self) -> None:
        raise RuntimeError("dead")


def test_reload_on_recover(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    core = _Core()
    assert wr.recover_core_webview(core, reason="gpu")["action"] == "reload"
    assert core.reloads == 1


def test_navigate_when_reload_raises(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    core = _FailingReload()
    assert wr.recover_core_webview(core, reason="browser")["action"] == "navigate"
    assert core.navigated == [core.Source]


def test_debounce_second_call(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    core = _Core()
    wr.recover_core_webview(core, reason="a")
    assert wr.recover_core_webview(core, reason="b")["action"] == "debounced"
    assert core.reloads == 1


def test_attach_hides_then_reloads(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    monkeypatch.setattr(wr, "_attached", set())
    core = _Core()
    hidden: list[str] = []
    handler = wr.attach_process_failed(core, label="pane:x", on_fail=hidden.append)
    assert handler is not None
    core._handlers.append(handler)
    # pythonnet-style += is not on this stub — invoke the handler directly.
    handler(None, SimpleNamespace(ProcessFailedKind="GpuProcessExited"))
    assert hidden == ["pane:x:GpuProcessExited"]
    assert core.reloads == 1
    assert wr.attach_process_failed(core, label="pane:x") is None


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
