"""UEFN Ducky Store pathway: search catalog, read items, install/update/remove.

Thin wrappers over :mod:`frontend.duckyos_account` + desktop plugin / skill install.
Available to the in-app agent and every IDE connected to ``uefn-ducky``.
"""

from __future__ import annotations

from typing import Any

from backend.json_util import tool_json
from backend.server import mcp

# Drop from MCP payloads — base64 icons blow up context.
_STRIP_KEYS = frozenset({"icon_data_url", "icon_mime", "has_icon"})


def _notify_plugins() -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "uefn_plugins_changed"})
    except Exception:
        pass


def _notify_skills() -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "skills_changed"})
    except Exception:
        pass


def _slim_item(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in _STRIP_KEYS}


def _fetch_catalog() -> dict[str, Any]:
    from frontend.duckyos_account import DuckyOSAccountError, store_catalog

    try:
        return store_catalog()
    except DuckyOSAccountError as exc:
        return {"ok": False, "error": exc.message, "code": exc.code, "items": []}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "error", "items": []}


def filter_catalog_items(
    items: list[dict[str, Any]],
    *,
    query: str = "",
    category: str = "",
    kind: str = "",
    state: str = "",
) -> list[dict[str, Any]]:
    """Filter catalog rows (pure — used by tools + self-check)."""
    q = (query or "").strip().lower()
    cat = (category or "").strip().lower()
    kind_f = (kind or "").strip().lower()
    state_f = (state or "").strip().lower()
    out: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item_kind = str(raw.get("kind") or "").lower()
        if kind_f and item_kind != kind_f:
            continue
        if state_f and str(raw.get("state") or "").lower() != state_f:
            continue
        cats = [str(c).lower() for c in (raw.get("categories") or [])]
        primary = str(raw.get("category") or "").lower()
        if cat and cat not in cats and cat != primary:
            continue
        if q:
            hay = " ".join(
                [
                    str(raw.get("slug") or ""),
                    str(raw.get("name") or ""),
                    str(raw.get("description") or ""),
                    " ".join(str(t) for t in (raw.get("tags") or [])),
                    " ".join(cats),
                ]
            ).lower()
            if q not in hay:
                continue
        out.append(_slim_item(raw))
    return out


def _categories_from_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for raw in items:
        if not isinstance(raw, dict):
            continue
        cats = [str(c).strip().lower() for c in (raw.get("categories") or []) if str(c).strip()]
        if not cats:
            primary = str(raw.get("category") or raw.get("kind") or "").strip().lower()
            if primary:
                cats = [primary]
        for c in cats:
            counts[c] = counts.get(c, 0) + 1
    return [{"id": k, "count": counts[k]} for k in sorted(counts)]


@mcp.tool()
def ducky_store_categories(pretty: bool = False) -> str:
    """List UEFN Ducky Store browse categories with item counts.

    Returns {ok, categories: [{id, count}]}. Anonymous catalog (published items only).
    """
    cat = _fetch_catalog()
    if cat.get("ok") is False:
        return tool_json(cat, pretty=pretty)
    items = cat.get("items") if isinstance(cat.get("items"), list) else []
    return tool_json({"ok": True, "categories": _categories_from_items(items)}, pretty=pretty)


@mcp.tool()
def ducky_store_search(
    query: str = "",
    category: str = "",
    kind: str = "",
    state: str = "",
    limit: int = 40,
    pretty: bool = False,
) -> str:
    """Search the UEFN Ducky Store catalog (plugins + skills).

    Filters (all optional):
    - query: substring match on slug/name/description/tags
    - category: e.g. plugins, skills, themes, 3d
    - kind: plugin | skill
    - state: available | installed | update

    Returns slim items (no icon payloads): slug, name, description, kind, categories,
    tags, latest_version, installed_version, state, enabled, paid, contributes_summary.
    """
    cat = _fetch_catalog()
    if cat.get("ok") is False:
        return tool_json(cat, pretty=pretty)
    items = cat.get("items") if isinstance(cat.get("items"), list) else []
    filtered = filter_catalog_items(
        items, query=query, category=category, kind=kind, state=state
    )
    lim = max(1, min(int(limit or 40), 200))
    return tool_json(
        {
            "ok": True,
            "count": len(filtered),
            "returned": min(len(filtered), lim),
            "items": filtered[:lim],
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_store_get(slug: str, pretty: bool = False) -> str:
    """Read one Store item (or local-only installed plugin) by slug.

    Use after search before install/update. Returns the slim catalog row or error.
    """
    sid = (slug or "").strip()
    if not sid:
        return tool_json({"ok": False, "error": "slug required"}, pretty=pretty)
    cat = _fetch_catalog()
    if cat.get("ok") is False:
        return tool_json(cat, pretty=pretty)
    for raw in cat.get("items") if isinstance(cat.get("items"), list) else []:
        if isinstance(raw, dict) and str(raw.get("slug") or "") == sid:
            return tool_json({"ok": True, "item": _slim_item(raw)}, pretty=pretty)
    return tool_json({"ok": False, "error": f"not found: {sid}"}, pretty=pretty)


@mcp.tool()
def ducky_store_install(
    slug: str,
    version: str = "",
    pretty: bool = False,
) -> str:
    """Install a Store plugin or skill by slug (download + AppData install).

    Free items work anonymously. Paid items need DuckyOS sign-in + purchase.
    New plugins with default_enabled are turned on automatically after install.
    For an already-installed pack with a newer version, prefer ducky_store_update.
    """
    from frontend.duckyos_account import DuckyOSAccountError, store_download_and_install

    sid = (slug or "").strip()
    if not sid:
        return tool_json({"ok": False, "error": "slug required"}, pretty=pretty)
    try:
        result = store_download_and_install(
            sid,
            version=str(version or "").strip() or None,
            replace=True,
            is_update=False,
        )
    except DuckyOSAccountError as exc:
        return tool_json({"ok": False, "error": exc.message, "code": exc.code}, pretty=pretty)
    except Exception as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if result.get("ok") and result.get("kind") == "plugin":
        # Belt-and-suspenders: import_plugin_from_bytes already enables
        # default_enabled plugins, but older/cached builds may not.
        imp = result.get("import") if isinstance(result.get("import"), dict) else {}
        if not bool(imp.get("enabled")):
            try:
                from backend.uefn_plugins.store import (
                    load_plugin_manifest,
                    set_uefn_plugin_enabled,
                )

                manifest = load_plugin_manifest(sid) or {}
                if bool(manifest.get("default_enabled", True)):
                    en = set_uefn_plugin_enabled(sid, True)
                    if isinstance(result, dict):
                        result = {**result, "enabled": bool(en.get("ok"))}
            except Exception:
                pass
        _notify_plugins()
    elif result.get("ok") and result.get("kind") == "skill":
        _notify_skills()
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_store_update(slug: str, version: str = "", pretty: bool = False) -> str:
    """Update an already-installed Store plugin or skill to the latest (or given) version.

    Same download path as install, but marks the request as an update (install_count).
    """
    from frontend.duckyos_account import DuckyOSAccountError, store_download_and_install

    sid = (slug or "").strip()
    if not sid:
        return tool_json({"ok": False, "error": "slug required"}, pretty=pretty)
    try:
        result = store_download_and_install(
            sid,
            version=str(version or "").strip() or None,
            replace=True,
            is_update=True,
        )
    except DuckyOSAccountError as exc:
        return tool_json({"ok": False, "error": exc.message, "code": exc.code}, pretty=pretty)
    except Exception as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if result.get("ok") and result.get("kind") == "plugin":
        _notify_plugins()
    elif result.get("ok") and result.get("kind") == "skill":
        _notify_skills()
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_store_set_enabled(
    slug: str,
    enabled: bool,
    trust_local: bool = False,
    pretty: bool = False,
) -> str:
    """Enable or disable an installed desktop plugin (Settings → Store gate).

    Local / AI-made plugins may return needs_trust=true. Agents cannot pass
    trust_local=true for source=ai — the user must confirm in Settings → Store.
    Skills are not enable-gated here — use ducky_skills_set_pack.
    """
    from backend.uefn_plugins.store import load_plugin_manifest, set_uefn_plugin_enabled

    sid = (slug or "").strip()
    if not sid:
        return tool_json({"ok": False, "error": "slug required"}, pretty=pretty)
    # Agents must not auto-trust AI-made plugins — UI confirm only.
    effective_trust = bool(trust_local)
    if effective_trust:
        man = load_plugin_manifest(sid) or {}
        if str(man.get("source") or "") == "ai":
            effective_trust = False
    try:
        result = set_uefn_plugin_enabled(sid, bool(enabled), trust_local=effective_trust)
    except FileNotFoundError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    except Exception as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if result.get("ok"):
        _notify_plugins()
    elif result.get("needs_trust"):
        try:
            from frontend.ui_web.agent_modes import push_ui_event

            push_ui_event(
                {
                    "type": "uefn_plugin_trust_request",
                    "plugin_id": str(result.get("plugin_id") or sid),
                    "source": str(result.get("source") or ""),
                    "detail": str(result.get("error") or ""),
                }
            )
        except Exception:
            pass
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_store_remove(
    slug: str,
    confirm: bool = False,
    erase_data: bool = False,
    pretty: bool = False,
) -> str:
    """Uninstall a Store/local desktop plugin or standalone skill pack.

    Requires confirm=true. Plugin-owned skills are removed with their plugin —
    do not delete those via skill APIs. erase_data clears plugin secret_keys when
    uninstalling a plugin.
    """
    sid = (slug or "").strip()
    if not sid:
        return tool_json({"ok": False, "error": "slug required"}, pretty=pretty)
    if not confirm:
        return tool_json(
            {
                "ok": False,
                "error": "confirm=true required to uninstall",
                "slug": sid,
            },
            pretty=pretty,
        )

    # Prefer plugin uninstall when a plugin folder exists.
    try:
        from backend.uefn_plugins.store import load_plugin_manifest, uninstall_uefn_plugin

        if load_plugin_manifest(sid) is not None:
            result = uninstall_uefn_plugin(sid, erase_data=bool(erase_data))
            if result.get("ok"):
                _notify_plugins()
            return tool_json(result, pretty=pretty)
    except FileNotFoundError:
        pass
    except Exception as exc:
        return tool_json({"ok": False, "error": str(exc), "slug": sid}, pretty=pretty)

    try:
        from backend.skill import delete_skill_pack

        ok = delete_skill_pack(sid)
        if ok:
            _notify_skills()
            return tool_json({"ok": True, "slug": sid, "kind": "skill"}, pretty=pretty)
        return tool_json(
            {"ok": False, "error": f"not installed as plugin or skill: {sid}"},
            pretty=pretty,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc), "slug": sid}, pretty=pretty)
    except Exception as exc:
        return tool_json({"ok": False, "error": str(exc), "slug": sid}, pretty=pretty)


def _self_check() -> None:
    items = [
        {
            "slug": "blender",
            "name": "Blender",
            "description": "3D modeling",
            "kind": "plugin",
            "category": "plugins",
            "categories": ["plugins", "3d"],
            "tags": ["mesh"],
            "state": "available",
            "icon_data_url": "data:image/png;base64,AAA",
        },
        {
            "slug": "verse-tips",
            "name": "Verse Tips",
            "description": "skill pack",
            "kind": "skill",
            "categories": ["skills"],
            "state": "installed",
        },
    ]
    hit = filter_catalog_items(items, query="blender")
    assert len(hit) == 1 and hit[0]["slug"] == "blender"
    assert "icon_data_url" not in hit[0]
    assert len(filter_catalog_items(items, kind="skill")) == 1
    assert len(filter_catalog_items(items, category="3d")) == 1
    assert len(filter_catalog_items(items, state="update")) == 0
    cats = _categories_from_items(items)
    ids = {c["id"] for c in cats}
    assert "plugins" in ids and "skills" in ids and "3d" in ids
    print("panel_store.py self-check ok")


if __name__ == "__main__":
    _self_check()
