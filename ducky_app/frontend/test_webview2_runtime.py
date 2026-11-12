"""No WebView2 runtime: one clear message before the window starts, never a window on the old
IE engine that sits on "loading" (Nov 11 2026, 1.2.485: launcher exit 20 webview2_missing, then
a Python window that fetched the page files and never ran them)."""

from __future__ import annotations

import pytest

from frontend import webview2_runtime as wv2
from frontend.ui_web import shutdown


def test_versions_compare_as_numbers() -> None:
    assert wv2.version_at_least("130.0.2849.80")
    assert wv2.version_at_least("86.0.622.0") and wv2.version_at_least("86.0.622")
    assert wv2.version_at_least("131.0.2903.5 canary")
    assert not wv2.version_at_least("9.0.0.0")  # as text "9" sorts after "86"
    assert not wv2.version_at_least("86.0.621.99") and not wv2.version_at_least("86")
    assert not wv2.version_at_least("") and not wv2.version_at_least("abc") and not wv2.version_at_least("130.x.1")


def test_a_runtime_or_a_channel_new_enough_starts_the_window() -> None:
    assert wv2.problem(["130.0.2849.80"]) == ""
    assert wv2.problem(["79.0.309.0", "131.0.2903.5"]) == ""  # an old leftover beside a current one


def test_no_runtime_says_what_to_install() -> None:
    text = wv2.problem([])
    assert text.startswith("UEFN Ducky needs Microsoft Edge WebView2 Runtime to show its window, and this PC doesn't have it.")
    assert wv2.DOWNLOAD_PAGE in text and "Your chats and settings are kept." in text


def test_an_old_runtime_names_the_version_found() -> None:
    assert "this PC has an old one (version 79.0.309.0)" in wv2.problem(["79.0.309.0"])


def test_require_raises_only_when_the_window_cannot_start(monkeypatch) -> None:
    monkeypatch.setattr(wv2, "registered_versions", lambda: ["130.0.2849.80"])
    monkeypatch.setattr(wv2.sys, "platform", "win32")
    wv2.require()
    monkeypatch.setattr(wv2, "registered_versions", lambda: [])
    with pytest.raises(wv2.WebView2Missing, match="doesn't have it"):
        wv2.require()


def test_the_start_error_is_the_plain_message_with_its_own_code_and_an_offer_to_open_the_page(monkeypatch) -> None:
    import tkinter.messagebox as messagebox
    import webbrowser

    from frontend import boot_report, error_log

    exc = wv2.WebView2Missing(wv2.problem([]))
    assert shutdown._format_fatal_message(exc) == str(exc)  # no "WebView2Missing:" in front, no traceback

    asked: list[str] = []
    opened: list[str] = []
    logged: list[tuple[str, str]] = []
    answers = [True, False]
    monkeypatch.setattr(messagebox, "askyesno", lambda title, message, **kw: asked.append(message) or answers.pop(0))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: pytest.fail("a plain error box"))
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(shutdown, "hard_exit", lambda api=None: None)
    monkeypatch.setattr(error_log, "record_error", lambda source, message: logged.append((source, message)))
    monkeypatch.setattr(boot_report, "mark", lambda *a, **k: None)

    class _Root:
        def destroy(self) -> None:
            pass

    shutdown.fatal_error_and_exit(exc, tk_root=_Root())
    shutdown.fatal_error_and_exit(exc, tk_root=_Root())  # "No": nothing opens
    assert opened == [wv2.DOWNLOAD_PAGE]
    assert all(m.endswith("Open Microsoft's download page now?") for m in asked) and len(asked) == 2
    assert [source for source, _ in logged] == ["webview2_missing", "webview2_missing"]
    assert "Traceback" not in logged[0][1] and "\n" not in logged[0][1]
