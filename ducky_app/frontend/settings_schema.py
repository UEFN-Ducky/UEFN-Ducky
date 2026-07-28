"""Machine-readable schema for :class:`~frontend.settings.PanelSettings`.

Powers the ``ducky_settings_*`` MCP tools: it names every setting, says which UI
tab owns it, whether an agent may write it with ``ducky_settings_set``, and (for
enums) the allowed values. Type and default are derived from the dataclass so
they can't drift; the human metadata below is hand-maintained.

``test_settings_schema.py`` asserts every dataclass field has a ``FIELD_META``
entry, so adding a field to ``PanelSettings`` without describing it here fails
the test rather than silently producing an undocumented setting.
"""

from __future__ import annotations

from typing import Any

from frontend.settings import PanelSettings


class FieldMeta:
    __slots__ = ("label", "tab", "settable", "enum", "restart_required", "description")

    def __init__(
        self,
        label: str,
        tab: str,
        *,
        settable: bool = False,
        enum: tuple[str, ...] | None = None,
        restart_required: bool = False,
        description: str = "",
    ) -> None:
        self.label = label
        self.tab = tab
        self.settable = settable
        self.enum = enum
        self.restart_required = restart_required
        self.description = description


# One entry per PanelSettings field. `settable` marks fields ducky_settings_set
# may write; structured fields (appearance, profiles, plugin lists) are False —
# they have dedicated tools. `port` and the legacy list are internal/not settable.
FIELD_META: dict[str, FieldMeta] = {
    "port": FieldMeta("Listener port", "internal", description="Fixed panel port; not user-editable."),
    "antigravity_config_path": FieldMeta(
        "Antigravity config path", "General", settable=True,
        description="Override path to Antigravity mcp_config.json.",
    ),
    "agent_provider": FieldMeta(
        "LLM provider", "LLMs", settable=True,
        description="Any Store-registered gateway provider id.",
    ),
    "agent_model": FieldMeta("Agent model id", "LLMs", settable=True),
    "default_model": FieldMeta(
        "Default model", "LLMs", settable=True,
        description='Qualified "backend:model_id" default for chats/duckies.',
    ),
    "uefn_project_root": FieldMeta(
        "UEFN project root", "General", settable=True,
        description="Prefer ducky_set_project, which also updates recents + deploy.",
    ),
    "verse_editor_enabled": FieldMeta("Verse editor enabled", "General", settable=True),
    "verse_diagnostics_cache_enabled": FieldMeta("Cache Verse diagnostics", "General", settable=True),
    "verse_diagnostics_auto_check": FieldMeta("Auto-check Verse on edit", "General", settable=True),
    "show_hidden_project_files": FieldMeta("Show hidden project files", "General", settable=True),
    "terminals_enabled": FieldMeta("Terminals enabled", "General", settable=False),
    "agent_max_turns": FieldMeta(
        "Max agent turns", "LLMs", settable=True,
        description="Tool-loop iterations per message (1-100).",
    ),
    "appearance_foundation": FieldMeta("Appearance foundation colors", "Appearance"),
    "appearance_overrides": FieldMeta("Appearance token overrides", "Appearance"),
    "appearance_status_overrides": FieldMeta("Appearance status colors", "Appearance"),
    "appearance_profiles": FieldMeta("Appearance profiles", "Appearance"),
    "appearance_active_profile_id": FieldMeta("Active appearance profile", "Appearance"),
    "appearance_effect_id": FieldMeta("Appearance effect", "Appearance"),
    "appearance_effects_enabled": FieldMeta("Appearance effects enabled", "Appearance"),
    "appearance_skin_id": FieldMeta("Appearance skin", "Appearance"),
    "appearance_sounds": FieldMeta("Appearance sounds", "Appearance"),
    "appearance_profile_patches": FieldMeta("Appearance profile patches", "Appearance"),
    "agent_profiles": FieldMeta("Ducky profiles", "Duckies"),
    "agent_profile_overrides": FieldMeta("Bundled profile overrides", "Duckies"),
    "hidden_bundled_agent_profile_ids": FieldMeta("Hidden bundled profiles", "Duckies"),
    "default_enabled_skills": FieldMeta("Legacy enabled skills", "internal"),
    "default_enabled_packs": FieldMeta("Default skill packs", "Skills & MCP"),
    "default_disabled_packs": FieldMeta("Disabled skill packs", "Skills & MCP"),
    "default_enabled_subskills": FieldMeta("Default subskills", "Skills & MCP"),
    "enabled_mcp_plugins": FieldMeta("Enabled MCP plugins", "Skills & MCP"),
    "enabled_uefn_plugins": FieldMeta("Enabled desktop plugins", "Store"),
    "trusted_local_uefn_plugins": FieldMeta("Trusted local plugins", "Store"),
    "disabled_builtin_toolsets": FieldMeta("Disabled built-in tool groups", "Skills & MCP"),
    "memory_auto_compress": FieldMeta(
        "Auto-compress chat context",
        "Memory",
        settable=True,
        description="When history grows past the thresholds, summarize older turns into a rolling cache.",
    ),
    "prompt_dedupe_exact_blocks": FieldMeta(
        "Strip exact duplicate paste blocks",
        "Memory",
        settable=True,
        description=(
            "Before send/resend, drop exact duplicate multi-line paste blocks (80+ chars). "
            "Short repeats like 'ok' are left alone. Not fuzzy / word-level."
        ),
    ),
    "memory_keep_last_messages": FieldMeta(
        "Keep last messages live",
        "Memory",
        settable=True,
        description="Number of recent messages always sent in full after compression (1-100).",
    ),
    "memory_compress_messages": FieldMeta(
        "Compress at message count",
        "Memory",
        settable=True,
        description="Auto-compress when the chat has at least this many messages.",
    ),
    "memory_compress_tokens": FieldMeta(
        "Compress at token estimate",
        "Memory",
        settable=True,
        description="Auto-compress when estimated context tokens reach this.",
    ),
    "memory_index_max_chars": FieldMeta(
        "Memory index max chars",
        "Memory",
        settable=True,
        description="Cap for the project-memory index injected into the system prompt.",
    ),
    "memory_summary_model": FieldMeta(
        "Summary model",
        "Memory",
        settable=True,
        description="Cheap API model for rolling summaries (empty = Voice / Default model).",
    ),
    "prompt_caching_enabled": FieldMeta(
        "Legacy provider cache markers fallback", "LLMs", settable=True
    ),
    "freeze_prompt_prefix": FieldMeta("Freeze prompt prefix (all providers)", "LLMs", settable=True),
    "anthropic_extended_cache_ttl": FieldMeta(
        "Legacy Anthropic 1h cache TTL fallback", "LLMs", settable=True
    ),
    "tool_result_format": FieldMeta(
        "Tool result format", "LLMs", settable=True, enum=("toon", "json"),
    ),
    "coding_agents": FieldMeta("BYOA coding agents", "LLMs"),
    "default_coding_agent": FieldMeta(
        "Default coding agent", "LLMs", settable=True,
        description="ducky or any Store-contributed coding-agent id.",
    ),
    "duckyos_base_url": FieldMeta("DuckyOS base URL", "Account", settable=True),
    "allow_settings_write": FieldMeta(
        "Allow agent settings writes", "General", settable=True,
        description="Master switch for ducky_settings_set.",
    ),
    "allow_agent_clicks": FieldMeta(
        "Allow agent clicks", "General", settable=True,
        description="Let ducky_ui_click click controls (default off = spotlight + user click).",
    ),
    "voice_enabled": FieldMeta(
        "Spoken replies", "Audio", settable=True,
        description="Auto-speak after normal (typed) chat replies. Live voice ignores this.",
    ),
    "voice_spoken_style": FieldMeta(
        "Spoken style", "Audio", settable=True, enum=("summary", "speak_along"),
    ),
    "voice_summary_model": FieldMeta(
        "Spoken summary model", "Audio", settable=True,
        description='Qualified cheap model for spoken summaries ("backend:model_id").',
    ),
    "voice_default_voice": FieldMeta("Default TTS voice", "Audio", settable=True),
    "voice_default_speed": FieldMeta("Default TTS speed", "Audio", settable=True),
    "voice_live_manual_send": FieldMeta(
        "Live voice manual send", "Audio", settable=True,
        description="In live voice, wait for Send instead of auto-sending when you pause.",
    ),
    "voice_process_talk": FieldMeta(
        "Process talk", "Audio", settable=True,
        description="How much live voice narrates tools/thinking (0 = mute, 1 = full).",
    ),
    "mic_permission": FieldMeta(
        "Microphone permission", "Audio", settable=True, enum=("ask", "allow", "block"),
        description="App-level mic consent before getUserMedia.",
    ),
    "mic_device_id": FieldMeta("Microphone device id", "Audio", settable=True),
    "output_device_id": FieldMeta("Output device id", "Audio", settable=True),
    "tts_volume": FieldMeta("TTS volume", "Audio", settable=True, description="Spoken-reply volume 0..1."),
    "audio_muted": FieldMeta(
        "Mute all audio", "Audio", settable=True,
        description="Master mute for SFX and spoken replies.",
    ),
    "walkthrough_completed": FieldMeta(
        "Walkthrough completions", "General",
        description="Per-tour completion map for the product walkthrough service.",
    ),
}


def _type_name(annotation: Any) -> str:
    raw = str(annotation)
    if raw.startswith("bool"):
        return "bool"
    if raw.startswith("int"):
        return "int"
    if raw.startswith("str"):
        return "str"
    if raw.startswith("list") or raw.startswith("List"):
        return "list"
    if raw.startswith("dict") or raw.startswith("Dict"):
        return "dict"
    return "object"


def known_field_names() -> set[str]:
    return {f.name for f in PanelSettings.__dataclass_fields__.values()}  # type: ignore[attr-defined]


def missing_meta_fields() -> set[str]:
    """Dataclass fields lacking a FIELD_META entry (should be empty)."""
    return known_field_names() - set(FIELD_META)


def settings_schema() -> dict[str, Any]:
    """Full schema: one entry per field with type, default, and UI metadata."""
    defaults = PanelSettings()
    fields: dict[str, Any] = {}
    for name, dc_field in PanelSettings.__dataclass_fields__.items():  # type: ignore[attr-defined]
        meta = FIELD_META.get(name)
        entry: dict[str, Any] = {
            "type": _type_name(dc_field.type),
            "default": getattr(defaults, name),
            "label": meta.label if meta else name,
            "tab": meta.tab if meta else "unknown",
            "settable": bool(meta.settable) if meta else False,
            "restart_required": bool(meta.restart_required) if meta else False,
        }
        if meta and meta.enum:
            entry["enum"] = list(meta.enum)
        if meta and meta.description:
            entry["description"] = meta.description
        fields[name] = entry
    return {"fields": fields}


def settable_keys() -> set[str]:
    return {name for name, meta in FIELD_META.items() if meta.settable}
