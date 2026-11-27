"""The website's ``remote_snapshot`` RPC: every chat, group and recent changeset in one read."""

from __future__ import annotations

import json
from typing import Any


def remote_snapshot() -> dict[str, Any]:
    """One disk read for the browser cache. Nothing is stored on the site."""
    from frontend.ui_web.panel_api import shared_panel_api

    api = shared_panel_api()
    convs = list(api.list_all_conversations())
    messages: dict[str, Any] = {}
    groups: dict[str, Any] = {}
    for row in convs:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "")
        if not cid:
            continue
        messages[cid] = api.load_messages(cid, full_results=True)
        if row.get("is_group"):
            groups[cid] = api.group_members(cid)
    ledger = list(api.list_changesets(limit=50))
    changesets: dict[str, Any] = {}
    for row in ledger:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("run_id") or "")
        if rid:
            changesets[rid] = api.get_changeset(rid)
    return json.loads(
        json.dumps(
            {
                "conversations": convs,
                "messages": messages,
                "groups": groups,
                "ledger": ledger,
                "changesets": changesets,
            },
            default=str,
        )
    )
