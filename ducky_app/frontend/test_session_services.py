"""Both app starts share one list of session services; a failing service is logged."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from frontend import session_services

_FRONTEND = Path(__file__).resolve().parent


def _calls(path: Path, name: str) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == name or getattr(node.func, "attr", None) == name)
    )


def test_both_start_paths_call_start_session_services() -> None:
    window = _FRONTEND / "ui_web" / "webview_app.py"
    backend = _FRONTEND / "native_host" / "headless.py"
    assert _calls(window, "start_session_services") == 1
    assert _calls(backend, "start_session_services") == 1
    assert _calls(backend, "_start_session_services") == 1
    # no hand-kept copy of the list on the launcher path
    text = backend.read_text(encoding="utf-8")
    for name in ("update_swap", "uninstaller_link", "native_crash", "mark_ready", "folder_access", "app_opened"):
        assert name not in text, name


def test_a_starter_that_fails_is_named_in_the_log(monkeypatch, caplog) -> None:
    import sys
    import types

    from frontend.ui_web import webview_app

    starters = webview_app.BACKGROUND_STARTERS
    assert len(starters) == 8
    for bad_name, bad_module, _ in starters:
        ran: list[str] = []
        for name, module, funcs in starters:
            fake = types.ModuleType(module)
            for func in funcs:
                def call(name=name, func=func) -> None:
                    if name == bad_name:
                        raise RuntimeError("no")
                    ran.append(f"{name}:{func}")
                setattr(fake, func, call)
            monkeypatch.setitem(sys.modules, module, fake)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=webview_app.__name__):
            webview_app.run_background_starters()
        assert {r.split(":")[0] for r in ran} == {n for n, _, _ in starters if n != bad_name}
        (record,) = caplog.records
        assert record.getMessage() == f"{bad_name} did not start" and record.exc_info
    assert any(n == "workflow scheduler" for n, _, _ in starters)


def test_a_failed_listener_config_write_is_logged(monkeypatch, tmp_path, caplog) -> None:
    from frontend import atomic_json, project_kind, settings

    def locked(*_a, **_k):
        raise PermissionError("config.json locked by UEFN")

    monkeypatch.setattr(settings, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "workspace_folders", lambda root: [root])
    monkeypatch.setattr(project_kind, "is_folder_project", lambda root: False)
    monkeypatch.setattr(atomic_json, "write_json_atomic", locked)
    monkeypatch.delenv("UEFN_VSCODE_WORKSPACE_FOLDERS", raising=False)
    monkeypatch.delenv("UEFN_DUCKY_PROJECT_ROOT", raising=False)
    with caplog.at_level(logging.WARNING, logger=settings.__name__):
        assert settings.apply_workspace_env(str(tmp_path / "Island")) is None  # still returns
    (record,) = caplog.records
    assert record.getMessage() == "listener config not written" and record.exc_info


def test_an_old_row_with_editor_tracking_mode_still_loads(monkeypatch, tmp_path) -> None:
    import json

    from frontend.settings import PanelSettings

    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path)
    (tmp_path / "panel_settings.json").write_text(
        json.dumps({"web_access": "on", "editor_tracking_mode": "basic"}), encoding="utf-8"
    )
    s = PanelSettings.load()
    assert s.web_access == "on" and not hasattr(s, "editor_tracking_mode")
    s.save()
    assert "editor_tracking_mode" not in json.loads((tmp_path / "panel_settings.json").read_text(encoding="utf-8"))


def test_the_schema_no_longer_lists_it() -> None:
    from frontend.settings_schema import FIELD_META, settings_schema

    assert "editor_tracking_mode" not in FIELD_META
    fields = settings_schema()["fields"]
    assert "editor_tracking_mode" not in fields
    assert "Editor capture depth" not in {f["label"] for f in fields.values()}


def test_each_service_failure_is_logged_and_the_rest_still_start(monkeypatch, caplog) -> None:
    names = [name for name, _ in session_services.SERVICES]
    for bad in names:
        ran: list[str] = []

        def make(name: str):
            def step(ctx) -> None:
                if name == bad:
                    raise RuntimeError("no")
                ran.append(name)
            return step

        monkeypatch.setattr(session_services, "SERVICES", tuple((n, make(n)) for n in names))
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=session_services.__name__):
            session_services.start_session_services(object(), {}, {}, None)
        assert ran == [n for n in names if n != bad]
        (record,) = caplog.records
        assert record.getMessage() == f"{bad} did not start" and record.exc_info
