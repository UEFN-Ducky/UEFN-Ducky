"""The plugin checker's list of Appearance variables can't drift from the theme engine,
and the Ducky UI kit holds itself to the rules it teaches.

appearance_vars.py and critical.css are both generated from computeCssVars
(web/scripts/generate-appearance-vars.ts, generate-critical-css.ts, on every dev start
and build). If this fails, run ``npx tsx scripts/generate-appearance-vars.ts`` in
ducky_app/frontend/ui_web/web.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.uefn_plugins import appearance_vars, plugin_lint, ui_kit

_CRITICAL = Path(__file__).resolve().parents[2] / "frontend/ui_web/web/src/theme/styles/critical.css"


def test_the_allowed_variables_are_the_theme_engines() -> None:
    css = _CRITICAL.read_text(encoding="utf-8")
    assert set(re.findall(r"^\s*--([a-z0-9-]+)\s*:", css, re.M)) == set(appearance_vars.NAMES)


def test_the_ui_kit_uses_only_appearance_variables_and_no_color_literals(tmp_path: Path) -> None:
    used = set(re.findall(r"var\(\s*--([\w-]+)", ui_kit.CSS))
    assert used and used <= appearance_vars.NAMES, sorted(used - appearance_vars.NAMES)
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "ducky.css").write_text(ui_kit.CSS, encoding="utf-8")
    (tmp_path / "ui" / "ducky.js").write_text(ui_kit.JS, encoding="utf-8")
    assert plugin_lint.check_ui(tmp_path, {}) == []
    for part in ("dk-btn--primary", "dk-btn--secondary", "dk-btn--ghost", "dk-btn--danger", "dk-btn--icon",
                 "dk-input", "dk-select", "dk-check", "dk-tab", "dk-card", "dk-table", "dk-badge",
                 "dk-empty", "dk-loading", "dk-error", ":focus-visible"):
        assert part in ui_kit.CSS, part


def test_panel_pages_get_the_error_reporter_after_head_never_before_the_doctype() -> None:
    from backend.uefn_plugins.webview import with_panel_error_script

    script = ui_kit.PANEL_ERROR_SCRIPT.encode()
    page = b"<!doctype html><html><head><title>x</title></head><body></body></html>"
    out = with_panel_error_script(page)
    assert out.startswith(b"<!doctype html><html><head>" + script)
    bare = with_panel_error_script(b"<!DOCTYPE html><div>hi</div>")
    assert bare.startswith(b"<!DOCTYPE html>" + script)
    assert with_panel_error_script(b"<div>hi</div>") == script + b"<div>hi</div>"
    assert with_panel_error_script(b"<html><header>x</header></html>").startswith(b"<html>" + script)
