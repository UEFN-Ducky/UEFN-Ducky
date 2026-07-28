"""Validated panel settings (dataclass + JSON persistence)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

# Serialize settings writes within this process. The file is also written by other
# processes (a second panel/MCP instance), so save() additionally uses a per-writer temp
# file + retry so concurrent saves can't corrupt each other or raise WinError 5.
_SAVE_LOCK = threading.Lock()


def default_app_data_dir() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return resolve_app_data_dir()
    return Path.home() / ".uefn-ducky"


# Listener / bridge port for the packaged panel — not user-configurable in the UI.
PANEL_LISTENER_PORT = 4200


@dataclass
class PanelSettings:
    """User preferences persisted under %LOCALAPPDATA%\\UEFN-Ducky\\panel_settings.json (Windows)."""

    port: int = 4200
    """Always :data:`PANEL_LISTENER_PORT` when using this panel (Apply / Save rewrite disk)."""

    antigravity_config_path: str = ""
    """Override path to Antigravity mcp_config.json if non-default."""

    agent_provider: str = ""
    """LLM provider id from an installed gateway (anthropic/openai/gemini/ollama)."""

    agent_model: str = ""
    """Model id; empty uses provider default."""

    default_model: str = ""
    """Qualified default model ("backend:model_id", e.g. "anthropic:claude-sonnet-4").

    Every chat and Ducky that has no model of its own resolves to this at
    create/spawn time. A model set on a Ducky profile or picked in a chat
    composer always overrides it.
    """

    uefn_project_root: str = ""
    """UEFN project folder for workspace file tools."""

    verse_editor_enabled: bool = True
    """When false, file tabs use read-only preview (legacy behavior)."""

    verse_diagnostics_cache_enabled: bool = True
    """When true, store scan results under AppData (verse_diagnostics/<project-slug>/) per project."""

    verse_diagnostics_auto_check: bool = True
    """When true, re-check Verse files on open, edit, and after AI writes (incremental when cache on)."""

    show_hidden_project_files: bool = False
    """When true, show engine folders and binary assets hidden from the sidebar by default."""

    terminals_enabled: bool = True
    """When false, hide terminal UI and block opening new terminal tabs."""

    agent_max_turns: int = 25
    """Max agent tool loop iterations per user message."""

    appearance_foundation: dict[str, str] = field(default_factory=dict)
    """Foundation colors: accent, bg, surface, text, border."""

    appearance_overrides: dict[str, str] = field(default_factory=dict)
    """Custom CSS token overrides (token id without --)."""

    appearance_status_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    """Per-status color overrides (error, success, info, warn)."""

    appearance_profiles: list[dict[str, Any]] = field(default_factory=list)
    """Named appearance snapshots: id, name, foundation, overrides, status_overrides."""

    appearance_active_profile_id: str = ""
    """Id of the profile currently applied (empty = unsaved custom look)."""

    appearance_effect_id: str = ""
    """Background effect id: empty = none, ``matrix`` = built-in, or ``plugin:<id>:<effect>``."""

    appearance_effects_enabled: bool = True
    """When False, keep the selected effect_id but do not mount the background animation."""

    appearance_skin_id: str = ""
    """Full chrome skin id: empty = none, or ``plugin:<pluginId>:<skinId>``."""

    appearance_sounds: dict[str, Any] = field(default_factory=dict)
    """Sound effects: {enabled, volume, mapping: {hookId: soundRef}}."""

    appearance_profile_patches: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Per plugin-profile customizations (foundation/overrides/effect/skin) keyed by profile id."""

    agent_profiles: list[dict[str, Any]] = field(default_factory=list)
    """User-created agent profile templates: id, name, ducky_style, ducky_personality, disabled_packs, disabled_tool_ids."""

    agent_profile_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Per-bundled-profile user overrides keyed by bundled id."""

    hidden_bundled_agent_profile_ids: list[str] | None = None
    """Bundled profile ids hidden from the settings library. None = hide all built-ins (default)."""

    default_enabled_skills: list[str] = field(default_factory=list)
    """Legacy flat skill list — ignored (all packs available by default)."""

    default_enabled_packs: list[str] = field(default_factory=list)
    """Legacy allowlist — ignored. Prefer default_disabled_packs."""

    default_enabled_subskills: dict[str, list[str]] = field(default_factory=dict)
    """Legacy per-pack subskill allowlist — ignored (lazy-loaded)."""

    default_disabled_packs: list[str] = field(default_factory=list)
    """Skill pack ids denied by default for new duckies. Empty = all packs available."""

    enabled_mcp_plugins: list[str] = field(default_factory=list)
    """MCP plugin pack ids enabled for embedded Ducky agents."""

    enabled_uefn_plugins: list[str] | None = None
    """In-process desktop plugin ids (Store/local). None = migrate from each plugin's default_enabled."""

    trusted_local_uefn_plugins: list[str] = field(default_factory=list)
    """Local sideloaded plugin ids the user has accepted the unofficial-plugin warning for."""

    disabled_builtin_toolsets: list[str] = field(default_factory=list)
    """Built-in tool group ids disabled by default (builtin_ducky). Empty = all on."""

    memory_auto_compress: bool = True
    """Auto-compress older chat turns into a rolling summary when over threshold."""

    prompt_dedupe_exact_blocks: bool = False
    """Strip exact duplicate paste-sized blocks from the user message before send/resend."""

    memory_keep_last_messages: int = 20
    """Live messages always sent to the model after compression."""

    memory_compress_messages: int = 40
    """Auto-compress when conversation message count reaches this."""

    memory_compress_tokens: int = 80_000
    """Auto-compress when estimated context tokens reach this."""

    memory_index_max_chars: int = 2_500
    """Max chars for the filtered project-memory index in the system prompt."""

    memory_summary_model: str = ""
    """Cheap API model for chat summaries (empty = Voice / Default model fallback)."""

    prompt_caching_enabled: bool = True
    """Legacy fallback when Anthropic/OpenAI plugin prefs omit promptCaching."""

    freeze_prompt_prefix: bool = True
    """Freeze skills/rules/MCP on turn 1 for every provider (Ducky-owned stable prefix)."""

    anthropic_extended_cache_ttl: bool = False
    """Legacy fallback when Anthropic plugin prefs omit extendedCacheTtl."""

    tool_result_format: str = "toon"
    """Tool results sent to the LLM: toon (default) or json."""

    coding_agents: dict[str, Any] = field(default_factory=dict)
    """BYOA coding-agent config: claude_code / codex / cursor → enabled, cli_path, default_args."""

    default_coding_agent: str = "ducky"
    """Default coding-agent path for new chats: ducky | claude_code | codex | cursor."""

    duckyos_base_url: str = ""
    """DuckyOS tenant URL (empty = default https://uefnducky.org). Non-secret."""

    allow_settings_write: bool = True
    """When false, ducky_settings_set (agent-driven settings writes) is refused."""

    allow_agent_clicks: bool = False
    """When true, ducky_ui_click may click panel controls programmatically. Off = spotlight + user click only."""

    voice_enabled: bool = False
    """When true, speak assistant replies (summary or speak-along)."""

    voice_spoken_style: str = "summary"
    """How replies are spoken: summary | speak_along."""

    voice_summary_model: str = ""
    """Qualified cheap model for spoken summaries ("backend:model_id"). Empty = default_model."""

    voice_default_voice: str = ""
    voice_default_speed: float = 1.0
    """Default TTS voice id (builtin:<name> or plugin:<pid>:<id>). Empty = browser default."""

    voice_live_manual_send: bool = False
    """Live voice: accumulate speech until the user presses Send (no auto-send on pause)."""

    voice_process_talk: float = 0.7
    """How much live voice narrates tools/thinking (0 = mute process chatter, 1 = full)."""

    mic_permission: str = "ask"
    """App-level mic consent: ask | allow | block (before getUserMedia)."""

    mic_device_id: str = ""
    """Preferred audioinput deviceId from enumerateDevices. Empty = default."""

    output_device_id: str = ""
    """Preferred audiooutput deviceId from enumerateDevices. Empty = default."""

    tts_volume: float = 1.0
    """Spoken-reply volume 0..1."""

    audio_muted: bool = False
    """Master mute for SFX + TTS playback."""

    walkthrough_completed: dict[str, bool] = field(default_factory=dict)
    """Product tour completion flags keyed by tour id (app.shell, settings.store, plugin.*)."""

    def validate(self) -> None:
        if self.port < 1 or self.port > 65535:
            raise ValueError("port must be 1-65535")
        if self.agent_max_turns < 1 or self.agent_max_turns > 100:
            raise ValueError("agent_max_turns must be 1-100")
        # Builtins + known gateway runtime ids (plugin may be off; keep legacy settings valid).
        if self.agent_provider and self.agent_provider not in (
            "anthropic",
            "gemini",
            "openai",
            "ollama",
        ):
            raise ValueError("agent_provider must be empty or a gateway id")
        if self.tool_result_format not in ("toon", "json"):
            raise ValueError("tool_result_format must be toon or json")
        if self.voice_spoken_style not in ("summary", "speak_along"):
            self.voice_spoken_style = "summary"
        if self.mic_permission not in ("ask", "allow", "block"):
            self.mic_permission = "ask"
        try:
            self.tts_volume = max(0.0, min(1.0, float(self.tts_volume)))
        except (TypeError, ValueError):
            self.tts_volume = 1.0
        try:
            self.voice_process_talk = max(0.0, min(1.0, float(self.voice_process_talk)))
        except (TypeError, ValueError):
            self.voice_process_talk = 0.7
        self.mic_device_id = str(self.mic_device_id or "").strip()
        self.output_device_id = str(self.output_device_id or "").strip()
        from backend.agent.coding_agents.base import (
            contributed_coding_agents,
            normalize_coding_agent,
        )

        self.default_coding_agent = normalize_coding_agent(self.default_coding_agent)
        allowed = {"ducky", *contributed_coding_agents()}
        if self.default_coding_agent not in allowed:
            self.default_coding_agent = "ducky"
        if self.coding_agents is None or not isinstance(self.coding_agents, dict):
            self.coding_agents = {}
        if not isinstance(self.walkthrough_completed, dict):
            self.walkthrough_completed = {}
        else:
            self.walkthrough_completed = {
                str(k): True for k, v in self.walkthrough_completed.items() if v
            }

    def settings_path(self) -> Path:
        return default_app_data_dir() / "panel_settings.json"

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> PanelSettings:
        from backend.agent.secrets import reject_api_key_fields

        data = reject_api_key_fields(data)
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        # Drop null-valued keys so the dataclass default applies — a persisted
        # `null` (older schema, manual edit) must not override a field default
        # with None and break callers that expect a list/dict.
        kwargs = {k: v for k, v in data.items() if k in known and v is not None}
        return cls(**kwargs)

    def _hidden_bundled_counts_as_override(self) -> bool:
        hidden = self.hidden_bundled_agent_profile_ids
        if hidden is None:
            return False
        from frontend.agent_profiles import bundled_profile_ids

        return frozenset(hidden) != bundled_profile_ids()

    def _has_overrides(self) -> bool:
        return bool(
            self.antigravity_config_path.strip()
            or self.uefn_project_root.strip()
            or self.agent_provider != ""
            or self.agent_model.strip()
            or self.default_model.strip()
            or self.agent_max_turns != 25
            or self.appearance_foundation
            or self.appearance_overrides
            or self.appearance_status_overrides
            or self.appearance_profiles
            or self.appearance_active_profile_id.strip()
            or self.appearance_effect_id.strip()
            or not self.appearance_effects_enabled
            or self.appearance_skin_id.strip()
            or self.appearance_sounds
            or self.appearance_profile_patches
            or self.agent_profiles
            or self.agent_profile_overrides
            or self._hidden_bundled_counts_as_override()
            or not self.verse_diagnostics_cache_enabled
            or not self.verse_diagnostics_auto_check
            or self.show_hidden_project_files
            or not self.terminals_enabled
            or self.default_disabled_packs
            or self.default_enabled_packs
            or self.default_enabled_subskills
            or self.default_enabled_skills
            or self.enabled_mcp_plugins
            # None = migrate from default_enabled; [] = user disabled every plugin
            # (must persist — bool([]) is False and used to wipe the settings file).
            or self.enabled_uefn_plugins is not None
            or self.trusted_local_uefn_plugins
            or self.disabled_builtin_toolsets
            or not self.memory_auto_compress
            or self.prompt_dedupe_exact_blocks
            or self.memory_keep_last_messages != 20
            or self.memory_compress_messages != 40
            or self.memory_compress_tokens != 80_000
            or self.memory_index_max_chars != 2_500
            or self.memory_summary_model.strip()
            or not self.prompt_caching_enabled
            or not self.freeze_prompt_prefix
            or self.anthropic_extended_cache_ttl
            or self.tool_result_format != "toon"
            or self.coding_agents
            or self.default_coding_agent != "ducky"
            or self.duckyos_base_url.strip()
            or not self.allow_settings_write
            or self.allow_agent_clicks
            or bool(self.walkthrough_completed)
        )

    def save(self) -> None:
        to_store = replace(self, port=PANEL_LISTENER_PORT)
        to_store.validate()
        path = to_store.settings_path()
        # Don't litter %LOCALAPPDATA% with a settings file that holds only defaults.
        if not to_store._has_overrides():
            with _SAVE_LOCK:
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(to_store.to_json_dict(), indent=2)
        with _SAVE_LOCK:
            # Unique temp per writer: a concurrent save (another thread, or a second panel
            # process) must not clobber the temp we're about to rename. A shared
            # "<name>.tmp" made two saves race and the loser hit WinError 5.
            fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
            )
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                # os.replace is atomic, but on Windows it can transiently fail with
                # PermissionError if another process holds the destination open (a reader,
                # or antivirus). Retry briefly before giving up.
                for attempt in range(5):
                    try:
                        os.replace(tmp, path)
                        return
                    except PermissionError:
                        if attempt == 4:
                            raise
                        time.sleep(0.05 * (attempt + 1))
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass

    @classmethod
    def load(cls) -> PanelSettings:
        path = default_app_data_dir() / "panel_settings.json"
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return cls()
            from backend.agent.secrets import reject_api_key_fields

            data = reject_api_key_fields(data)
            raw = cls.from_json_dict(data)
            # builtin_uefn removed — domain tools are Store plugins now.
            disabled = [
                g
                for g in (getattr(raw, "disabled_builtin_toolsets", None) or [])
                if str(g).strip() != "builtin_uefn"
            ]
            hidden = (
                []
                if "hidden_bundled_agent_profile_ids" not in data
                else list(getattr(raw, "hidden_bundled_agent_profile_ids", None) or [])
            )
            return replace(
                raw,
                port=PANEL_LISTENER_PORT,
                disabled_builtin_toolsets=disabled,
                hidden_bundled_agent_profile_ids=hidden,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            return cls()


def panel_exe_dir() -> Path:
    """Directory containing the running panel (frozen) or repo when running as script."""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def apply_workspace_env(project_root: str) -> None:
    """Set UEFN_VSCODE_WORKSPACE_FOLDERS and project root for MCP + UEFN listener."""
    root = (project_root or "").strip()
    if not root:
        return
    import json as _json

    from frontend.ui_web.verse_editor.lsp.verse_workspace import discover_verse_workspace

    ws = discover_verse_workspace(root)
    folder_paths = [f["path"] for f in (ws.get("workspace_folders") or []) if isinstance(f, dict) and f.get("path")]
    os.environ["UEFN_VSCODE_WORKSPACE_FOLDERS"] = _json.dumps(folder_paths or [root])
    os.environ["UEFN_DUCKY_PROJECT_ROOT"] = root
    try:
        from frontend.atomic_json import write_json_atomic

        cfg_path = default_app_data_dir() / "config.json"
        existing: dict = {}
        if cfg_path.is_file():
            try:
                existing = _json.loads(cfg_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
        existing["project_root"] = root
        existing.setdefault("port", PANEL_LISTENER_PORT)
        write_json_atomic(cfg_path, existing)
    except Exception:
        pass

