"""What a chat building a plugin is told and given matches the app it builds for."""

from __future__ import annotations

import inspect

from backend.tools.panel.panel_ai_plugins import ducky_plugin_reference, plugin_api_markdown
from backend.uefn_plugins.compile import COMPILED_MIN_APP, _at_least
from backend.uefn_plugins.host import _PluginApi


def test_every_api_call_a_plugin_gets_is_documented_in_the_reference() -> None:
    names = [n for n in dir(_PluginApi) if not n.startswith("_")]
    undocumented = [n for n in names if not (inspect.getdoc(getattr(_PluginApi, n)) or "").strip()]
    assert undocumented == [], f"give these a docstring, ducky_plugin_reference shows it: {undocumented}"
    listed = plugin_api_markdown()
    assert all(f"`api.{n}" in listed for n in names)
    assert "`api.register_tts(" in ducky_plugin_reference()


def test_a_compiled_build_never_asks_for_an_app_older_than_the_safe_one() -> None:
    assert _at_least("", COMPILED_MIN_APP) == COMPILED_MIN_APP
    assert _at_least("1.2.100", COMPILED_MIN_APP) == COMPILED_MIN_APP
    assert _at_least("1.3.0", COMPILED_MIN_APP) == "1.3.0"
    assert _at_least("not a version", COMPILED_MIN_APP) == COMPILED_MIN_APP
