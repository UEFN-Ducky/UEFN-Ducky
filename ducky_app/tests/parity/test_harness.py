"""The harness itself, and the first answer both backends share: an unknown method."""

from __future__ import annotations

import pytest

from parity_harness import backend_exe, check_same


def test_python_answers_in_the_panel_api_envelope(python) -> None:
    assert python.call("get_restore_tabs", "main") == (200, {"ok": True, "result": []})
    assert python.call("no_such_method") == (404, {"ok": False, "error": "method not allowed"})


def test_rust_backend_starts_and_reports_health(rust) -> None:
    status, body = rust.request("/__rs/health")
    assert status == 200 and body["ok"] is True and body["pid"] == rust.proc.pid


def test_an_unknown_method_gets_the_same_answer_from_both(same) -> None:
    assert same("no_such_method") == (404, {"ok": False, "error": "method not allowed"})


def test_a_different_answer_fails_and_shows_both(python) -> None:
    class Wrong:
        name = "rust"

        def call(self, method, *args):
            return 200, {"ok": True, "result": ["chat:1"]}

    with pytest.raises(AssertionError, match=r"get_restore_tabs\('main',\): rust .* != python "):
        check_same(python, Wrong(), "get_restore_tabs", "main")


def test_ignored_result_keys_do_not_count() -> None:
    class Fixed:
        def __init__(self, at):
            self.at = at

        def call(self, method, *args):
            return 200, {"ok": True, "result": {"name": "a", "at": self.at}}

    assert check_same(Fixed(1), Fixed(2), "m", ignore=("at",)) == (200, {"ok": True, "result": {"name": "a"}})
    with pytest.raises(AssertionError):
        check_same(Fixed(1), Fixed(2), "m")


def test_a_pinned_exe_that_is_missing_means_no_rust_backend(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DUCKY_BACKEND_EXE", str(tmp_path / "nope"))
    assert backend_exe() is None
