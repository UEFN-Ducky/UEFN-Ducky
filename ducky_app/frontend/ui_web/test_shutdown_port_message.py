"""Every port refused (WinError 10013): plain words, not a traceback. Other errors unchanged."""

from __future__ import annotations

import pytest

from frontend.ui_web import shutdown

_TEXTS = {
    "en": "An attempt was made to access a socket in a way forbidden by its access permissions",
    "fr": "Une tentative d’accès à un socket de manière interdite par ses autorisations d’accès a été tentée",
}


def _refused(lang: str) -> OSError:
    exc = OSError(13, _TEXTS[lang])
    exc.winerror = 10013
    return exc


@pytest.mark.parametrize("lang", sorted(_TEXTS))
def test_10013_gets_the_friendly_text(lang: str) -> None:
    exc = _refused(lang)
    text = shutdown._format_fatal_message(exc)
    assert text.startswith("Windows is blocking the network ports Ducky uses")
    assert "netsh int ipv4 show excludedportrange protocol=tcp" in text
    assert _TEXTS[lang] in text  # the details stay copyable
    assert shutdown._ports_blocked(exc)


def test_other_errors_are_unchanged() -> None:
    busy = OSError(98, "Address already in use")
    busy.winerror = 10048
    import traceback

    assert shutdown._format_fatal_message(busy) == "".join(traceback.format_exception_only(OSError, busy)).strip()
    assert not shutdown._ports_blocked(busy)
    assert shutdown._format_fatal_message(ValueError("boom")) == "ValueError: boom"
    assert shutdown._format_fatal_message("plain") == "plain"


def test_window_title_names_the_ports(monkeypatch) -> None:
    import tkinter.messagebox as messagebox

    shown: list[str] = []
    monkeypatch.setattr(messagebox, "showerror", lambda title, message, parent=None: shown.append(title))
    monkeypatch.setattr(shutdown, "hard_exit", lambda api=None: None)
    from frontend import boot_report, error_log

    monkeypatch.setattr(error_log, "record_error", lambda *a, **k: None)
    monkeypatch.setattr(boot_report, "mark", lambda *a, **k: None)

    class _Root:
        def destroy(self) -> None:
            pass

    shutdown.fatal_error_and_exit(_refused("fr"), tk_root=_Root())
    shutdown.fatal_error_and_exit(ValueError("boom"), tk_root=_Root())
    assert shown == [shutdown.PORTS_BLOCKED_TITLE, "UEFN Ducky"]
