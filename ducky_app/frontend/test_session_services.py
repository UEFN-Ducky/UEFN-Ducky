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
