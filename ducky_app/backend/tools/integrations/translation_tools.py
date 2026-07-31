"""MCP tools for the Translation UEFN desktop plugin.

Registered by the Translation plugin ``register()`` — not imported from tools/__init__.py.
Agents call these directly; the Languages UI uses the same ``translation_service``.
"""

from __future__ import annotations


from backend.util.json_util import tool_json
from backend.server import mcp
from backend.tools.integrations.translation_service import PLUGIN_ID, run_translate_ui_batch


def _require_plugin() -> None:
    from backend.uefn_plugins.host import is_plugin_enabled, uefn_agent_tools_allowed

    if not is_plugin_enabled(PLUGIN_ID):
        raise ValueError("Translation plugin is disabled — enable it in Settings → Store")
    if not uefn_agent_tools_allowed(PLUGIN_ID):
        raise ValueError(
            "Translation tools are off for this ducky — enable them under Tools & MCPs"
        )


@mcp.tool()
def translate_ui_batch(
    language: str,
    strings_json: str,
    model: str = "",
    write_cache: bool = True,
    pretty: bool = False,
) -> str:
    """Translate a batch of English UI chrome strings into ``language`` via the Translation plugin.

    Prefer this over hand-rolled LLM prompts. Pass a JSON object whose keys (and values)
    are the English strings, e.g. ``{"Support":"Support","Store":"Store"}``. Max 40 keys
    per call — split larger jobs. Results are merged into the plugin language cache when
    ``write_cache`` is true so the live UI can pick them up.

    Args:
        language: Target language name (e.g. Spanish, Français, ja). Not English.
        strings_json: JSON object of English UI strings to translate (keys = sources).
        model: Optional ``provider:model``; empty uses Languages / Default Model.
        write_cache: Merge translations into the plugin cache for this language.
        pretty: Pretty-print JSON response.
    """
    _require_plugin()
    result = run_translate_ui_batch(
        language,
        strings_json,
        model=model or "",
        write_cache=bool(write_cache),
    )
    return tool_json(result, pretty=pretty)


@mcp.tool()
def translate_ui_cache_get(language: str, pretty: bool = False) -> str:
    """Read the Translation plugin chrome catalog cache for a language (English→translated map)."""
    _require_plugin()
    from frontend.ui_web import plugin_host_api as pha

    lang = str(language or "").strip()
    if not lang:
        raise ValueError("language required")
    data = pha.cache_get(PLUGIN_ID, lang) or {}
    if not isinstance(data, dict):
        data = {}
    return tool_json({"ok": True, "language": lang, "catalog": data, "count": len(data)}, pretty=pretty)


@mcp.tool()
def translate_ui_cache_clear(language: str = "", pretty: bool = False) -> str:
    """Clear Translation plugin cache for one language, or all chrome catalogs when language is empty."""
    _require_plugin()
    from frontend.ui_web import plugin_host_api as pha

    lang = str(language or "").strip()
    pha.cache_clear(PLUGIN_ID, lang)
    return tool_json({"ok": True, "cleared": lang or "*"}, pretty=pretty)
