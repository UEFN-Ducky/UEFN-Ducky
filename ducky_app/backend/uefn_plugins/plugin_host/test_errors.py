"""Load failures: recorded per plugin, scrubbed, and listed by label."""

from __future__ import annotations

from backend.uefn_plugins.plugin_host import errors, lifecycle, state


def test_a_recorded_failure_is_listed_and_scrubbed(monkeypatch) -> None:
    monkeypatch.setattr(errors, "_plugin_label", lambda pid: "Err Probe")
    try:
        errors._record_plugin_load_error("err-probe", RuntimeError("boom"))
        assert state._LOAD_FAILURES["err-probe"] == "RuntimeError: boom"
        assert errors.plugin_load_failures()["Err Probe"] == "RuntimeError: boom"
        errors._record_plugin_load_error("err-probe", "Restart to finish updating")
        assert state._LOAD_FAILURES["err-probe"] == "Restart to finish updating"
    finally:
        state._LOAD_FAILURES.pop("err-probe", None)


def test_tried_and_not_loaded_counts_failures_and_pauses() -> None:
    state._LOAD_FAILURES["err-failed"] = "x"
    lifecycle._PAUSED_PRIVATE_PLUGINS["err-paused"] = "space"
    try:
        missing = errors.tried_and_not_loaded()
        assert {"err-failed", "err-paused"} <= missing
    finally:
        state._LOAD_FAILURES.pop("err-failed", None)
        lifecycle._PAUSED_PRIVATE_PLUGINS.pop("err-paused", None)


def test_label_falls_back_to_the_id() -> None:
    assert errors._plugin_label("no-such-plugin-probe") == "no-such-plugin-probe"
