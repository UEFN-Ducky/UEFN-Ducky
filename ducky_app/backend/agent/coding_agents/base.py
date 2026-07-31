"""Coding-agent adapter protocol and registry."""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# Host-owned aliases only. Vendor aliases come from plugin register(aliases=[…]).
_HOST_CODING_AGENT_ALIASES = {
    "embedded": "ducky",
    "default": "ducky",
}


def _plugin_coding_agent_aliases() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        from backend.uefn_plugins.host import (
            get_coding_agent_registration,
            registered_coding_agent_ids,
        )

        for aid in registered_coding_agent_ids():
            reg = get_coding_agent_registration(aid) or {}
            for alias in reg.get("aliases") or []:
                key = str(alias or "").strip().lower().replace("-", "_")
                if key:
                    out[key] = aid
    except Exception:
        pass
    return out


def coding_agent_label(agent_id: str) -> str:
    aid = (agent_id or "").strip().lower().replace("-", "_")
    if aid == "ducky":
        return "Ducky"
    try:
        from backend.uefn_plugins.host import get_contributions

        for row in get_contributions().get("llm_coding_agents") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("id") or "").strip().lower().replace("-", "_") == aid:
                label = str(row.get("label") or "").strip()
                if label:
                    return label
    except Exception:
        pass
    return aid.replace("_", " ").title() if aid else "Agent"


# Back-compat for importers that still read these names.
CODING_AGENT_IDS = ("ducky",)  # dynamic agents come from the registry
CODING_AGENT_LABELS: dict[str, str] = {"ducky": "Ducky"}


def contributed_coding_agents() -> tuple[str, ...]:
    """Enabled gateway coding-agent ids that also have a registered factory."""
    try:
        from backend.uefn_plugins.host import (
            ensure_plugins_loaded_async,
            get_coding_agent_registration,
            get_contributions,
            plugins_ready,
        )

        # Never block PanelSettings.save / Store enable on sync register repair.
        # ensure_plugins_loaded() → unity-mcp nested MCP sync hung Discord toggles
        # for minutes (UI snapped back). Boot still repairs via async load.
        if not plugins_ready():
            ensure_plugins_loaded_async()
        out: list[str] = []
        seen: set[str] = set()
        for row in get_contributions().get("llm_coding_agents") or []:
            if not isinstance(row, dict):
                continue
            aid = str(row.get("id") or "").strip().lower().replace("-", "_")
            if not aid or aid in seen:
                continue
            if get_coding_agent_registration(aid) is None:
                continue
            seen.add(aid)
            out.append(aid)
        return tuple(out)
    except Exception:
        return ()


def listed_external_coding_agents() -> tuple[str, ...]:
    return contributed_coding_agents()


def normalize_coding_agent(value: str | None) -> str:
    key = (value or "ducky").strip().lower().replace("-", "_").replace(" ", "_")
    key = _HOST_CODING_AGENT_ALIASES.get(key, key)
    key = _plugin_coding_agent_aliases().get(key, key)
    if key == "ducky":
        return "ducky"
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        if get_coding_agent_registration(key) is not None:
            return key
    except Exception:
        pass
    if key in contributed_coding_agents():
        return key
    return "ducky"


@dataclass(frozen=True)
class CodingAgentCapabilities:
    terminal_agent: bool = False
    chat_api: bool = False
    a2a: bool = True
    mcp_inject: bool = False
    needs_api_key: bool = False
    needs_cli: bool = False
    resume: bool = False
    """True when the CLI/SDK can resume an upstream session across turns."""


@dataclass
class CodingAgentInfo:
    id: str
    label: str
    enabled: bool
    available: bool
    status: str
    cli_path: str = ""
    default_args: str = ""
    capabilities: CodingAgentCapabilities = field(default_factory=CodingAgentCapabilities)
    models: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        install_help = ""
        plugin_id = ""
        shows_thinking = False
        try:
            from backend.uefn_plugins.host import get_coding_agent_registration

            reg = get_coding_agent_registration(self.id) or {}
            install_help = str(reg.get("install_help") or "")
            plugin_id = str(reg.get("plugin_id") or "")
            shows_thinking = bool(
                reg.get("shows_thinking_effort") or callable(reg.get("thinking_env"))
            )
        except Exception:
            pass
        return {
            "id": self.id,
            "label": self.label,
            "enabled": self.enabled,
            "available": self.available,
            "status": self.status,
            "cli_path": self.cli_path,
            "default_args": self.default_args,
            "install_help": install_help,
            "shows_thinking_effort": shows_thinking,
            "plugin_id": plugin_id,
            "capabilities": {
                "terminal_agent": self.capabilities.terminal_agent,
                "chat_api": self.capabilities.chat_api,
                "a2a": self.capabilities.a2a,
                "mcp_inject": self.capabilities.mcp_inject,
                "needs_api_key": self.capabilities.needs_api_key,
                "needs_cli": self.capabilities.needs_cli,
            },
            "models": list(self.models),
        }


@dataclass
class CodingAgentLaunchResult:
    ok: bool
    terminal_session_id: str = ""
    upstream_session_id: str = ""
    output_tail: str = ""
    reply_text: str = ""
    error: str = ""
    status: str = ""
    streamed: bool = False
    """True when the adapter already pushed text deltas live (no re-stream)."""
    usage: dict[str, Any] = field(default_factory=dict)
    """Real usage from the CLI's result event: input/output/cache tokens,
    cost_usd, num_turns, model, context_tokens (window used this turn)."""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    """Ordered thinking/text/tool_call blocks (embedded-agent format) so the
    turn's steps survive a panel reload, not just the final reply text."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "terminal_session_id": self.terminal_session_id,
            "upstream_session_id": self.upstream_session_id,
            "output_tail": self.output_tail,
            "reply_text": self.reply_text,
            "error": self.error,
            "status": self.status,
            "usage": dict(self.usage),
            "blocks": list(self.blocks),
        }


class CodingAgentAdapter(Protocol):
    id: str
    label: str
    capabilities: CodingAgentCapabilities

    def detect(self, settings: Any) -> CodingAgentInfo: ...

    def launch(
        self,
        *,
        prompt: str,
        system_prompt: str,
        cwd: str,
        conv_id: str,
        model: str,
        mcp_config_path: str,
        extra_args: str,
        cli_path: str,
        env: dict[str, str],
        push: Any,
        session_id: str = "",
        run_id: str = "",
        cancel: Any = None,
        timeout_s: float = 0.0,
        image_paths: list[str] | None = None,
    ) -> CodingAgentLaunchResult: ...


def which_cli(name: str, override: str = "") -> str:
    raw = (override or "").strip()
    if raw:
        p = Path(raw)
        if p.is_file():
            return str(p.resolve())
        found = shutil.which(raw)
        if found:
            return found
        return ""
    found = shutil.which(name)
    return found or ""


def get_adapter(agent_id: str) -> CodingAgentAdapter | None:
    aid = normalize_coding_agent(agent_id)
    if aid == "ducky":
        return None
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        reg = get_coding_agent_registration(aid)
        if reg is None:
            return None
        factory = reg.get("factory")
        if not callable(factory):
            return None
        return factory()
    except Exception:
        return None


def list_coding_agents(settings: Any | None = None) -> list[CodingAgentInfo]:
    from frontend.settings import PanelSettings

    s = settings or PanelSettings.load()
    out: list[CodingAgentInfo] = [
        CodingAgentInfo(
            id="ducky",
            label="Ducky",
            enabled=True,
            available=True,
            status="Embedded agent",
            capabilities=CodingAgentCapabilities(chat_api=True, a2a=True),
            models=[],
        )
    ]
    for aid in listed_external_coding_agents():
        adapter = get_adapter(aid)
        if adapter is None:
            continue
        out.append(adapter.detect(s))
    return out


# Short-TTL cache for detect_all. Each detect() does filesystem PATH scans
# (shutil.which per CLI) costing ~400ms; the panel calls this on every chat
# open/close and stream tick, so without a cache a burst spawns dozens of
# concurrent pywebview bridge threads that each block returning through the
# single WebView2 UI thread — a thundering-herd freeze. CLI/key availability
# changes rarely, so a few seconds of staleness is fine; settings/key writes
# invalidate explicitly.
_DETECT_TTL_SEC = 5.0
_detect_lock = threading.Lock()
_detect_cache: dict[str, Any] | None = None
_detect_cache_at = 0.0


def invalidate_detect_cache() -> None:
    """Force the next detect_all() to re-probe CLIs (call after settings/key writes)."""
    global _detect_cache, _detect_cache_at
    with _detect_lock:
        _detect_cache = None
        _detect_cache_at = 0.0


def detect_all(settings: Any | None = None) -> dict[str, Any]:
    # Only cache the default/hot path (settings is None). Callers that pass an
    # explicit settings object want a fresh probe of that specific config.
    if settings is not None:
        agents = list_coding_agents(settings)
        return {"agents": [a.to_dict() for a in agents]}
    global _detect_cache, _detect_cache_at
    now = time.monotonic()
    with _detect_lock:
        if _detect_cache is not None and (now - _detect_cache_at) < _DETECT_TTL_SEC:
            return _detect_cache
    agents = list_coding_agents(None)
    payload = {"agents": [a.to_dict() for a in agents]}
    with _detect_lock:
        _detect_cache = payload
        _detect_cache_at = time.monotonic()
    return payload
