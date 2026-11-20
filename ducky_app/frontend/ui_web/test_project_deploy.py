"""A deploy that keeps failing the same way is one error row, from whichever path tried it."""

from __future__ import annotations

import pytest

from frontend.ui_web import project_deploy


@pytest.fixture
def rows(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(project_deploy, "_reported", {})
    monkeypatch.setattr(project_deploy, "record_error", lambda source, msg: seen.append(msg))
    monkeypatch.setattr(project_deploy, "_safe_normalize", lambda path: path.rstrip("/\\").lower())
    return seen


def _denied(name: str) -> PermissionError:
    return PermissionError(13, "Access is denied", name)


def test_the_same_failure_from_every_path_is_one_row(rows: list[str]) -> None:
    """Nov 19 2026: a 1.2.507 PC sent 14 deploy rows; switch / Settings / button logged every try."""
    for name in ("init_unreal.py", "boot.py", "listener.py"):  # a new file named each try
        project_deploy.report_deploy_failure("C:/Isl/A", _denied(name), alert=False)
    project_deploy.report_deploy_failure("c:/isl/a/", _denied("x"), alert=False)  # same island, other spelling
    assert len(rows) == 1


def test_a_new_error_or_a_working_deploy_reports_again(rows: list[str]) -> None:
    project_deploy.report_deploy_failure("C:/Isl/A", _denied("a"), alert=False)
    project_deploy.report_deploy_failure("C:/Isl/A", FileNotFoundError(2, "gone"), alert=False)
    assert len(rows) == 2
    project_deploy.deploy_worked("C:/Isl/A")
    project_deploy.report_deploy_failure("C:/Isl/A", FileNotFoundError(2, "gone"), alert=False)
    assert len(rows) == 3
    project_deploy.report_deploy_failure("C:/Isl/B", FileNotFoundError(2, "gone"), alert=False)
    assert len(rows) == 4
