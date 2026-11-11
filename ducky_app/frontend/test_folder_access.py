"""Windows Controlled folder access blocking Documents: one question, Windows' admin prompt, restart."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from frontend import app_versions, folder_access as fa

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")


@pytest.fixture
def run(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    monkeypatch.setattr("frontend.bundle_root.is_packaged_runtime", lambda: True)
    monkeypatch.setattr(fa, "_state_path", lambda: tmp_path / fa.STATE_FILE)
    calls: dict = {"asked": [], "told": [], "allowed": [], "restarts": 0, "probes": 0}
    world = {"blocked": True, "mode": fa.BLOCK, "yes": True, "uac": True}

    def blocked() -> bool:
        calls["probes"] += 1
        return world["blocked"]

    def check() -> str:
        def restart() -> None:
            calls["restarts"] += 1

        return fa.check(
            blocked=blocked,
            mode=lambda: world["mode"],
            ask=lambda text: calls["asked"].append(text) or world["yes"],
            tell=calls["told"].append,
            allow=lambda paths: calls["allowed"].append(paths) or world["uac"],
            restart=restart,
        )

    return check, calls, world


def test_writable_documents_ask_nothing(run) -> None:
    check, calls, world = run
    world["blocked"] = False
    assert check() == "ok"
    assert calls["asked"] == [] and calls["allowed"] == []


def test_blocked_asks_once_allows_ducky_through_the_admin_prompt_and_restarts(run) -> None:
    check, calls, world = run
    assert check() == "allowed"
    assert calls["asked"] == [fa.ASK_TEXT]
    (paths,) = calls["allowed"]
    assert str(app_versions.versions_dir() / "*" / "UEFN-Ducky.exe") in paths  # every later version too
    assert calls["restarts"] == 1
    # Restarted and allowed: the next start writes fine and asks nothing.
    world["blocked"] = False
    assert check() == "ok" and len(calls["asked"]) == 1


def test_still_blocked_after_allowing_says_how_to_do_it_by_hand_once(run) -> None:
    check, calls, world = run
    assert check() == "allowed"
    assert check() == "still_blocked"  # an organization's policy can override it
    assert calls["told"] == [fa.STILL_BLOCKED_TEXT]
    probes = calls["probes"]
    assert check() == "asked" and calls["probes"] == probes  # no more probes this version


def test_no_is_remembered_for_this_version_and_asked_again_after_an_update(run, monkeypatch) -> None:
    check, calls, world = run
    world["yes"] = False
    assert check() == "no"
    probes = calls["probes"]
    assert check() == "asked"
    assert calls["probes"] == probes  # each probe also makes Windows show its own notice
    monkeypatch.setattr(fa, "__version__", "9.9.9")
    world["yes"] = True
    assert check() == "allowed" and len(calls["asked"]) == 2


def test_declining_windows_admin_prompt_keeps_ducky_running_and_says_how(run) -> None:
    check, calls, world = run
    world["uac"] = False
    assert check() == "failed"
    assert calls["told"] == [fa.NOT_ALLOWED_TEXT] and calls["restarts"] == 0


def test_a_refusal_that_is_not_controlled_folder_access_asks_nothing(run) -> None:
    check, calls, world = run
    world["mode"] = 0
    assert check() == "not_protection"
    assert calls["asked"] == []


def test_source_runs_and_test_copies_skip_it(run, monkeypatch) -> None:
    check, calls, _ = run
    monkeypatch.setattr("frontend.bundle_root.is_packaged_runtime", lambda: False)
    assert check() == "skipped"
    monkeypatch.setattr("frontend.bundle_root.is_packaged_runtime", lambda: True)
    monkeypatch.setenv("DUCKY_SANDBOX", "1")
    assert check() == "skipped"
    assert calls["probes"] == 0


def test_write_probe_leaves_nothing_and_reads_access_denied_as_blocked(tmp_path: Path, monkeypatch) -> None:
    documents = tmp_path / "Documents"
    documents.mkdir()
    assert fa.write_blocked(documents) is False
    assert list(documents.iterdir()) == []
    assert fa.write_blocked(tmp_path / "missing") is False  # not a block

    def refuse(*_a, **_k):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(fa.os, "open", refuse)
    assert fa.write_blocked(documents) is True


def test_admin_command_quotes_every_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(app_versions, "read_current", lambda: {"dir": str(tmp_path / "O'Brien" / "UEFN Ducky")})
    paths = fa.allowed_app_paths()
    assert str(tmp_path / "O'Brien" / "UEFN Ducky" / "UEFN-Ducky-Bridge.exe") in paths
    script = base64.b64decode(fa.allow_command(paths)).decode("utf-16-le")
    assert script.startswith("$ErrorActionPreference = 'Stop'; Add-MpPreference -ControlledFolderAccessAllowedApplications ")
    assert "'" + str(tmp_path / "O''Brien" / "UEFN Ducky" / "UEFN-Ducky.exe") + "'" in script
    assert "Set-MpPreference" not in script  # Set replaces the person's own allowed apps
