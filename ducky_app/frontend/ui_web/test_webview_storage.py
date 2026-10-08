"""The panel's WebView2 keeps its local storage between launches."""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).with_name("webview_app.py")


def _start_keywords() -> dict[str, ast.expr]:
    tree = ast.parse(_APP.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "start"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "webview"
    ]
    assert len(calls) == 1, "expected exactly one webview.start(...) call"
    return {kw.arg: kw.value for kw in calls[0].keywords if kw.arg}


def test_webview_start_keeps_storage_between_launches() -> None:
    # pywebview defaults to private mode: WebView2 runs InPrivate (local storage in
    # memory only) and closing the window deletes the storage_path folder, so every
    # setting the panel saves in local storage was lost on each start.
    kw = _start_keywords()
    private = kw.get("private_mode")
    assert isinstance(private, ast.Constant) and private.value is False
    assert "storage_path" in kw


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
