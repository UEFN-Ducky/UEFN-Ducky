"""One chat's own setup: create it (and the first-run welcome ducky), its ducky config,
skills, style, mute flag and files, the ducky catalog and custom Verse templates."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


def _conversation_row(conv: Any) -> dict[str, Any]:
    return {
        "id": conv.id,
        "title": conv.title,
        "ducky_style": conv.ducky_style,
        "ducky_name": conv.ducky_name or "",
        "profile_id": getattr(conv, "profile_id", None) or "",
        "ducky_personality": conv.ducky_personality,
        "tts_voice": getattr(conv, "tts_voice", None) or "",
        "tts_speed": float(getattr(conv, "tts_speed", 0) or 0.0),
        "thinking_effort": getattr(conv, "thinking_effort", None) or "",
        "file_path": conv.file_path,
        "model": conv.model or "",
        "provider": conv.provider or "",
        "coding_agent": getattr(conv, "coding_agent", None) or "ducky",
    }


def chat_tool_view(self, tool_names: list[str] | None = None, paths: list[str] | None = None) -> dict[str, Any]:
    """A tool card's plugin (name, panel to open) and links for the media files its
    result names, so the card plays them in the chat."""
    from backend.uefn_plugins.chat_view import chat_tool_view

    return chat_tool_view(tool_names, paths)


def create_conversation(
    self,
    folder_id: str,
    ducky_style: str | None = None,
    file_path: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, str]:
    conv = self._new_conversation(folder_id, ducky_style, file_path, config)
    _pa.notify_chats_changed(conv.id, conv.title, folder_id)
    return _conversation_row(conv)


def finish_first_run_setup(self) -> dict[str, Any]:
    """Finish the first-run setup and make the welcome ducky, whose first message offers
    the walkthroughs. Made after the setup saved the Default Model, so the chat starts on
    it. Once per AppData; every later call is ``created: False``."""
    import time

    from frontend.starter_llm_gateways import (
        WELCOME_DUCKY_TEXT,
        WELCOME_DUCKY_TITLE,
        WELCOME_DUCKY_TOURS,
        finish_setup,
        start_first_run_tour,
    )
    from frontend.ui_web.chats.saves import append_message

    try:
        if not finish_setup():
            return {"ok": True, "created": False}
        conv = self._new_conversation("", None, None, {"title": WELCOME_DUCKY_TITLE})
        append_message(
            conv,
            {
                "role": "assistant",
                "content": WELCOME_DUCKY_TEXT,
                "tours": list(WELCOME_DUCKY_TOURS),
                "ts": time.time(),
            },
        )
        start_first_run_tour(conv.id)
    except Exception as exc:
        return {"ok": False, "created": False, "error": str(exc)}
    # Announced only now, so the tab never opens on an empty chat.
    _pa.notify_chats_changed(conv.id, conv.title, "")
    return {"ok": True, "created": True, **_conversation_row(conv)}


def _new_conversation(
    self,
    folder_id: str,
    ducky_style: str | None = None,
    file_path: str | None = None,
    config: dict[str, Any] | None = None,
) -> Any:
    style = ducky_style if ducky_style is not None else _pa.default_bundled_style()
    settings = _pa.PanelSettings.load()
    cfg = config if isinstance(config, dict) else {}
    title = str(cfg.get("title") or "").strip()
    ducky_name = str(cfg.get("ducky_name") or "").strip()
    profile_id = str(cfg.get("profile_id") or "").strip()
    personality = str(cfg.get("ducky_personality") or "")
    tts_voice = str(cfg.get("tts_voice") or "").strip()
    tts_speed = float(cfg.get("tts_speed") or 0.0)
    thinking_effort = str(cfg.get("thinking_effort") or "").strip().lower()
    disabled_packs = cfg.get("disabled_packs")
    enabled_subskills = cfg.get("enabled_subskills")
    disabled_tool_ids = cfg.get("disabled_tool_ids")
    if isinstance(cfg.get("ducky_style"), str) and cfg.get("ducky_style").strip():
        style = str(cfg.get("ducky_style")).strip()

    from backend.agent.coding_agents.base import normalize_coding_agent
    from frontend.favorite_models import ResolveErr, ResolveOk, allow_create_without_model

    coding_agent = "ducky"
    resolved_model = ""
    resolved_provider = ""

    if "favorite_models" in cfg:
        # Profile-driven create: the profile's model, else the global default.
        # No model at all still creates — the composer prompts on send.
        result = allow_create_without_model(
            _pa.resolve_model_selection(cfg.get("favorite_models"), settings)
        )
        if isinstance(result, ResolveErr):
            raise ValueError(result.message)
        coding_agent = result.coding_agent
        resolved_model = result.model
        resolved_provider = result.provider
    else:
        explicit_agent = str(cfg.get("coding_agent") or "").strip()
        coding_agent = normalize_coding_agent(explicit_agent or "ducky")
        # Branded chats (e.g. Duck-Tac-Toe) pass coding_agent="ducky" so a
        # Cursor/Codex Default Model must not hijack the conversation.
        force_ducky = bool(explicit_agent) and coding_agent == "ducky"
        if coding_agent == "ducky":
            # Blank chat — seed the global Default Model (Settings → LLMs) so
            # the composer opens with a working model. No default (or an
            # unavailable one) leaves the model empty for a manual pick.
            result = _pa.resolve_model_selection(None, settings)
            if isinstance(result, ResolveOk):
                if force_ducky and result.coding_agent != "ducky":
                    api_pick = _pa._first_available_api_model()
                    if api_pick:
                        resolved_provider, resolved_model = api_pick
                    # else: leave model empty — keep coding_agent ducky
                else:
                    coding_agent = result.coding_agent
                    resolved_model = result.model
                    resolved_provider = result.provider

    explicit_agent = str(cfg.get("coding_agent") or "").strip()
    if explicit_agent and "favorite_models" in cfg:
        # Explicit coding_agent in config can only override when the model already
        # resolved to that agent (or ducky). Never invent a model for a forced agent.
        forced = normalize_coding_agent(explicit_agent)
        if forced != "ducky" and forced != coding_agent and not resolved_model:
            raise ValueError(
                f"Cannot force coding agent {forced!r} without an exact model selection."
            )
        if forced != "ducky":
            coding_agent = forced

    project_root = None
    target_slug = str(cfg.get("project_slug") or "").strip()
    if target_slug:
        from frontend.ui_web.chats.store import create_project_root

        project_root = create_project_root(target_slug, settings.uefn_project_root or "")

    conv = _pa.create_conversation(
        settings,
        folder_id,
        title=title,
        ducky_style=style,
        ducky_name=ducky_name,
        profile_id=profile_id,
        ducky_personality=personality,
        tts_voice=tts_voice,
        tts_speed=tts_speed,
        thinking_effort=thinking_effort,
        file_path=file_path or "",
        disabled_packs=disabled_packs if isinstance(disabled_packs, list) else None,
        enabled_subskills=enabled_subskills if isinstance(enabled_subskills, dict) else None,
        disabled_tool_ids=disabled_tool_ids if isinstance(disabled_tool_ids, list) else None,
        model=resolved_model,
        provider=resolved_provider,
        coding_agent=coding_agent,
        project_root=project_root,
    )
    return conv


def apply_ducky_config(self, conv_id: str, config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {"ok": False, "error": "config must be an object"}
    disabled_packs = config.get("disabled_packs")
    enabled_subskills = config.get("enabled_subskills")
    disabled_tool_ids = config.get("disabled_tool_ids")
    title = config.get("title")
    ducky_name = config.get("ducky_name")
    profile_id = config.get("profile_id")
    _pa.apply_ducky_config(
        conv_id,
        ducky_style=str(config.get("ducky_style") or _pa.default_bundled_style()),
        ducky_name=str(ducky_name) if ducky_name is not None else None,
        profile_id=str(profile_id) if profile_id is not None else None,
        ducky_personality=str(config.get("ducky_personality") or ""),
        tts_voice=str(config["tts_voice"]) if "tts_voice" in config else None,
        tts_speed=float(config["tts_speed"]) if "tts_speed" in config else None,
        thinking_effort=str(config["thinking_effort"]) if "thinking_effort" in config else None,
        title=str(title) if title is not None else None,
        disabled_packs=disabled_packs if isinstance(disabled_packs, list) else None,
        enabled_subskills=enabled_subskills if isinstance(enabled_subskills, dict) else None,
        disabled_tool_ids=disabled_tool_ids if isinstance(disabled_tool_ids, list) else None,
    )
    return {"ok": True}


def save_chat_file(self, conv_id: str, name: str = "", data_base64: str = "") -> dict[str, Any]:
    """A file attached to an answer on an agent's question card, saved in that chat."""
    import base64

    from frontend.ui_web.conversation_attachments import save_chat_file

    try:
        raw = base64.b64decode(str(data_base64 or "").split(",", 1)[-1], validate=True)
    except Exception:
        return {"ok": False, "error": "The file could not be read."}
    try:
        return save_chat_file(str(conv_id or ""), str(name or "file"), raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def set_muted(self, conv_id: str, muted: bool) -> dict[str, Any]:
    """Mute or unmute a chat or a group hub. Only stores the flag: its agent keeps running."""
    cid = (conv_id or "").strip()
    conv = _pa.load_conversation(cid)
    if conv is None:
        return {"ok": False, "error": "Conversation not found"}
    conv.muted = bool(muted)
    _pa.save_conversation(conv)
    _pa.notify_chats_changed(conv.id, conv.title, conv.folder_id, open_tab=False)
    return {"ok": True, "id": conv.id, "muted": conv.muted, "is_group": bool(getattr(conv, "is_group", False))}


def get_conversation_skills(self, conv_id: str) -> dict[str, Any]:
    from backend.skills.manifest import list_skill_packs
    from backend.skills.seeding import seed_skill_packs
    from backend.skills.selection import resolve_conversation_selection
    from backend.mcp_plugins.store import seed_mcp_plugins

    seed_skill_packs()
    seed_mcp_plugins()
    conv = _pa.load_conversation(conv_id)
    settings = _pa.PanelSettings.load()
    packs = list_skill_packs()
    if conv:
        sel = resolve_conversation_selection(conv, settings)
    else:
        from backend.skills.selection import default_selection_from_settings

        sel = default_selection_from_settings(settings)
    # For UI: packs with no stored allowlist show every toggleable root as on.
    enabled_subs = dict(sel.enabled_subskills)
    for pack in packs:
        pid = str(pack.get("id") or "")
        if not pid or pid in enabled_subs:
            continue
        if pid in sel.disabled_packs:
            continue
        toggleable = [
            str(s.get("id"))
            for s in (pack.get("subskills") or [])
            if isinstance(s, dict)
            and str(s.get("id") or "").strip()
            and not s.get("parent_id")
            and not s.get("always_on")
        ]
        enabled_subs[pid] = toggleable
    return {
        "disabled_packs": list(sel.disabled_packs),
        "enabled_packs": sel.enabled_packs,
        "enabled_subskills": enabled_subs,
        "enabled_skills": sel.enabled_packs,
        "packs": packs,
        "catalog": packs,
        "primary_filename": "uefn",
    }


def set_conversation_skills(self, conv_id: str, filenames: list[str]) -> dict[str, Any]:
    enabled = _pa.set_conversation_enabled_skills(conv_id, filenames)
    return {"ok": True, "enabled_skills": enabled, "enabled_packs": enabled}


def set_conversation_skill_selection(
    self,
    conv_id: str,
    enabled_packs: list[str],
    enabled_subskills: dict[str, list[str]],
) -> dict[str, Any]:
    packs, subs = _pa.set_conversation_skill_selection(conv_id, enabled_packs, enabled_subskills)
    return {
        "ok": True,
        "enabled_packs": packs,
        "enabled_subskills": subs,
        "enabled_skills": packs,
    }


def set_default_enabled_skills(self, filenames: list[str]) -> dict[str, Any]:
    return self.set_default_skill_selection(filenames, {})


def set_default_skill_selection(
    self,
    enabled_packs: list[str],
    enabled_subskills: dict[str, list[str]],
) -> dict[str, Any]:
    """Legacy API — clears the default deny-list (all packs available)."""
    del enabled_packs, enabled_subskills
    from backend.skills.selection import merge_selection

    settings = _pa.PanelSettings.load()
    sel = merge_selection(disabled_packs=[])
    settings = _pa.replace(
        settings,
        default_disabled_packs=[],
        default_enabled_packs=[],
        default_enabled_subskills={},
        default_enabled_skills=[],
    )
    settings.save()
    return {
        "ok": True,
        "default_disabled_packs": [],
        "default_enabled_packs": sel.enabled_packs,
        "default_enabled_subskills": {},
        "default_enabled_skills": sel.enabled_packs,
    }


def set_conversation_ducky_style(
    self,
    conv_id: str,
    ducky_style: str,
    ducky_personality: str | None = None,
) -> None:
    _pa.set_conversation_ducky_style(conv_id, ducky_style, ducky_personality=ducky_personality)


def list_ducky_catalog(self) -> dict[str, Any]:
    from frontend.ui_web.panel_httpd import panel_ui_http_url

    catalog = _pa._list_ducky_catalog()
    base = panel_ui_http_url()
    for item in catalog.get("builtin", []):
        if isinstance(item, dict):
            item["url"] = f"./duckies/{item.get('file', '')}"
    for item in catalog.get("custom", []):
        if isinstance(item, dict):
            duck_id = str(item.get("id", ""))
            slug = duck_id.removeprefix("custom:")
            item["url"] = f"{base}duckies/custom/{slug}.png"
    catalog["custom_base_url"] = base
    return catalog


def upload_custom_ducky(self, filename: str, png_base64: str) -> dict[str, str]:
    entry = _pa.save_custom_ducky_png(filename, png_base64)
    from frontend.ui_web.panel_httpd import panel_ui_http_url

    base = panel_ui_http_url()
    slug = entry.id.removeprefix("custom:")
    return {
        "id": entry.id,
        "label": entry.label,
        "file": entry.file,
        "kind": entry.kind,
        "url": f"{base}duckies/custom/{slug}.png",
    }


def delete_custom_ducky(self, style_id: str) -> dict[str, bool]:
    return {"ok": _pa.delete_custom_ducky(style_id)}


def open_custom_duckies_folder(self) -> None:
    d = _pa.custom_duckies_dir(for_write=True)
    _pa.os.startfile(str(d))  # type: ignore[attr-defined]


def list_custom_verse_templates(self) -> list[dict[str, Any]]:
    return [entry.to_dict() for entry in _pa.list_custom_verse_templates()]


def get_custom_verse_template(self, template_id: str) -> dict[str, Any] | None:
    entry = _pa.get_custom_verse_template(template_id)
    return entry.to_dict() if entry else None


def save_custom_verse_template(
    self,
    name: str,
    icon: str,
    content: str = "",
    template_id: str = "",
    folder: str = "",
    files_json: str = "",
) -> dict[str, Any]:
    """Create or update a user Verse template (never Store/plugin templates).

    files_json: optional JSON array of ``{path, content}`` for multi-file packs.
    Pass template_id to update an existing ``custom:…`` template.
    """
    files: Any = None
    raw = (files_json or "").strip()
    if raw:
        try:
            files = _pa.json.loads(raw)
        except _pa.json.JSONDecodeError as exc:
            raise ValueError(f"invalid files_json: {exc}") from exc
    return _pa.save_custom_verse_template(
        name,
        icon,
        content,
        template_id=template_id or "",
        folder=folder or "",
        files=files,
    ).to_dict()


def delete_custom_verse_template(self, template_id: str) -> dict[str, bool]:
    return {"ok": _pa.delete_custom_verse_template(template_id)}
