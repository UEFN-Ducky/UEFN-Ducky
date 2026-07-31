"""Language pathway.

UI translation is owned by the Translation desktop plugin (Settings → Languages),
not by core panel settings. There is no stable programmatic set-language backend
to wrap, so these tools guide the user to the Languages tab: they open it and
spotlight it rather than claim a silent change. Enable the Translation plugin in
Settings → Store first if the Languages tab isn't present.
"""

from __future__ import annotations

from backend.util.json_util import tool_json
from backend.panel.rpc import panel_rpc
from backend.server import mcp


@mcp.tool()
def ducky_i18n_list_languages(pretty: bool = False) -> str:
    """Open Settings → Languages so the user can see/add UI languages.

    UI language is managed by the Translation plugin, not a settable core setting.
    Opens the Languages tab and reports whether the panel was reachable.
    """
    nav = panel_rpc("navigate", {"route": "settings.languages"})
    return tool_json(
        {
            "opened": nav,
            "note": "UI languages are managed in Settings → Languages (Translation plugin). "
            "Enable it in Settings → Store if the tab is missing.",
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_i18n_set_language(lang: str = "", pretty: bool = False) -> str:
    """Guide the user to set the UI language (Settings → Languages).

    Opens the Languages tab and spotlights it. Language switching is a Translation-
    plugin action, so this points the user there rather than changing it silently.
    """
    nav = panel_rpc("navigate", {"route": "settings.languages"})
    spot = panel_rpc(
        "spotlight",
        {
            "target_id": "settings.tab.languages",
            "mode": "circle",
            "label": f"Pick {lang}".strip() or "Choose a language",
            "ttl_ms": 8000,
            "require_click": False,
        },
    )
    return tool_json(
        {
            "opened": nav,
            "spotlight": spot,
            "note": "Choose or add the language in Settings → Languages (Translation plugin).",
        },
        pretty=pretty,
    )
