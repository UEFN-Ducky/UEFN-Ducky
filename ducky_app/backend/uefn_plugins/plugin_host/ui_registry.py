"""What loaded plugins contribute to the UI: Settings tabs, panels, header, references, gateways."""

from __future__ import annotations

import re
from typing import Any, Mapping

from backend.uefn_plugins.store import get_enabled_plugin_ids


def _merged_ide_hookups(enabled_set: set[str]) -> list[dict[str, Any]]:
    """plugin.json ide.hookups + register() bindings (register wins on kind clash)."""
    by_kind: dict[str, dict[str, Any]] = {}
    for row in _CONTRIBUTIONS.get("ide_hookups") or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "").strip().lower().replace("-", "_")
        pid = str(row.get("plugin_id") or "")
        if not kind or pid not in enabled_set:
            continue
        by_kind[kind] = {
            "kind": kind,
            "plugin_id": pid,
            "label": str(row.get("label") or kind).strip() or kind,
        }
    for kind, row in _IDE_HOOKUPS.items():
        pid = str(row.get("plugin_id") or "")
        if pid not in enabled_set:
            continue
        by_kind[kind] = {
            "kind": kind,
            "plugin_id": pid,
            "label": str(row.get("label") or kind).strip() or kind,
        }
    return sorted(by_kind.values(), key=lambda p: str(p.get("kind") or ""))


def normalize_chat_reference(row: Mapping[str, Any], plugin_id: str) -> dict[str, Any] | None:
    """Drop a composer reference whose href is not one of the tab schemes."""
    if not isinstance(row, Mapping):
        return None
    trigger = str(row.get("trigger") or "").strip()
    if trigger not in ("@", "/"):
        return None
    ref_id = str(row.get("id") or "").strip()
    label = str(row.get("label") or "").strip()
    href = str(row.get("href") or "").strip()
    if not ref_id or not label or not re.fullmatch(
        r"(ducky|profile|skill|subskill|mcp|plugin):[A-Za-z0-9_./:-]+", href
    ):
        return None
    out: dict[str, Any] = {
        "trigger": trigger,
        "id": ref_id,
        "label": label,
        "group": str(row.get("group") or "Plugins").strip() or "Plugins",
        "href": href,
        "plugin_id": plugin_id,
    }
    description = str(row.get("description") or "").strip()
    if description:
        out["description"] = description
    return out


def _dedupe_contrib_rows(
    rows: list[Any], *, key_fields: tuple[str, ...] = ("id",)
) -> list[Any]:
    """Keep first row per (plugin_id, …keys). Guards raced double-load after Store update."""
    seen: set[tuple[str, ...]] = set()
    out: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("plugin_id") or "").strip().lower()
        parts = tuple(str(row.get(f) or "").strip() for f in key_fields)
        key = (pid, *parts)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _plugin_icon_url(plugin_id: str) -> str:
    try:
        from backend.uefn_plugins.store import plugin_icon_data_url

        return plugin_icon_data_url(plugin_id) or ""
    except Exception:
        return ""


def _provider_shows_thinking(row: dict[str, Any]) -> bool:
    factory = _LLM_PROVIDER_FACTORIES.get(str(row.get("id") or "")) or {}
    if "shows_thinking_effort" in factory:
        return bool(factory.get("shows_thinking_effort"))
    if "shows_thinking_effort" in row:
        return bool(row.get("shows_thinking_effort"))
    return True


def image_generators() -> list[dict[str, Any]]:
    """Text to Image backends the loaded plugins declare (contributes.automations.image_generators)."""
    enabled = set(get_enabled_plugin_ids())
    with _LOCK:
        rows = _dedupe_contrib_rows(_CONTRIBUTIONS["automations_image_generators"])
    return [dict(row) for row in rows if row.get("plugin_id") in enabled]


def media_backends(node_type: str) -> list[dict[str, Any]]:
    """Backends the loaded plugins add to one image / 3D node (contributes.automations.media_backends)."""
    enabled = set(get_enabled_plugin_ids())
    with _LOCK:
        rows = _dedupe_contrib_rows(_CONTRIBUTIONS["automations_media_backends"], key_fields=("node", "id"))
    return [dict(row) for row in rows if row.get("plugin_id") in enabled and row.get("node") == node_type]


def image_generator_tools() -> set[str]:
    """Tool names of every declared image generator (no settings read: runs per tool call)."""
    with _LOCK:
        rows = list(_CONTRIBUTIONS["automations_image_generators"])
    return {str(row.get("tool") or "") for row in rows if isinstance(row, dict)} - {""}


def get_ui_contributions() -> dict[str, Any]:
    """Snapshot contrib registry for Settings UI — does not wait on register()."""
    enabled = list(get_enabled_plugin_ids())
    enabled_set = set(enabled)
    with _LOCK:
        out = {
            "settings_tabs": _dedupe_contrib_rows(_CONTRIBUTIONS["settings_tabs"]),
            "settings_sections": _dedupe_contrib_rows(_CONTRIBUTIONS["settings_sections"]),
            "dock_panels": _dedupe_contrib_rows(_CONTRIBUTIONS["dock_panels"]),
            "editor_kinds": _dedupe_contrib_rows(
                _CONTRIBUTIONS["editor_kinds"], key_fields=("kind",)
            ),
            "header_buttons": _dedupe_contrib_rows(_CONTRIBUTIONS["header_buttons"]),
            "header_menus": _dedupe_contrib_rows(_CONTRIBUTIONS["header_menus"]),
            "ui_panels": _dedupe_contrib_rows(_CONTRIBUTIONS["ui_panels"]),
            "chat_references": _dedupe_contrib_rows(
                _CONTRIBUTIONS["chat_references"], key_fields=("trigger", "id")
            ),
            "shell_boots": _dedupe_contrib_rows(
                _CONTRIBUTIONS["shell_boots"], key_fields=("entry",)
            ),
            "appearance_profiles": _dedupe_contrib_rows(_CONTRIBUTIONS["appearance_profiles"]),
            "appearance_css": _dedupe_contrib_rows(
                _CONTRIBUTIONS["appearance_css"], key_fields=("entry",)
            ),
            "appearance_effects": _dedupe_contrib_rows(_CONTRIBUTIONS["appearance_effects"]),
            "appearance_skins": _dedupe_contrib_rows(_CONTRIBUTIONS["appearance_skins"]),
            "sounds": _dedupe_contrib_rows(_CONTRIBUTIONS["sounds"]),
            "hooks": _dedupe_contrib_rows(_CONTRIBUTIONS["hooks"]),
            "automations_nodes": _dedupe_contrib_rows(_CONTRIBUTIONS["automations_nodes"]),
            "automations_triggers": _dedupe_contrib_rows(_CONTRIBUTIONS["automations_triggers"]),
            "automations_templates": _dedupe_contrib_rows(_CONTRIBUTIONS["automations_templates"]),
            "automations_image_generators": _dedupe_contrib_rows(_CONTRIBUTIONS["automations_image_generators"]),
            "verse_templates": sorted(
                _dedupe_contrib_rows(_CONTRIBUTIONS["verse_templates"]),
                key=lambda t: (int(t.get("order") or 100), str(t.get("name") or "")),
            ),
            "tts_voices": _dedupe_contrib_rows(_CONTRIBUTIONS["tts_voices"]),
            # Listers persist across reloads (like _REGISTERED); only surface the
            # ones whose plugin is currently enabled so the picker never polls a
            # disabled plugin.
            "tts_voice_plugins": sorted(p for p in _TTS_VOICE_LISTERS if p in enabled_set),
            "llm_providers": sorted(
                [
                    {
                        **row,
                        "shows_thinking_effort": _provider_shows_thinking(row),
                        "icon_data_url": _plugin_icon_url(str(row.get("plugin_id") or "")),
                    }
                    for row in _dedupe_contrib_rows(_CONTRIBUTIONS["llm_providers"])
                ],
                key=lambda p: (int(p.get("order") or 100), str(p.get("label") or "")),
            ),
            "llm_coding_agents": sorted(
                [
                    {
                        **row,
                        "shows_thinking_effort": bool(
                            (
                                _CODING_AGENT_FACTORIES.get(
                                    str(row.get("id") or "").replace("-", "_")
                                )
                                or {}
                            ).get("shows_thinking_effort")
                            or callable(
                                (
                                    _CODING_AGENT_FACTORIES.get(
                                        str(row.get("id") or "").replace("-", "_")
                                    )
                                    or {}
                                ).get("thinking_env")
                            )
                        ),
                    }
                    for row in _dedupe_contrib_rows(_CONTRIBUTIONS["llm_coding_agents"])
                ],
                key=lambda p: (int(p.get("order") or 100), str(p.get("label") or "")),
            ),
            "ide_hookups": _merged_ide_hookups(enabled_set),
            "agent_tools": dict(_CONTRIBUTIONS["agent_tools"]),
            "walkthroughs": list(_CONTRIBUTIONS.get("walkthroughs") or []),
            "enabled_ids": enabled,
        }
    # Every gateway list reads this: a plugin's gateway shows only while the plugin says so,
    # and says how its turns are paid (billing) so the UI never names a gateway.
    out["llm_providers"] = [
        {**row, **({"billing": billing} if (billing := provider_option(row.get("id"), "billing")) else {})}
        for row in out["llm_providers"]
        if provider_visible(row.get("id"))
    ]
    return out


def get_project_sources() -> list[dict[str, Any]]:
    """Projects-window tabs from enabled plugins (``contributes['project.sources']``)."""
    with _LOCK:
        rows = list(_CONTRIBUTIONS.get("project_sources") or [])
    return [r for r in rows if is_plugin_enabled(str(r.get("plugin_id") or ""))]


def get_reference_sources() -> list[dict[str, Any]]:
    """Live composer/search rows from enabled plugins (``contributes.chat.reference_sources``)."""
    with _LOCK:
        rows = list(_CONTRIBUTIONS.get("reference_sources") or [])
    return [r for r in rows if is_plugin_enabled(str(r.get("plugin_id") or ""))]


def get_contributions() -> dict[str, Any]:
    ensure_plugins_loaded()  # also repairs missing register() backends
    return get_ui_contributions()


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.boot import ensure_plugins_loaded  # noqa: E402
from backend.uefn_plugins.plugin_host.gateways import provider_option, provider_visible  # noqa: E402
from backend.uefn_plugins.plugin_host.lifecycle import is_plugin_enabled  # noqa: E402
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _CODING_AGENT_FACTORIES,
    _CONTRIBUTIONS,
    _IDE_HOOKUPS,
    _LLM_PROVIDER_FACTORIES,
    _LOCK,
    _TTS_VOICE_LISTERS,
)
