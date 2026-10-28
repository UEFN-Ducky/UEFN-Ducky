"""Folders, conversations, groups, profiles, duckies, send/cancel. Mixin for PanelApi — methods stay on the PyWebView JS object."""

from __future__ import annotations

import threading
import uuid
from typing import Any

import frontend.ui_web.panel_api as _pa

# ponytail: one lock for group creates and folder deletes; folder saves replace the whole
# list, so two at once lost one's change. Per-project locks if they ever need to overlap.
_FOLDER_WRITE_LOCK = threading.RLock()

# Duckies made with no island open (the Global Agents folder).
GLOBAL_SLUG = "_no_project"


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


def _sync_composer_selection(conv_id: str, model: str, coding_agent: str = "") -> None:
    """Apply the composer's model + backend to the chat before a turn starts.

    The picker's own save can lose to a still-running turn that saves its stale
    copy of the chat, so the send is authoritative. Updates this conversation
    only — never global settings or a Ducky profile's favorite_models.
    """
    from backend.agent.coding_agents.base import normalize_coding_agent
    from backend.agent.model_pricing import resolve_provider_for_model

    turn_model = (model or "").strip()
    if turn_model.lower() == "default":
        turn_model = ""
    agent = normalize_coding_agent(coding_agent) if (coding_agent or "").strip() else ""
    if not turn_model and not agent:
        return
    conv = _pa.load_conversation(conv_id)
    if conv is None:
        return
    changed = False
    current = normalize_coding_agent(getattr(conv, "coding_agent", None) or "ducky")
    if agent and agent != current:
        conv.coding_agent = current = agent
        if agent != "ducky":
            conv.provider = ""
        changed = True
    if turn_model and (conv.model or "").strip() != turn_model:
        conv.model = turn_model
        changed = True
    if turn_model and current == "ducky":
        resolved = resolve_provider_for_model(turn_model, conv.provider or "")
        if resolved and resolved != (conv.provider or "").strip().lower():
            conv.provider = resolved
            changed = True
    if changed:
        _pa.save_conversation(conv)


def _copy_name(profile: dict[str, Any], members: list[dict[str, Any]]) -> str:
    """The next free copy name for a profile already seated in a group: "Verse Coder 2"."""
    base = str(profile.get("name") or "").strip() or "Ducky"
    taken = {str(m.get(key) or "").strip() for m in members for key in ("name", "ducky_name")}
    n = 2
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


# A tool card's preview; past it the card asks load_tool_result for the rest.
TOOL_RESULT_CAP = 8_000


def _cap_tool_results(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        tool = row.get("tool")
        result = tool.get("result") if isinstance(tool, dict) else None
        if isinstance(result, str) and len(result) > TOOL_RESULT_CAP:
            row["tool"] = {**tool, "result": result[:TOOL_RESULT_CAP], "resultTruncated": True,
                           "resultBytes": len(result.encode("utf-8"))}


class PanelApiChatsMixin:
    @staticmethod
    def _folder_sidebar_row(
        f: Any,
        *,
        project_slug: str = "",
        project_name: str = "",
    ) -> dict[str, str | float]:
        row: dict[str, str | float] = {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id,
            "sort_order": f.sort_order,
            "group_hub_id": getattr(f, "group_hub_id", "") or "",
        }
        if project_slug:
            row["project_slug"] = project_slug
            row["project_name"] = project_name or project_slug
        return row

    @staticmethod
    def _one_list() -> None:
        """Duckies are one list, whatever project is selected; older per-project rows move
        into it the first time the sidebar loads."""
        from frontend.ui_web.project_chats import merge_into_one_list

        try:
            merge_into_one_list()
        except Exception:  # noqa: BLE001 - the list still shows what is already in it
            pass

    def chat_tool_view(self, tool_names: list[str] | None = None, paths: list[str] | None = None) -> dict[str, Any]:
        """A tool card's plugin (name, panel to open) and links for the media files its
        result names, so the card plays them in the chat."""
        from backend.uefn_plugins.chat_view import chat_tool_view

        return chat_tool_view(tool_names, paths)

    def list_folders(self, all_projects: bool = False) -> list[dict[str, str | float]]:
        """The one Duckies list's folders (``all_projects`` is ignored: there is one list)."""
        del all_projects
        self._one_list()
        _pa.ensure_group_folder_hubs()
        return [self._folder_sidebar_row(f) for f in _pa.load_folders()]

    def list_conversations(self, folder_id: str) -> list[dict[str, str | float | int]]:
        return [
            self._conversation_sidebar_row(c)
            for c in _pa.list_conversations(folder_id)
        ]

    def list_all_conversations(self, all_projects: bool = False) -> list[dict[str, str | float | int | bool]]:
        """Every ducky in the one list, in one call (metadata only) for the sidebar."""
        del all_projects
        self._one_list()
        convs = list(_pa.list_all_conversation_metadata())
        group_ids = {c.id for c in convs if getattr(c, "is_group", False)}
        return [self._conversation_sidebar_row(c, group_ids=group_ids) for c in convs]

    def list_conversations_for_file(self, file_path: str) -> list[dict[str, str | float | int]]:
        return [
            self._conversation_sidebar_row(c)
            for c in _pa.list_conversations_for_file(file_path)
        ]

    @staticmethod
    def _sidebar_context_tokens(c: Any) -> int:
        """Cheap context-window size for sidebar hover totals (no recompute)."""
        from frontend.ui_web.token_usage import resolve_context_window_tokens

        stats = getattr(c, "coding_agent_stats", None)
        stored = 0
        num_turns = 0
        if isinstance(stats, dict):
            stored = int(stats.get("context_tokens") or 0)
            num_turns = int(stats.get("num_turns") or 0)
        last: dict[str, Any] | None = None
        usage = getattr(c, "token_usage", None)
        if isinstance(usage, dict):
            calls = usage.get("calls")
            if isinstance(calls, list) and calls and isinstance(calls[-1], dict):
                last = calls[-1]
        if last is not None:
            return resolve_context_window_tokens(
                stored_context_tokens=stored,
                input_tokens=int(last.get("input_tokens") or 0),
                cache_read_tokens=int(last.get("cache_read_tokens") or 0),
                cache_write_tokens=int(last.get("cache_write_tokens") or 0),
                num_turns=num_turns,
            )
        return max(0, stored)

    @staticmethod
    def _conversation_sidebar_row(
        c: Any,
        *,
        group_ids: set[str] | None = None,
        project_slug: str = "",
        project_name: str = "",
    ) -> dict[str, str | float | int | bool]:
        del group_ids  # retained for call-site compat; subagents retired
        parent_id = (getattr(c, "parent_conv_id", None) or "").strip()
        row: dict[str, str | float | int | bool] = {
            "id": c.id,
            "title": c.title,
            "sort_order": c.sort_order,
            "updated": c.updated,
            "ducky_style": c.ducky_style,
            "ducky_name": c.ducky_name or "",
            "profile_id": (getattr(c, "profile_id", None) or "").strip(),
            "ducky_personality": c.ducky_personality or "",
            "tts_voice": getattr(c, "tts_voice", None) or "",
            "tts_speed": float(getattr(c, "tts_speed", 0) or 0.0),
            "file_path": c.file_path,
            "model": c.model or "",
            "provider": c.provider or "",
            "coding_agent": getattr(c, "coding_agent", None) or "ducky",
            "thinking_effort": getattr(c, "thinking_effort", None) or "",
            "terminal_session_id": getattr(c, "terminal_session_id", None) or "",
            "folder_id": getattr(c, "folder_id", None) or "",
            "parent_conv_id": parent_id,
            "is_group": bool(getattr(c, "is_group", False)),
            "muted": bool(getattr(c, "muted", False)),
            # Subagents retired — group members nest under hubs, never parent-linked seats.
            "is_subagent": False,
            "leader_conv_id": (getattr(c, "leader_conv_id", None) or "").strip(),
            "group_members": list(getattr(c, "group_members", None) or []),
            "tool_call_count": int(getattr(c, "tool_call_count", 0) or 0),
            "file_count": int(getattr(c, "file_count", 0) or 0),
            "context_tokens": _pa.PanelApi._sidebar_context_tokens(c),
        }
        if project_slug:
            row["project_slug"] = project_slug
            row["project_name"] = project_name or project_slug
        return row

    def create_folder(self, name: str, parent_id: str = "") -> dict[str, str]:
        f = _pa.create_folder(name, parent_id)
        return {"id": f.id, "name": f.name}

    def _folder_root(self, folder_id: str) -> str | None:
        """Where a folder lives (None: the open island). The sidebar lists Global Agents and,
        with every island shown, other islands' folders too."""
        from frontend.ui_web.project_chats import folder_project_slug

        slug = folder_project_slug(folder_id)
        try:
            return self._layout_project_root(slug) if slug else None
        except ValueError:
            return None

    def rename_folder(self, folder_id: str, name: str) -> None:
        _pa.rename_folder(folder_id, name, self._folder_root(folder_id))

    def delete_folder(self, folder_id: str, archive_members: bool = False) -> list[str]:
        """Delete a folder. Returns group hub ids moved to Archive.

        archive_members also archives duckies in the group. Otherwise they stay.
        """
        root = self._folder_root(folder_id)
        subtree = _pa.folder_subtree_ids(folder_id, root)
        # Stop runners that are about to leave the active tree.
        scope = subtree if archive_members else [folder_id]
        hub_ids = _pa.group_hub_ids_in(scope, root)
        archived: list[str] = []
        if hub_ids:
            from frontend.ui_web.agent_modes import cancel_agent, is_agent_running

            for hub_id in hub_ids:
                targets = [hub_id]
                if archive_members:
                    targets.extend(_pa.conversation_descendant_ids(hub_id, root))
                archived.extend(targets)
                for target_id in targets:
                    if is_agent_running(target_id):
                        _pa.cancel_agent(target_id)
        # Marks every folder it removes deleted, so a stale folder list can't bring them back.
        with _FOLDER_WRITE_LOCK:
            removed = _pa.delete_folder(folder_id, root, archive_members=bool(archive_members))
        # Every open window drops the group (and what went with it) right away.
        _pa.notify_chats_changed(
            open_tab=False,
            removed_conv_ids=[*removed, *archived],
            removed_folder_ids=scope or [folder_id],
        )
        return removed

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
        )
        from frontend.ui_web.project_chats import append_message

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
            from frontend.ui_web.project_chats import create_project_root

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

    def group_create(
        self,
        name: str = "",
        folder_id: str = "",
        open_tab: bool = True,
        project_slug: str = "",
    ) -> dict[str, Any]:
        """Create a group as a folder: folder click opens the group hub chat.

        ``project_slug`` makes it on another island, or in Global Agents
        (``_no_project``); empty keeps the open island.
        """
        title = (name or "").strip() or "Group"
        settings = _pa.PanelSettings.load()
        project_root: str | None = None
        target_slug = (project_slug or "").strip()
        if target_slug:
            from frontend.ui_web.project_chats import create_project_root

            project_root = create_project_root(target_slug, settings.uefn_project_root or "")
        # Parent folder_id here means "create the group-folder inside this folder".
        parent_folder = (folder_id or "").strip()
        with _FOLDER_WRITE_LOCK:
            return self._group_create_locked(title, parent_folder, settings, project_root, open_tab)

    def _group_create_locked(
        self, title: str, parent_folder: str, settings: Any, project_root: str | None, open_tab: bool
    ) -> dict[str, Any]:
        # The folder is saved already linked to its hub (Oct 25 2026): saved plain first and
        # linked two saves later, a sidebar refresh in between showed a stray plain folder
        # next to the group when groups were made fast.
        hub_id = str(uuid.uuid4())
        hub_folder = _pa.create_folder(title, parent_folder, project_root, group_hub_id=hub_id)
        conv = _pa.create_conversation(
            settings,
            hub_folder.id,
            title=title,
            ducky_style=_pa.default_bundled_style(),
            ducky_name="Group",
            project_root=project_root,
            conv_id=hub_id,
        )
        conv.is_group = True
        conv.leader_conv_id = ""
        conv.group_members = []
        # A group made inside another group's folder sits in that group, the way archiving
        # keeps it: deleting, archiving and team plans follow parent_conv_id. Oct 15 2026: a
        # lead's nested groups had none, so deleting its team left them and their members.
        outer = next((f for f in _pa.load_folders(project_root) if parent_folder and f.id == parent_folder), None)
        outer_hub = (getattr(outer, "group_hub_id", None) or "").strip() if outer is not None else ""
        if outer_hub:
            conv.parent_conv_id = outer_hub
        _pa.save_conversation(conv, project_root)
        _pa.notify_chats_changed(conv.id, conv.title, conv.folder_id, open_tab=bool(open_tab))
        return {
            "ok": True,
            "id": conv.id,
            "title": conv.title,
            "is_group": True,
            "leader_conv_id": "",
            "group_members": [],
            "folder_id": hub_folder.id,
        }

    def group_find_or_create(
        self, name: str = "", folder_id: str = "", open_tab: bool = False, caller_conv_id: str = ""
    ) -> dict[str, Any]:
        """Reuse a live group of this name anywhere on the caller's island, or create one there.

        The island comes from ``folder_id`` or ``caller_conv_id`` (the open island when
        neither is known). A same-name group inside ``folder_id`` wins over one elsewhere.
        Archived groups are skipped. The sidebar's own new-group button stays on
        ``group_create``, so a person can still make a duplicate on purpose.
        """
        from frontend.archive_folder import is_archive_folder_id
        from frontend.ui_web.project_chats import conversation_project_slug, folder_project_slug

        title = (name or "").strip() or "Group"
        parent = (folder_id or "").strip()
        # Oct 22 2026: matching only inside the caller's folder made "Deploy core to Fly" 3 times
        # once the caller's chat had moved.
        slug = folder_project_slug(parent) or conversation_project_slug((caller_conv_id or "").strip())
        project_root: str | None = None
        if slug:
            from frontend.ui_web.project_chats import create_project_root

            project_root = create_project_root(slug, _pa.PanelSettings.load().uefn_project_root or "")
        candidates = [
            f for f in _pa.load_folders(project_root) if (getattr(f, "name", "") or "").strip() == title
        ]
        candidates.sort(key=lambda f: (getattr(f, "parent_id", "") or "").strip() != parent)
        for folder in candidates:
            hub_id = (getattr(folder, "group_hub_id", None) or "").strip()
            outer = (getattr(folder, "parent_id", "") or "").strip()
            if not hub_id or is_archive_folder_id(folder.id) or is_archive_folder_id(outer):
                continue
            hub = _pa.load_conversation(hub_id, project_root=project_root)
            if hub is None or not getattr(hub, "is_group", False):
                continue
            if is_archive_folder_id(getattr(hub, "folder_id", "") or ""):
                continue
            return {
                "ok": True,
                "id": hub.id,
                "title": hub.title,
                "is_group": True,
                "leader_conv_id": (getattr(hub, "leader_conv_id", None) or "").strip(),
                "group_members": getattr(hub, "group_members", None) or [],
                "folder_id": folder.id,
                "reused": True,
            }
        created = self.group_create(name=title, folder_id=parent, open_tab=open_tab, project_slug=slug)
        if created.get("ok"):
            created["reused"] = False
        return created

    def group_seat_profile(
        self,
        group_id: str,
        profile_id: str,
        model: str = "",
        write_allowed: list[str] | None = None,
        for_agent: bool = False,
        exclude_conv_id: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        """Seat a ducky in a group, reusing its idle chat instead of a new group.

        ``name`` names the seat by its role ("Search lead"): an idle seat of that name is
        reused, otherwise a new seat gets it. Without one, a busy copy (another node still
        running) gets the next name, "Verse Coder 2", in the same group. ``for_agent``: an agent is seating it, so it never lands on a
        pay-per-token API key the person has not opened to agents.
        """
        from frontend.agent_profiles import get_agent_profile
        from frontend.ui_web.agent_modes import is_agent_running, wait_for_idle
        from frontend.ui_web.group_orchestrator import group_members

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        profile = get_agent_profile(profile_id)
        if not profile:
            return {"ok": False, "error": f"Unknown ducky profile: {profile_id}"}
        pid = str(profile.get("id") or profile_id).strip()
        members = group_members(group)
        same = [m for m in members if m.get("profile_id") == pid and not m.get("is_group")]
        wanted = (name or "").strip()
        if wanted:
            same = [m for m in same if str(m.get("name") or m.get("ducky_name") or "").strip() == wanted]
        for member in same:
            cid = str(member.get("member_conv_id") or "").strip()
            # The MCP bridge cannot see the caller's panel-process session.
            # Never reuse the requesting chat as its own worker.
            if not cid or cid == exclude_conv_id or _pa.load_conversation(cid) is None:
                continue
            # A finished turn's thread stays alive for a moment after the reply.
            # That is not a parallel branch; wait it out before making a copy.
            if is_agent_running(cid):
                wait_for_idle(cid, 3.0)
            if not is_agent_running(cid):
                if for_agent:
                    blocked = self._agent_api_key_refusal(cid)
                    if blocked:
                        return {"ok": False, "error": blocked}
                return {
                    "ok": True,
                    "member": member,
                    "reused": True,
                    "group_members": members,
                    "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
                }
        copy = wanted or (_copy_name(profile, members) if same else "")
        seated = self.group_invite(
            group_id, pid, model=model, write_allowed=write_allowed, name=copy, for_agent=for_agent
        )
        if seated.get("ok"):
            seated["reused"] = False
        return seated

    @staticmethod
    def _agent_api_key_refusal(conv_id: str) -> str:
        """Why an agent may not run work in this chat ("" = it may): the chat bills an API
        key per token that the person has not opened to agents."""
        from frontend.favorite_models import billing_kind

        conv = _pa.load_conversation(conv_id)
        if conv is None:
            return ""
        agent = str(getattr(conv, "coding_agent", "") or "ducky")
        provider = str(getattr(conv, "provider", "") or "")
        if billing_kind(agent, provider, str(getattr(conv, "model", "") or "")) != "api_key":
            return ""
        allowed = {str(p).strip().lower() for p in (_pa.PanelSettings.load().agent_api_keys or [])}
        if provider.strip().lower() in allowed:
            return ""
        return (f"Chat {conv.title!r} runs on the person's {provider} API key (billed per token); agents may "
                "not use it. Seat a ducky whose profile model is a subscription model (e.g. "
                "codex:…) instead, or ask the person to switch that chat's model or allow agents "
                "to use this key in Settings → LLMs.")

    def group_invite(
        self,
        group_id: str,
        profile_id: str,
        model: str = "",
        write_allowed: list[str] | None = None,
        name: str = "",
        for_agent: bool = False,
    ) -> dict[str, Any]:
        """Spawn an independent member chat from a ducky profile and add it to the group.

        ``write_allowed`` seats the member with a write lane (see group_set_member_lane).
        """
        from frontend.agent_profiles import get_agent_profile
        from frontend.ducky_assets import ducky_style_label, normalize_ducky_style
        from frontend.favorite_models import ResolveErr, allow_create_without_model
        from frontend.ui_web.group_orchestrator import (
            _group_folder_id,
            group_members,
            member_color_for_index,
            normalize_member,
        )

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        if write_allowed is not None:
            # Check the lane first: a refused lane used to leave the new member's chat behind.
            check = self.group_check_lane(group_id, "__new_member__", write_allowed)
            if not check.get("ok"):
                return {"ok": False, "error": "; ".join(check.get("errors") or ["lane refused"])}
        profile = get_agent_profile(profile_id)
        if not profile:
            return {"ok": False, "error": f"Unknown ducky profile: {profile_id}"}
        existing = group_members(group)
        pid = str(profile.get("id") or profile_id).strip()
        wanted = (name or "").strip()
        if wanted and any(wanted in (str(m.get("name") or "").strip(), str(m.get("ducky_name") or "").strip())
                          for m in existing):
            return {"ok": False, "error": f"A member of this group is already called {wanted!r}"}
        # A copy name ("Verse Coder 2") is how a profile gets a second seat. The UI invite
        # passes no name and still refuses a duplicate; an agent asking for a second one
        # gets the copy (Oct 21 2026: a lead asked for 2 Verse Coders and was refused).
        if not (name or "").strip() and any(m.get("profile_id") == pid for m in existing):
            if not for_agent:
                return {"ok": False, "error": "That ducky is already in this group"}
            name = _copy_name(profile, existing)
        settings = _pa.PanelSettings.load()
        override = (model or "").strip()
        favorites = [override] if override else profile.get("favorite_models")
        result = allow_create_without_model(
            _pa.resolve_model_selection(favorites, settings)
        )
        if for_agent:
            from frontend.favorite_models import refuse_paid_api_for_agent

            result = refuse_paid_api_for_agent(result, settings)
        if isinstance(result, ResolveErr):
            return {"ok": False, "error": result.message}
        disabled_packs = profile.get("disabled_packs")
        disabled_tools = profile.get("disabled_tool_ids")
        enabled_subs = profile.get("enabled_subskills")
        style = normalize_ducky_style(str(profile.get("ducky_style") or ""))
        # Library profile name (Verse Coder) — not avatar style label (Artist).
        ducky_name = (name or "").strip() or str(profile.get("name") or "").strip() or ducky_style_label(style)
        folder_id = _group_folder_id(group_id) or (group.folder_id or "").strip()
        if not folder_id:
            return {"ok": False, "error": "Group has no folder"}
        member = _pa.create_conversation(
            settings,
            folder_id,
            title=ducky_name,
            parent_conv_id=group_id,
            ducky_style=style,
            ducky_name=ducky_name,
            profile_id=pid,
            ducky_personality=str(profile.get("ducky_personality") or ""),
            tts_voice=str(profile.get("tts_voice") or "").strip(),
            tts_speed=float(profile.get("tts_speed") or 0.0),
            disabled_packs=disabled_packs if isinstance(disabled_packs, list) else None,
            enabled_subskills=enabled_subs if isinstance(enabled_subs, dict) else None,
            disabled_tool_ids=disabled_tools if isinstance(disabled_tools, list) else None,
            model=result.model,
            provider=result.provider or None,
            coding_agent=result.coding_agent,
        )
        _pa.adopt_group_project(member, group_id)
        row = normalize_member(
            {
                "member_conv_id": member.id,
                "profile_id": pid,
                "name": ducky_name,
                "ducky_name": ducky_name,
                "ducky_style": style,
                "model": result.selection.qualified if result.model else "",
                "coding_agent": result.coding_agent,
                "tts_voice": str(getattr(member, "tts_voice", None) or profile.get("tts_voice") or ""),
                "tts_speed": float(getattr(member, "tts_speed", None) or profile.get("tts_speed") or 0.0),
                "color": member_color_for_index(len(existing)),
            },
            index=len(existing),
        )
        # First invited member becomes leader when none set yet.
        if not (getattr(group, "leader_conv_id", None) or "").strip():
            group.leader_conv_id = member.id
        self._save_roster(group, [*existing, row])
        if write_allowed is not None:
            laned = self.group_set_member_lane(group_id, member.id, write_allowed)
            if not laned.get("ok"):
                return {"ok": False, "error": laned.get("error"), "member": row}
            group = _pa.load_conversation(group_id) or group
            row = next((m for m in group_members(group) if m.get("member_conv_id") == member.id), row)
        # Reload sidebar only — do not notify the member id (that auto-opens a tab)
        # and do not re-select the group (that remounts the pane on a stale roster).
        _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
        return {
            "ok": True,
            "member": row,
            "group_members": group.group_members,
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
        }

    def post_team_message(self, sender_id: str, receiver_id: str, body: str = "") -> dict[str, Any]:
        """Show a message one team member sent another in their group chat (from the tool server)."""
        from frontend.ui_web.agent_modes import get_panel_push
        from frontend.ui_web.group_orchestrator import post_team_message

        posted = post_team_message(str(sender_id or ""), str(receiver_id or ""), str(body or ""), push=get_panel_push())
        return {"ok": True, "posted": posted}

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

    def group_set_leader(self, group_id: str, member_conv_id: str) -> dict[str, Any]:
        """Designate the spokesperson for a group (cross-group / nested routing)."""
        from frontend.ui_web.group_orchestrator import group_members

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        mid = (member_conv_id or "").strip()
        if not mid:
            return {"ok": False, "error": "member_conv_id required"}
        members = group_members(group)
        if not any(m.get("member_conv_id") == mid for m in members):
            return {"ok": False, "error": "Member not in this group"}
        group.leader_conv_id = mid
        _pa.save_conversation(group)
        _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
        return {"ok": True, "leader_conv_id": mid, "group_members": members}

    def group_add_member(
        self, group_id: str, conv_id: str, as_leader: bool = False, move_folder: bool = True
    ) -> dict[str, Any]:
        """Move an existing chat into a group folder and roster (optional as_leader).

        ``move_folder`` (default) files an as_leader chat in the group's folder too, so a
        workstream lead sits in the group it leads. A chat that leads the group it is filed
        in (the root lead, or a lead joining a sibling team) keeps its place.
        """
        from frontend.ui_web.group_orchestrator import (
            group_members,
            member_color_for_index,
            normalize_member,
            stays_with_outer_group,
            sync_group_members_from_folder,
        )
        from frontend.ui_web.group_orchestrator import _group_folder_id
        from frontend.ui_web.project_chats import adopt_group_project, island_for_conversation

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        cid = (conv_id or "").strip()
        member = _pa.load_conversation(cid)
        if member is None:
            return {"ok": False, "error": "Conversation not found"}
        if getattr(member, "is_group", False):
            return {"ok": False, "error": "Cannot add a group hub as a leaf member"}
        folder_id = _group_folder_id(group_id) or (group.folder_id or "").strip()
        if not folder_id:
            return {"ok": False, "error": "Group has no folder"}
        _convs, folders = island_for_conversation(group_id)
        # A member moved from another group keeps its lane: the new group's roster row is
        # built fresh from the folder and would otherwise drop it ("No lane").
        old_row = self._lane_row_in_other_group(member, group_id)
        # The lead of an outer group joins its subgroups' rosters but stays filed on top of
        # its own group; a chat leading the group it sits in is never pulled out of it
        # (Oct 22 2026: that left the old group empty). Everyone else moves in.
        move = not as_leader or move_folder
        if move and as_leader and self._leads_home_group(member, folders):
            move = False
        if move and not stays_with_outer_group(member, folder_id, folders):
            _pa.move_conversation(cid, folder_id)
            member = _pa.load_conversation(cid) or member
            member.parent_conv_id = group_id
            member.folder_id = folder_id
            _pa.save_conversation(member)
        adopt_group_project(member, group_id)
        sync_group_members_from_folder(group)
        group = _pa.load_conversation(group_id) or group
        existing = group_members(group)
        if not any(m.get("member_conv_id") == cid for m in existing):
            name = (
                str(getattr(member, "ducky_name", None) or "").strip()
                or str(member.title or "").strip()
                or "Ducky"
            )
            row = normalize_member(
                {
                    "member_conv_id": cid,
                    "profile_id": str(getattr(member, "profile_id", None) or ""),
                    "name": name,
                    "ducky_name": str(getattr(member, "ducky_name", None) or ""),
                    "ducky_style": str(getattr(member, "ducky_style", None) or ""),
                    "model": str(getattr(member, "model", None) or ""),
                    "coding_agent": str(getattr(member, "coding_agent", None) or ""),
                    "tts_voice": str(getattr(member, "tts_voice", None) or ""),
                    "tts_speed": float(getattr(member, "tts_speed", None) or 0.0),
                    "color": member_color_for_index(len(existing)),
                },
                index=len(existing),
            )
            existing = [*existing, row]
        if as_leader or not (getattr(group, "leader_conv_id", None) or "").strip():
            group.leader_conv_id = cid
        self._save_roster(group, existing)
        _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
        out: dict[str, Any] = {
            "ok": True,
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
            "group_members": group_members(group),
            "folder_id": (getattr(member, "folder_id", None) or "").strip(),
        }
        now_row = next((m for m in out["group_members"] if m.get("member_conv_id") == cid), {})
        if old_row and now_row.get("write_allowed") is None:
            kept = self.group_set_member_lane(
                group_id, cid, old_row["write_allowed"], set_by=str(old_row.get("lane_set_by") or "user")
            )
            if kept.get("ok"):
                out["group_members"] = kept["group_members"]
            else:
                out["lane_warning"] = f"Lane not kept: {kept.get('error')}"
        return out

    @staticmethod
    def _leads_home_group(member: Any, folders: list[Any]) -> bool:
        """True when ``member`` is the leader of the group whose folder it sits in."""
        home = (getattr(member, "folder_id", None) or "").strip()
        hub_id = next((str(getattr(f, "group_hub_id", "") or "").strip() for f in folders if f.id == home), "")
        hub = _pa.load_conversation(hub_id) if hub_id else None
        return hub is not None and (getattr(hub, "leader_conv_id", None) or "").strip() == member.id

    @staticmethod
    def _lane_row_in_other_group(member: Any, group_id: str) -> dict[str, Any] | None:
        """The member's roster row in the group it is leaving, when that row has a lane."""
        from frontend.ui_web.group_orchestrator import group_members

        old_id = (getattr(member, "parent_conv_id", None) or "").strip()
        if not old_id or old_id == group_id:
            return None
        old = _pa.load_conversation(old_id)
        if not old or not getattr(old, "is_group", False):
            return None
        row = next((m for m in group_members(old) if m.get("member_conv_id") == member.id), None)
        return row if row and row.get("write_allowed") is not None else None

    def group_set_member_model(
        self, group_id: str, member_conv_id: str, model: str = ""
    ) -> dict[str, Any]:
        """Set the model a group member uses for their turns."""
        from frontend.favorite_models import ResolveErr
        from frontend.ui_web.group_orchestrator import group_members, normalize_member

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        mid = (member_conv_id or "").strip()
        members = group_members(group)
        if not any(m.get("member_conv_id") == mid for m in members):
            return {"ok": False, "error": "Member not in this group"}
        settings = _pa.PanelSettings.load()
        override = (model or "").strip()
        result = _pa.resolve_model_selection([override] if override else None, settings)
        if isinstance(result, ResolveErr):
            return {"ok": False, "error": result.message}
        agent_res = self.set_conversation_coding_agent(
            mid,
            result.coding_agent,
            result.model,
            result.provider or "",
        )
        if not agent_res.get("ok"):
            return {"ok": False, "error": agent_res.get("error") or "Failed to set model"}
        next_members = []
        for i, m in enumerate(members):
            row = dict(m)
            if row.get("member_conv_id") == mid:
                row["model"] = result.selection.qualified
                row["coding_agent"] = result.coding_agent
            next_members.append(normalize_member(row, index=i))
        next_members = self._save_roster(group, next_members)
        _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
        return {"ok": True, "group_members": next_members}

    @staticmethod
    def _save_roster(group: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Every roster save: never puts back a teammate's older lane (Oct 24 2026)."""
        from frontend.ui_web.group_orchestrator import save_group_roster

        return save_group_roster(group, rows, save=_pa.save_conversation)

    # -- write lanes ------------------------------------------------------------

    def group_set_member_lane(
        self,
        group_id: str,
        member_conv_id: str,
        write_allowed: list[str] | str | None,
        force: bool = False,
        set_by: str = "user",
    ) -> dict[str, Any]:
        """Set (or clear with None) a member's write lane; refuses overlaps with teammates."""
        import time

        from backend.workspace import events, lanes as lane_engine
        from frontend.ui_web.group_orchestrator import group_members

        from frontend.ui_web.project_chats import _conversation_lock

        # Use the same reentrant group lock as save_conversation so competing
        # lane updates cannot both validate an obsolete roster snapshot.
        with _conversation_lock(group_id):
            group = _pa.load_conversation(group_id)
            if not group or not getattr(group, "is_group", False):
                return {"ok": False, "error": "Not a group chat"}
            mid = (member_conv_id or "").strip()
            try:
                rows, warnings = lane_engine.set_member_lane(
                    group_members(group), mid, write_allowed, set_by=(set_by or "user"), now=time.time(), force=bool(force)
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            with lane_engine.setting_lane(mid):
                next_members = self._save_roster(group, rows)
            lane_engine.invalidate_lane_cache(mid)
        _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
        lane = next((m.get("write_allowed") for m in next_members if m.get("member_conv_id") == mid), None)
        events.emit(events.LaneChangedEvent(group.id, mid, None if lane is None else tuple(lane)).to_dict())
        return {
            "ok": True,
            "group_members": next_members,
            "warnings": warnings,
            "lanes": lane_engine.lane_set_view(group.id, next_members),
        }

    def group_lane_authorities(self, group_id: str) -> list[str]:
        """Who may set lanes in this group: its leader, then the leader of every group it
        is nested in (an outer lead seats its subgroups' workers)."""
        from frontend.ui_web.group_orchestrator import _group_folder_id
        from frontend.ui_web.project_chats import island_for_conversation

        gid = (group_id or "").strip()
        group = _pa.load_conversation(gid)
        if not group or not getattr(group, "is_group", False):
            return []
        leaders = [(getattr(group, "leader_conv_id", None) or "").strip()]
        _convs, folders = island_for_conversation(gid)
        rows = {f.id: f for f in folders}
        seen: set[str] = set()
        cur = (getattr(rows.get(_group_folder_id(gid)), "parent_id", None) or "").strip()
        while cur and cur not in seen:
            seen.add(cur)
            hub_id = (getattr(rows.get(cur), "group_hub_id", None) or "").strip()
            hub = _pa.load_conversation(hub_id) if hub_id else None
            if hub is not None and getattr(hub, "is_group", False):
                leaders.append((getattr(hub, "leader_conv_id", None) or "").strip())
            cur = (getattr(rows.get(cur), "parent_id", None) or "").strip()
        return [lid for lid in leaders if lid]

    def group_get_lanes(self, group_id: str) -> dict[str, Any]:
        from backend.workspace import lanes as lane_engine
        from frontend.ui_web.group_orchestrator import group_members

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        return {"ok": True, **lane_engine.lane_set_view(group.id, group_members(group))}

    def group_check_lane(
        self, group_id: str, member_conv_id: str, write_allowed: list[str] | str | None
    ) -> dict[str, Any]:
        """Dry run for the lane editor: normalized globs plus overlap errors/warnings."""
        from backend.workspace import lanes as lane_engine
        from frontend.ui_web.group_orchestrator import group_members

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "errors": ["Not a group chat"], "warnings": [], "normalized": None}
        try:
            lane = lane_engine.normalize_lane(write_allowed)
        except lane_engine.LaneGlobError as exc:
            return {"ok": False, "errors": [str(exc)], "warnings": [], "normalized": None}
        proposed = {str(m.get("member_conv_id") or ""): m.get("write_allowed") for m in group_members(group)}
        proposed[(member_conv_id or "").strip()] = lane
        verdict = lane_engine.check_lane_set(proposed)
        return {"ok": not verdict["errors"], "normalized": lane, **verdict}

    def group_remove(self, group_id: str, member_conv_id: str) -> dict[str, Any]:
        from frontend.ui_web.group_orchestrator import evict_member_from_group, group_members

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        mid = (member_conv_id or "").strip()
        kept = [m for m in group_members(group) if m.get("member_conv_id") != mid]
        leader = (getattr(group, "leader_conv_id", None) or "").strip()
        if leader and leader == mid:
            group.leader_conv_id = str(kept[0].get("member_conv_id") or "") if kept else ""
        # Persist leader/roster first so a later folder-sync cannot pull this member back.
        self._save_roster(group, kept)
        evict_member_from_group(mid)
        _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
        return {
            "ok": True,
            "group_members": kept,
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
        }

    def group_members(self, group_id: str) -> dict[str, Any]:
        from frontend.ui_web.group_orchestrator import (
            _rehome_group_roster,
            group_members,
            is_group_conversation,
            sync_group_members_from_folder,
        )

        group = _pa.load_conversation(group_id)
        if not group:
            return {"ok": False, "error": "Conversation not found", "members": []}
        if is_group_conversation(group):
            moved = _rehome_group_roster(group)
            sync_group_members_from_folder(group)
            group = _pa.load_conversation(group_id) or group
            if moved:
                _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
        # Read observations only; never persist runtime or duplicate plan state.
        import time
        from frontend.ui_web.project_chats import conversation_project_slug, project_root_for_slug
        from backend.agent.coding_agents.plans import load_plan_view
        from frontend.ui_web.agent_modes import get_panel_push

        members = group_members(group) if is_group_conversation(group) else []
        try:
            # A bridge process has no authoritative view of panel sessions.
            running = self.list_running_agents() if get_panel_push() is not None else None
            if not isinstance(running, list) or not all(isinstance(cid, str) for cid in running):
                running = None
        except Exception:
            running = None
        observed_at = time.time()
        enriched = []
        for member in members:
            row = dict(member)
            observation: dict[str, Any] = {
                "observed_at": observed_at, "runtime": "unknown", "assignment": None,
                "group_id": group.id, "role": "unknown", "project_slug": None,
            }
            row["observation"] = observation
            enriched.append(row)
            try:
                cid = str(member.get("member_conv_id") or "")
                conv = _pa.load_conversation(cid)
                if conv is None or not self._filed_with_group(conv, group.id):
                    continue
                slug = conversation_project_slug(cid)
                if not slug or slug != conversation_project_slug(group.id):
                    continue
                observation["project_slug"] = slug
                observation["role"] = "leader" if cid == getattr(group, "leader_conv_id", "") else "member"
                if running is not None:
                    observation["runtime"] = "running" if cid in running else "idle"
                root = "" if slug == "_no_project" else project_root_for_slug(slug)
                if root is None:
                    continue  # Unknown stored project: never use the active island.
                # Plans are found up the chat's parent chain; a member filed in another
                # group (an outer lead) shows this group's assignment instead.
                filed_here = (getattr(conv, "parent_conv_id", None) or "").strip() == group.id
                plan = load_plan_view(cid if filed_here else group.id, project_root=root)
                if not plan and not filed_here:
                    plan = load_plan_view(cid, project_root=root)
                if not plan:
                    continue
                source = plan.get("assigned_from") or {}
                node_id = source.get("node_id")
                node = next((n for n in plan.get("nodes", []) if n.get("id") == node_id), None)
                if node_id and node is None:
                    continue
                observation["assignment"] = {
                    "plan_id": plan.get("id"), "chat_id": plan.get("chat_id"),
                    "node_id": node_id, "title": (node or plan).get("content", plan.get("title", "")),
                    "status": (node or plan).get("status", "unknown"),
                }
            except Exception:
                # Ambiguous/deleted assignments and unavailable stores stay unknown.
                pass
        return {
            "ok": True,
            "is_group": is_group_conversation(group),
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
            "members": enriched,
        }

    @staticmethod
    def _filed_with_group(conv: Any, group_id: str) -> bool:
        """A roster member counts wherever it is filed: in this group, or in another live
        group (a lead that stays on top of its outer group). A parent that no longer exists
        is a stale row."""
        parent_id = (getattr(conv, "parent_conv_id", None) or "").strip()
        if parent_id == group_id:
            return True
        parent = _pa.load_conversation(parent_id) if parent_id else None
        return parent is not None and bool(getattr(parent, "is_group", False))

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

    def group_stop(self, group_id: str) -> dict[str, Any]:
        """Stop every running member's turn in this group and every group nested in it, never
        the group's lead (Oct 24 2026: Stop team also stopped the lead). Nothing is deleted;
        members can be started again."""
        from frontend.ui_web.team_keeper import stop_team

        return stop_team(group_id, self.cancel_agent)

    def list_agent_profiles(self) -> dict[str, Any]:
        profiles = _pa.list_agent_profiles()
        return {
            "profiles": profiles,
            "archived_profiles": _pa.list_archived_agent_profiles(),
            "template_profiles": _pa.list_bundled_agent_profile_templates(),
            "blank_profile_id": _pa.BLANK_PROFILE_ID,
        }

    def archive_agent_profile(self, profile_id: str) -> dict[str, Any]:
        saved = _pa.archive_agent_profile(profile_id)
        return {"ok": True, "profile": saved}

    def unarchive_agent_profile(self, profile_id: str) -> dict[str, Any]:
        saved = _pa.unarchive_agent_profile(profile_id)
        return {"ok": True, "profile": saved}

    def save_agent_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
        old = self._profile_name(str(profile.get("id") or ""))
        saved = _pa.save_agent_profile(profile)
        self._sync_chats_for_profile(saved, old)
        return {"ok": True, "profile": saved}

    def save_agent_profile_override(self, bundled_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        old = self._profile_name(bundled_id)
        saved = _pa.save_agent_profile_override(bundled_id, patch)
        self._sync_chats_for_profile(saved, old)
        return {"ok": True, "profile": saved}

    @staticmethod
    def _profile_name(profile_id: str) -> str:
        from frontend.agent_profiles import get_agent_profile

        try:
            return str((get_agent_profile(profile_id) or {}).get("name") or "").strip() if profile_id else ""
        except Exception:  # noqa: BLE001 - a new profile has no old name
            return ""

    def _sync_chats_for_profile(self, profile: dict[str, Any], old_name: str = "") -> None:
        """A renamed library profile renames the chats still called by its old name (and its
        numbered copies, "Verse Coder 2"). Seats named by role ("Search lead") keep their name."""
        pid = str(profile.get("id") or "").strip()
        name = str(profile.get("name") or "").strip()
        old = (old_name or "").strip()
        if not pid or not name or not old or old == name:
            return
        try:
            from frontend.ui_web.project_chats import (
                list_all_conversation_metadata,
                load_conversation,
                save_conversation,
            )

            for meta in _pa.list_all_conversation_metadata():
                if str(getattr(meta, "profile_id", "") or "").strip() != pid:
                    continue
                current = str(getattr(meta, "ducky_name", "") or "").strip()
                suffix = current[len(old):]
                if not current.startswith(old) or (suffix and not suffix.strip().isdigit()):
                    continue
                conv = _pa.load_conversation(meta.id)
                if conv is None:
                    continue
                conv.ducky_name = name + suffix
                _pa.save_conversation(conv, touch_updated=False)
                _pa.notify_chats_changed(conv.id, conv.title, conv.folder_id, open_tab=False)
        except Exception:
            _pa.logging.getLogger(__name__).exception("sync chats for profile %s failed", pid)

    def delete_agent_profile(self, profile_id: str) -> dict[str, Any]:
        _pa.delete_agent_profile(profile_id)
        return {"ok": True}

    def duplicate_agent_profile(self, profile_id: str) -> dict[str, Any]:
        duplicated = _pa.duplicate_agent_profile(profile_id)
        return {"ok": True, "profile": duplicated}

    def get_agent_profile_editor_catalog(self) -> dict[str, Any]:
        """Duckies editor catalog — never sync-wait on plugin register().

        Same non-blocking pattern as get_uefn_plugin_contributions: return packs/
        builtins/MCP immediately; fill UEFN agent tool rows once plugins_ready.
        """
        from backend.agent.builtin_toolsets import (
            builtin_group_rows,
            get_enabled_builtin_group_ids,
        )
        from backend.mcp_plugins.store import (
            get_enabled_plugin_ids,
            list_mcp_plugins,
            seed_mcp_plugins,
        )
        from backend.skills.store import default_selection_from_settings, list_skill_packs, seed_skill_packs
        from backend.uefn_plugins.host import (
            ensure_plugins_loaded_async,
            plugins_ready,
            uefn_agent_tool_rows,
        )
        from frontend.ui_web.project_chats import all_available_tool_ids

        seed_skill_packs()
        seed_mcp_plugins()
        settings = _pa.PanelSettings.load()
        sel = default_selection_from_settings(settings)
        base = {
            "packs": list_skill_packs(),
            "default_disabled_packs": list(sel.disabled_packs),
            "default_disabled_tool_ids": [],
            "default_enabled_packs": sel.enabled_packs,  # derived (all − denied)
            "default_enabled_subskills": {},
        }
        if plugins_ready():
            return {
                **base,
                "tools": builtin_group_rows() + list_mcp_plugins() + uefn_agent_tool_rows(),
                "default_tool_ids": all_available_tool_ids(),
                "plugins_loading": False,
            }
        # Kick background load; UI refreshes on uefn_plugins_changed.
        ensure_plugins_loaded_async(on_done=self._notify_plugins_ready)
        return {
            **base,
            "tools": builtin_group_rows() + list_mcp_plugins(),
            "default_tool_ids": list(get_enabled_builtin_group_ids())
            + list(get_enabled_plugin_ids()),
            "plugins_loading": True,
        }

    def get_conversation_skills(self, conv_id: str) -> dict[str, Any]:
        from backend.skills.store import list_skill_packs, resolve_conversation_selection, seed_skill_packs
        from backend.mcp_plugins.store import seed_mcp_plugins

        seed_skill_packs()
        seed_mcp_plugins()
        conv = _pa.load_conversation(conv_id)
        settings = _pa.PanelSettings.load()
        packs = list_skill_packs()
        if conv:
            sel = resolve_conversation_selection(conv, settings)
        else:
            from backend.skills.store import default_selection_from_settings

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
        from backend.skills.store import merge_selection

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

    def set_agent_allow_everything(self, conv_id: str, on: bool) -> dict[str, Any]:
        """The chat's own "Allow everything" (approval cards) on or off."""
        from backend.tools.panel.permission_prompt import allow_state, set_allow_everything

        set_allow_everything(str(conv_id or ""), bool(on))
        return {"ok": True, **allow_state(str(conv_id or ""))}

    def get_agent_permissions(self, conv_id: str, agent: str = "") -> dict[str, Any]:
        """The chat's approval mode, the modes its agent can use and the rules it allowed."""
        from backend.tools.panel.permission_prompt import chat_permissions

        return chat_permissions(str(conv_id or ""), str(agent or ""))

    def update_agent_plugin(self, conv_id: str, agent: str = "") -> dict[str, Any]:
        """The pop-up's Update: install the Store's newest plugin for this agent, then its new modes."""
        from backend.tools.panel import permission_prompt as permissions

        conv_id = str(conv_id or "")
        agent_id = str(agent or "") or permissions.chat_permissions(conv_id)["agent"]
        permissions.update_agent_plugin(agent_id)
        return permissions.chat_permissions(conv_id, agent_id)

    def set_agent_permission_mode(self, conv_id: str, mode: str, agent: str = "") -> dict[str, Any]:
        """Pick "ask", "edits" or "all" for this chat; its next turn uses it."""
        from backend.tools.panel import permission_prompt as permissions

        conv_id, agent = str(conv_id or ""), str(agent or "")
        row = next((m for m in permissions.chat_permissions(conv_id, agent)["modes"] if m["id"] == mode), None)
        if row is None:
            raise ValueError(f"Unknown permission mode: {mode!r}")
        if not row["available"]:
            raise ValueError(row["reason"])
        permissions.set_permission_mode(conv_id, mode)
        state = permissions.chat_permissions(conv_id, agent)
        if state["mode"] != mode:
            raise ValueError("Could not save the permission change.")
        return state

    def remove_agent_permission_rule(self, conv_id: str, rule: str, agent: str = "") -> dict[str, Any]:
        """Forget one "always allow" rule of this chat."""
        from backend.tools.panel import permission_prompt as permissions

        conv_id = str(conv_id or "")
        permissions.forget_rule(conv_id, str(rule or ""))
        if rule in permissions._rules(conv_id):
            raise ValueError("Could not save the permission change.")
        return permissions.chat_permissions(conv_id, str(agent or ""))

    def clear_agent_permission_rules(self, conv_id: str, agent: str = "") -> dict[str, Any]:
        """Forget every "always allow" rule of this chat; its mode stays."""
        from backend.tools.panel import permission_prompt as permissions

        conv_id = str(conv_id or "")
        permissions.clear_rules(conv_id)
        state = permissions.chat_permissions(conv_id, str(agent or ""))
        if state["rules"]:
            raise ValueError("Could not save the permission change.")
        return state

    def list_saved_agent_permissions(self) -> list[dict[str, str]]:
        """All remembered chat approvals, including individual command/file rules."""
        from backend.tools.panel import permission_prompt as permissions

        if permissions._rules_in_db():
            from backend.store.repos import chats, kv

            docs = kv.list_docs("workspace_state", "agent_allow:")
            entries = []
            for key, rules in docs.items():
                conv_id = key.removeprefix("agent_allow:")
                conv = chats.conv_get(conv_id, with_messages=False)
                if conv and isinstance(rules, list):
                    entries.append((conv_id, str(conv.get("title") or "Chat"), rules))
        else:
            entries = [
                (conv.id, conv.title or "Chat", list(getattr(conv, "agent_allow_rules", None) or []))
                for _, conv in _pa.iter_conversations_by_project()
            ]
        return [
            {"conv_id": conv_id, "title": title, "rule": rule,
             "label": "Allow everything in this chat" if rule == "*" else rule}
            for conv_id, title, rules in entries for rule in rules if isinstance(rule, str) and rule
        ]

    def revoke_saved_agent_permission(self, conv_id: str, rule: str) -> dict[str, bool]:
        """Human Settings action: remove one existing approval, never grant access."""
        from backend.tools.panel import permission_prompt as permissions

        rules = permissions._rules(conv_id)
        remaining = [saved for saved in rules if saved != rule]
        permissions._store_rules(conv_id, remaining)
        if permissions._rules(conv_id) != remaining:
            raise ValueError("Could not save the permission change.")
        return {"ok": True}

    def rename_conversation(self, conv_id: str, title: str) -> None:
        conv = _pa.load_conversation(conv_id)
        if conv:
            conv.title = title.strip() or conv.title
            _pa.save_conversation(conv)

    def move_chats_to_project(
        self,
        conv_ids: list[str],
        folder_ids: list[str],
        target_slug: str,
        folder_id: str = "",
    ) -> None:
        from frontend.ui_web.project_chats import move_chats_to_project

        move_chats_to_project(
            [str(cid) for cid in (conv_ids or [])],
            [str(fid) for fid in (folder_ids or [])],
            str(target_slug or ""),
            str(folder_id or ""),
        )
        _pa.notify_chats_changed(open_tab=False)

    def _chat_root(self, conv_id: str) -> str | None:
        """The island a chat lives on (None: the open one): Global Agents and other
        islands' chats are in the sidebar too."""
        from frontend.ui_web.project_chats import conversation_project_slug

        slug = conversation_project_slug(conv_id)
        try:
            return self._layout_project_root(slug) if slug else None
        except ValueError:
            return None

    def move_conversation(self, conv_id: str, folder_id: str) -> None:
        archiving = _pa.is_archive_folder_id(folder_id)
        root = self._chat_root(conv_id)
        leaving: list[str] = []
        if archiving:
            from frontend.ui_web.agent_modes import cancel_agent, is_agent_running

            leaving = [conv_id, *_pa.conversation_descendant_ids(conv_id, root)]
            conv = _pa.load_conversation(conv_id, root)
            if conv is not None and getattr(conv, "is_group", False):
                # A group goes to Archive with its duckies and nested groups.
                own = next((f for f in _pa.load_folders(root) if (f.group_hub_id or "") == conv_id), None)
                if own is not None:
                    subtree = _pa.folder_subtree_ids(own.id, root)
                    for hub_id in _pa.group_hub_ids_in(subtree, root):
                        leaving.extend([hub_id, *_pa.conversation_descendant_ids(hub_id, root)])
            leaving = list(dict.fromkeys(leaving))
            for target_id in leaving:
                if is_agent_running(target_id):
                    _pa.cancel_agent(target_id)
        _pa.move_conversation(conv_id, folder_id or "", root)
        # Archived chats leave every open window's tree at once; a restore reloads them.
        _pa.notify_chats_changed(open_tab=False, removed_conv_ids=leaving)

    def delete_conversation(self, conv_id: str, keep_inside: bool = False) -> None:
        from frontend.ui_web.agent_modes import cancel_agent, is_agent_running

        root = self._chat_root(conv_id)
        if keep_inside:
            # Only the group goes: what was in it stays where it is, on its own.
            for child_id in _pa.conversation_descendant_ids(conv_id, root):
                child = _pa.load_conversation(child_id, root)
                if child is not None and (child.parent_conv_id or "").strip() == conv_id:
                    child.parent_conv_id = ""
                    child.leader_conv_id = ""
                    _pa.save_conversation(child, root)
        # Its sub-agent chats (an archived group's duckies and nested groups too) are found
        # on its own island (Global Agents or another one).
        doomed = [conv_id, *_pa.conversation_descendant_ids(conv_id, root)]
        for target_id in doomed:
            if is_agent_running(target_id):
                _pa.cancel_agent(target_id)
        _pa.delete_conversation(conv_id, root)
        _pa.notify_chats_changed(open_tab=False, removed_conv_ids=doomed)

    @staticmethod
    def _layout_project_root(slug: str) -> str | None:
        """Where a layout for this project is saved. None: the open island."""
        from frontend.ui_web.project_chats import project_root_for_slug, project_slug

        wanted = (slug or "").strip()
        if not wanted:
            return None
        current = getattr(_pa.PanelSettings.load(), "uefn_project_root", "") or ""
        if project_slug(current) == wanted:
            return None
        if wanted == GLOBAL_SLUG:
            return ""
        root = project_root_for_slug(wanted)
        if root is None:
            raise ValueError(f"Unknown project: {wanted}")
        return root

    def apply_sidebar_layout(self, payload: dict[str, Any]) -> None:
        folders = payload.get("folders")
        chats = payload.get("chats")
        if not isinstance(folders, list) or not isinstance(chats, list):
            raise ValueError("layout payload must include folders and chats arrays")
        project_root = self._layout_project_root(str(payload.get("project_slug") or ""))
        # Unknown ids (deleted elsewhere) are skipped, never re-created.
        _pa.apply_sidebar_layout(folders=folders, chats=chats, project_root=project_root)
        # Drag in/out of a group folder is a roster change — refresh IN THIS CHAT
        # after disk is saved (the optimistic tree is too early to fetch against).
        _pa.notify_chats_changed(open_tab=False)

    def load_messages(
        self, conv_id: str, before_id: int | None = None, limit: int | None = None, full_results: bool = False
    ) -> list[dict[str, Any]]:
        """The one message-load path: conversation as flat UI rows.

        `_messages_to_ui` is the single source of truth for how a stored
        conversation becomes rows; row ids are message index * 1000 + row index,
        so a page (``limit`` newest rows, or the ``limit`` rows before
        ``before_id``) converts only the messages it shows, newest first.
        Without arguments the whole conversation comes back, as before.

        A tool result over ``TOOL_RESULT_CAP`` chars is cut, with ``resultTruncated``
        and ``resultBytes`` on the row (``load_tool_result`` has it whole): 40 rows over
        32 KB were 22% of a 7 MB chat. ``full_results`` keeps them whole (account mirror).
        """
        started = _pa.time.monotonic()
        try:
            conv = _pa.load_conversation(conv_id)
            if not conv:
                return []
            rows = _pa._messages_to_ui(
                conv,
                live=conv_id in self.list_running_agents(),
                before_id=None if before_id is None else int(before_id),
                limit=None if limit is None else int(limit),
            )
        except Exception as exc:
            # A chat that can't be read must leave a trace, not just a spinner.
            _pa.record_error("chat", f"Chat {str(conv_id)[:8]} couldn't be loaded: {type(exc).__name__}: {exc}")
            raise
        if not full_results:
            _cap_tool_results(rows)
        took = _pa.time.monotonic() - started
        if took > 5.0:
            _pa.record_activity("chat", f"Chat {str(conv_id)[:8]} took {took:.1f}s to load ({len(rows)} rows)")
        return rows

    def load_tool_result(self, conv_id: str, row_id: int) -> dict[str, Any]:
        """The whole result of one tool row that ``load_messages`` cut."""
        conv = _pa.load_conversation(conv_id)
        if not conv:
            return {"ok": False, "error": "Chat not found"}
        rows = _pa._messages_to_ui(conv, before_id=int(row_id) + 1, limit=1)
        tool = rows[-1].get("tool") if rows and rows[-1].get("id") == int(row_id) else None
        if not isinstance(tool, dict):
            return {"ok": False, "error": "No tool result in that row"}
        return {"ok": True, "result": str(tool.get("result") or "")}

    def send_message(
        self,
        conv_id: str,
        text: str,
        mode: str,
        model: str,
        file_path: str = "",
        attachments: list[dict[str, Any]] | None = None,
        coding_agent: str = "",
    ) -> dict[str, Any]:
        from frontend.ui_web.group_orchestrator import (
            is_group_conversation,
            run_group_turn,
        )

        if file_path:
            before = _pa.load_conversation(conv_id)
            had_path = bool(before and before.file_path)
            _pa.ensure_conversation_file_path(conv_id, file_path)
            if not had_path:
                after = _pa.load_conversation(conv_id)
                if after and after.file_path:
                    _pa.notify_chats_changed(
                        after.id, after.title, after.folder_id, push=self._push, open_tab=False
                    )
        _sync_composer_selection(conv_id, model, coding_agent)
        conv = _pa.load_conversation(conv_id)
        # Subagents retired — every non-group chat is composable (group members included).
        if conv is not None and is_group_conversation(conv):
            run_id = run_group_turn(
                conv_id,
                text,
                mode=mode,
                model=model,
                push=self._push,
                attachments=attachments or [],
            )
            return {"run_id": run_id}
        run_id = _pa.run_message(conv_id, text, mode, model, push=self._push, attachments=attachments or [])
        from frontend.ui_web.agent_modes import HELD_FOR_MODEL

        if run_id == HELD_FOR_MODEL:
            return {"run_id": "", "held_for_model": "1"}
        if run_id == "queued":  # the chat runs: the send waits as a steer / next turn, never a Stop
            return {"ok": True, "queued": True, "note": "Sent — the agent takes it at its next step"}
        return {"run_id": run_id}

    def run_held_messages(self, conv_id: str, model: str, coding_agent: str = "") -> dict[str, Any]:
        """The composer picked a model for a chat whose messages waited for one: save the
        pick the way a send does, then run them in order."""
        from frontend.ui_web.agent_modes import release_held_for_model

        if not (model or "").strip() or (model or "").strip().lower() == "default":
            return {"ok": False, "error": "Pick a model first."}
        _sync_composer_selection(conv_id, model, coding_agent)
        return {"ok": True, "ran": release_held_for_model(conv_id)}

    def held_for_model(self, conv_id: str) -> dict[str, Any]:
        """How many messages this chat keeps until a model is picked."""
        from frontend.ui_web.agent_modes import held_for_model

        return {"ok": True, "held": held_for_model(conv_id)}

    def continue_interrupted(
        self,
        conv_id: str,
        mode: str,
        model: str,
        file_path: str = "",
        coding_agent: str = "",
    ) -> dict[str, str]:
        """Resume the last interrupted turn without appending a user message."""
        from frontend.ui_web.group_orchestrator import is_group_conversation

        if file_path:
            _pa.ensure_conversation_file_path(conv_id, file_path)
        _sync_composer_selection(conv_id, model, coding_agent)
        conv = _pa.load_conversation(conv_id)
        if conv is None or (conv is not None and is_group_conversation(conv)):
            return {"run_id": ""}
        run_id = _pa.run_message(conv_id, "", mode, model, push=self._push, resume=True)
        return {"run_id": run_id}

    def resend_last_user_message(
        self,
        conv_id: str,
        text: str,
        mode: str,
        model: str,
        file_path: str = "",
        attachments: list[dict[str, Any]] | None = None,
        coding_agent: str = "",
    ) -> dict[str, str]:
        """Cursor-style edit + resend: rewind to the last user turn and rerun.

        Only the most recent user message is editable, so we never need to map a
        synthetic UI row id back to a stored message — we just drop the last
        ``role == "user"`` entry (and everything after it) and let the normal
        send path append the edited message and start a fresh run.
        """
        conv = _pa.load_conversation(conv_id)
        if not conv:
            return {"run_id": ""}
        # Cursor-style edit mid-answer: cancel the live run, then rewind + rerun.
        if conv_id in _pa.list_running_agents():
            _pa.cancel_agent(conv_id)
            _pa.wait_for_idle(conv_id, 2.0)
            if conv_id in _pa.list_running_agents():
                return {"run_id": ""}
        last_user = next(
            (i for i in range(len(conv.messages) - 1, -1, -1) if conv.messages[i].get("role") == "user"),
            None,
        )
        if last_user is not None:
            conv.messages = conv.messages[:last_user]
            _pa.save_conversation(conv)
        return self.send_message(conv_id, text, mode, model, file_path, attachments, coding_agent)

    def rewind_to_message(self, conv_id: str, row_id: int, text: str = "") -> dict[str, Any]:
        """Restart from here: erase this user message and everything after it, as if never sent.

        ``row_id`` is the message's UI row id (stored index * ROW_ID_STEP); ``text`` must still
        match it, so a chat changed meanwhile is refused instead of cut in the wrong place. A
        coding agent's resumed session still holds the erased turns: it is dropped, and the
        next turn starts a new one from the kept messages. The composer takes the text back.
        """
        from frontend.ui_web.chat_rows import ROW_ID_STEP

        conv = _pa.load_conversation(conv_id)
        if not conv:
            return {"ok": False, "error": "Chat not found."}
        if conv_id in _pa.list_running_agents():
            _pa.cancel_agent(conv_id)
            _pa.wait_for_idle(conv_id, 2.0)
            if conv_id in _pa.list_running_agents():
                return {"ok": False, "error": "The agent is still stopping. Try again in a moment."}
            conv = _pa.load_conversation(conv_id) or conv
        try:
            index = int(row_id) // ROW_ID_STEP
        except (TypeError, ValueError):
            return {"ok": False, "error": "Unknown message."}
        msg = conv.messages[index] if 0 <= index < len(conv.messages) else None
        stored = str((msg or {}).get("text", (msg or {}).get("content", "")) or "") if isinstance(msg, dict) else ""
        if not isinstance(msg, dict) or msg.get("role") != "user" or stored.strip() != (text or "").strip():
            return {"ok": False, "error": "This chat changed. Reload it and try again."}
        conv.messages = conv.messages[:index]
        if str(getattr(conv, "upstream_session_id", "") or "").strip():
            from backend.agent.coding_agents.session_refresh import mark_rewound

            mark_rewound(conv)
        try:
            from backend.agent.prompt_cache import invalidate_conv_cache

            invalidate_conv_cache(conv)
        except Exception:
            pass
        _pa.save_conversation(conv)
        return {"ok": True, "kept": index}

    def get_context_usage(
        self,
        conv_id: str,
        model: str,
        mode: str = "agent",
        draft: str = "",
        include_content: bool = False,
    ) -> dict[str, Any]:
        from frontend.ui_web.context_tokens import compute_context_usage

        try:
            return compute_context_usage(
                conv_id, model, mode=mode, draft_text=draft, include_content=include_content
            )
        except Exception as exc:
            import logging

            _pa.logging.getLogger(__name__).exception("get_context_usage failed for %s: %s", conv_id, exc)
            from frontend.ui_web.context_tokens import context_limit_for_model

            limit = context_limit_for_model(model) or 0
            return {
                "used_tokens": 0,
                "context_limit": limit,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "breakdown": [],
                "omitted": [],
                "error": str(exc),
            }

    def reset_context(self, conv_id: str, segments: list[str], mode: str = "agent", model: str = "") -> dict[str, Any]:
        from frontend.ui_web.context_control import reset_context

        return reset_context(conv_id, segments, model=model, mode=mode)

    def restore_context(self, conv_id: str, segments: list[str], mode: str = "agent", model: str = "") -> dict[str, Any]:
        from frontend.ui_web.context_control import restore_context

        return restore_context(conv_id, segments, model=model, mode=mode)

    def get_session_files(self, conv_id: str) -> list[dict[str, Any]]:
        from frontend.ui_web.session_files import compute_session_files

        return compute_session_files(conv_id)

    def cancel_agent(self, conv_id: str = "") -> bool:
        from frontend.ui_web.group_orchestrator import cancel_group_run, list_group_running_ids

        if conv_id:
            cancel_group_run(conv_id)
        else:
            for gid in list_group_running_ids():
                cancel_group_run(gid)
        _pa.cancel_agent(conv_id or None)
        return True

    def steer_message(self, conv_id: str, steer_id: str, text: str) -> dict[str, Any]:
        """Send now on a queued message: the running turn takes it at its next tool call
        (frontend/ui_web/steer.py). ok False: no turn runs, send it as a normal message."""
        from frontend.ui_web import steer

        return {"ok": steer.post(conv_id, steer_id, text)}

    def list_running_agents(self) -> list[str]:
        from frontend.ui_web.group_orchestrator import list_group_running_ids

        ids = list(_pa.list_running_agents())
        for gid in list_group_running_ids():
            if gid not in ids:
                ids.append(gid)
        return ids

    def wait_for_agent_idle(self, conv_id: str, timeout: float = 2.0) -> bool:
        return _pa.wait_for_idle(conv_id, timeout)

    def voice_transcribe_audio(self, b64_audio: str, mime: str = "audio/webm") -> dict[str, Any]:
        """Batch Whisper transcription (prefer via bridge_job_start)."""
        from backend.voice.transcription import transcribe_audio

        return transcribe_audio(str(b64_audio or ""), str(mime or "audio/webm"))

    def voice_create_realtime_token(self) -> dict[str, Any]:
        """Mint an ephemeral Realtime client secret for browser streaming STT."""
        from backend.voice.transcription import create_realtime_token

        return create_realtime_token()

    def voice_win_tts_start(self, text: str, voice_id: str = "") -> dict[str, Any]:
        from backend.voice.windows_tts import start

        return start(str(text or ""), str(voice_id or ""))

    def voice_win_tts_voices_start(self) -> dict[str, Any]:
        from backend.voice.windows_tts import voices_start

        return voices_start()

    def voice_win_tts_poll(self, job_id: str) -> dict[str, Any]:
        from backend.voice.windows_tts import poll

        return poll(str(job_id or ""))

    def voice_win_stt_prewarm(self) -> dict[str, Any]:
        """Spawn the Windows speech helper early so the first mic press starts fast."""
        from backend.voice.windows_speech import prewarm

        return prewarm()

    def voice_win_stt_start(self, lang: str = "") -> dict[str, Any]:
        """Start Windows dictation (desktop mic, no key); returns session + poll cursor."""
        from backend.voice.windows_speech import start_session

        return start_session(str(lang or ""))

    def voice_win_stt_poll(self, session: str, cursor: int = 0, wait_ms: int = 400) -> dict[str, Any]:
        """Long-poll Windows dictation events (interim / final / ended)."""
        from backend.voice.windows_speech import poll_session

        return poll_session(str(session or ""), cursor, wait_ms)

    def voice_win_stt_stop(self, session: str) -> dict[str, Any]:
        from backend.voice.windows_speech import stop_session

        return stop_session(str(session or ""))

    def voice_win_stt_cancel(self, session: str) -> dict[str, Any]:
        from backend.voice.windows_speech import cancel_session

        return cancel_session(str(session or ""))

    def voice_open_windows_settings(self, page: str) -> dict[str, Any]:
        """Open Windows Settings → speech / microphone privacy so the user can turn it on."""
        from backend.voice.windows_speech import open_windows_settings

        return open_windows_settings(str(page or ""))

    def voice_summarize_reply(self, assistant_text: str, model: str = "") -> dict[str, Any]:
        """Short spoken summary via the cheap voice model (prefer via bridge_job_start)."""
        from backend.voice.summary import summarize_for_speech

        return summarize_for_speech(str(assistant_text or ""), model=str(model or ""))
