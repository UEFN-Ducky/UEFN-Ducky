"""A gateway's accounts or keys changed: the model lists and every open view follow at once.

Nothing refreshed before. The model picker went on listing a gateway's API models after its
key was deleted (the refresh skipped a gateway with no key and left its old list in place),
and kept showing the old account after a switch or a sign-out until the app restarted.
"""

from __future__ import annotations

from typing import Any

EVENT = "gateway_accounts_changed"


def accounts_changed(api: Any, result: dict[str, Any], provider: str) -> dict[str, Any]:
    """Call with an accounts RPC's result. When it went through: coding-agent logins are
    probed again, the model lists are fetched again (a key that is gone takes its models
    with it), and each window is told to read its pickers and account tables again.
    Returns ``result`` unchanged; never raises."""
    if not result.get("ok"):
        return result
    try:
        from backend.agent.coding_agents.base import invalidate_detect_cache, kick_detect_refresh

        invalidate_detect_cache()
        kick_detect_refresh()
    except Exception:
        pass
    try:
        from frontend.ui_web import panel_api

        panel_api.kick_model_refresh()
    except Exception:
        pass
    try:
        api._push_panel({"type": EVENT, "provider": str(provider or "").strip().lower()})
    except Exception:
        pass
    return result
