"""The fixed list of anonymous usage events UEFN Ducky may report.

Every event is a name from ``EVENTS`` and props whose values are one of a short
enum or a bucket label. A prop that is not listed, a value outside its enum, or
free text of any kind is dropped here before it can be queued, and the Store
plugin checks the same list again on arrival. Nothing in this file can carry a
path, a project name, a message, an email or a hostname.
"""

from __future__ import annotations

import re
from typing import Any

# A bucket: (upper bound inclusive, label); the last entry's bound is None.
Buckets = tuple[tuple[float | None, str], ...]

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
# Catalog identifiers (tool, skill pack, plugin slug, error code): short slugs only.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

DURATION_S: Buckets = ((10, "10s"), (60, "1m"), (300, "5m"), (1200, "20m"), (None, "more"))
TOOL_S: Buckets = ((1, "1s"), (10, "10s"), (60, "1m"), (None, "more"))
# How long a run lasted before it died (app_unclean_exit): a start-up crash is "1m".
DIED_AFTER_S: Buckets = ((60, "1m"), (300, "5m"), (1800, "30m"), (7200, "2h"), (28800, "8h"), (None, "more"))
UPTIME_S: Buckets = ((300, "5m"), (1800, "30m"), (7200, "2h"), (28800, "8h"), (None, "more"))
STUCK_S: Buckets = ((60, "60s"), (300, "5m"), (1200, "20m"), (None, "more"))
TOOL_CALLS: Buckets = ((0, "0"), (5, "1-5"), (20, "6-20"), (None, "more"))
NODES: Buckets = ((5, "1-5"), (20, "6-20"), (None, "more"))
STARTUP_S: Buckets = ((2, "2s"), (5, "5s"), (15, "15s"), (None, "more"))
IDLE_CPU_PCT: Buckets = ((1, "1pct"), (5, "5pct"), (None, "more"))
AGENTS_N: Buckets = ((0, "0"), (1, "1"), (4, "2-4"), (None, "more"))
MEMORY_MB: Buckets = ((500, "500mb"), (1024, "1gb"), (2048, "2gb"), (None, "more"))
INSTALL_AGE_S: Buckets = ((86400, "day0"), (7 * 86400, "week1"), (30 * 86400, "month1"), (None, "older"))

PROVIDERS = ("claude", "codex", "cursor", "ollama", "ducky_ai", "other")
# The first-run setup's Connect AI step: which AI, and how it ended up connected (or not).
SETUP_AI_PROVIDERS = ("claude", "codex", "gemini", "cursor", "ollama", "ducky_ai", "other")
SETUP_AI_STATES = ("plan", "key", "account", "not_logged_in", "not_installed", "none")
MODEL_FAMILIES = ("claude", "gpt", "gemini", "ollama", "ducky_ai", "other")
YES_NO = ("yes", "no")
WHERE = ("agent", "ui", "bridge", "uefn", "store", "sync", "workflow", "other")
# What kind of failure an error was, read from its message on the PC (the message never leaves).
ERROR_KINDS = ("session_dead", "busy", "timeout", "offline", "rate_limit", "auth", "too_long", "bad_picture",
               "bad_request", "server_error", "memory", "permission", "not_found", "crash", "other")
# boot_report stages (webview_app._boot_trace names); a run's last one is sent with app_unclean_exit.
BOOT_STAGES = ("launch", "handoff", "handoff_exit", "reclaim_claim", "webview_import_patches",
               "import_panel_api", "panel_api_init", "http_server", "create_window",
               "ready_for_webview_start", "window_shown", "page_started", "other")
# Why a tool call failed: run_agent_command's "fail" word, refused = answered {"ok": false} / "ERROR".
TOOL_FAILS = ("exit_nonzero", "busy", "not_approved", "timeout", "not_found", "refused", "error")
# What a terminal command was, from its first words (cmd_kind); the command never leaves.
CMD_KINDS = ("test", "build", "git", "install", "script", "other")
# Tools whose tool.call carries cmd.
COMMAND_TOOLS = frozenset({"ducky_terminal_run", "sandbox_run"})
TABS = ("chat", "editor", "terminal", "plan", "usage", "settings", "discord", "plugin",
        "ducky-profile", "changes", "workflows", "store", "browser", "forge")

# name -> {prop: enum tuple | ("bucket", Buckets, source numeric prop) | ("slug",) | ("version",)}
EVENTS: dict[str, dict[str, Any]] = {
    "app.open": {
        "version": ("version",),
        "os": ("win10", "win11", "other"),
        "install_age": ("bucket", INSTALL_AGE_S, "install_age_s"),
    },
    "app.heartbeat": {"uefn_up": YES_NO},
    "app.close": {"uptime": ("bucket", UPTIME_S, "uptime_s")},
    "update.installed": {"from": ("version",), "to": ("version",)},
    "update.check": {
        "status": ("none", "no_release", "up_to_date", "update_available", "paused", "error"),
        "channel": ("installed", "portable", "dev"),
        "pack": YES_NO,
    },
    "setup.step": {"step": ("welcome", "plugins", "connect_ai", "defaults", "done", "closed", "skipped")},
    "setup.first_message": {"within": ("first_session", "later")},
    "setup.plugin": {"plugin_id": ("slug",), "ok": YES_NO},
    "setup.ai": {"provider": SETUP_AI_PROVIDERS, "state": SETUP_AI_STATES},
    "tour.end": {"tour": ("slug",), "how": ("done", "skipped"), "at": ("slug",)},
    "uefn.connect": {"ok": YES_NO, "reason": ("lost", "port_busy", "not_running", "listener_missing", "timeout", "other")},
    "uefn.popup_watchdog": {"kind": ("save", "lone_ok", "other")},
    "agent.login": {"provider": PROVIDERS, "ok": YES_NO, "reason": ("no_cli", "auth", "network", "other")},
    "chat.turn": {
        "provider": PROVIDERS,
        "model_family": MODEL_FAMILIES,
        "outcome": ("ok", "error", "stopped", "stuck", "timeout", "limit", "login", "interrupted", "other"),
        "duration": ("bucket", DURATION_S, "duration_s"),
        "tool_calls": ("bucket", TOOL_CALLS, "tool_calls_n"),
        "kind": ERROR_KINDS,  # failed turns only
        # Failed tool calls in the turn; a command that exited non-zero is not one.
        "tool_fails": ("bucket", TOOL_CALLS, "tool_fails_n"),
    },
    # fail: failed calls only. cmd: COMMAND_TOOLS only. after_fail: sent only as "yes" (this
    # chat's last call of the tool failed), so a retry that works reads as a recovery.
    "tool.call": {"tool": ("slug",), "ok": YES_NO, "duration": ("bucket", TOOL_S, "duration_s"),
                  "fail": TOOL_FAILS, "cmd": CMD_KINDS, "after_fail": ("yes",)},
    "skill.used": {"skill": ("slug",)},
    "workflow.run": {"outcome": ("ok", "error", "stopped"), "nodes": ("bucket", NODES, "nodes_n")},
    "plugin.install": {"plugin_id": ("slug",)},
    "plugin.remove": {"plugin_id": ("slug",)},
    "feature.open": {"tab": TABS},
    # at: the Ducky function an uncaught exception came from (backend.bridge.revive._run).
    # stage, uptime: app_unclean_exit only (the dead run's last boot stage and how long it ran).
    "error.code": {"code": ("slug",), "where": WHERE, "kind": ERROR_KINDS, "version": ("version",), "at": ("slug",),
                   "stage": BOOT_STAGES, "uptime": ("bucket", DIED_AFTER_S, "uptime_s"),
                   # app_unclean_exit: the dead run's app and WebView2 memory at its last heartbeat.
                   "memory": ("bucket", MEMORY_MB, "memory_mb"), "webview": ("bucket", MEMORY_MB, "webview_mb")},
    # tool: the call that hung (outside agents' calls; Ducky's own agent hangs as a whole turn).
    "agent.stuck": {"provider": PROVIDERS, "after": ("bucket", STUCK_S, "after_s"), "tool": ("slug",)},
    "ui.crash": {"code": ("slug",)},
    "perf.sample": {
        "startup": ("bucket", STARTUP_S, "startup_s"),
        "idle_cpu": ("bucket", IDLE_CPU_PCT, "idle_cpu_pct"),
        "memory": ("bucket", MEMORY_MB, "memory_mb"),
        "agents": ("bucket", AGENTS_N, "agents_running_n"),
    },
}

# Events the panel UI may report through ``usage_track`` (everything else is backend-only).
UI_EVENTS = frozenset({"feature.open", "setup.step", "setup.ai", "tour.end"})

# Setup, the web installer and the uninstaller (release/installer/host) report setup.open,
# setup.install, setup.result, setup.close and uninstall.done under the same install id
# (kept in HKCU\Software\UEFN Ducky, see usage_stats); the Store's list in
# plugin-uefn-ducky-store src/usage.rs holds those too.


def bucket(value: Any, buckets: Buckets) -> str | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    for bound, label in buckets:
        if bound is None or n <= bound:
            return label
    return None


def yes_no(value: Any) -> str:
    if isinstance(value, str):
        return "yes" if value.strip().lower() in ("yes", "true", "1", "ok") else "no"
    return "yes" if value else "no"


def slug(value: Any) -> str | None:
    s = str(value or "").strip().lower()
    return s if SLUG_RE.match(s) else None


def validate(name: str, props: dict[str, Any] | None = None) -> dict[str, str] | None:
    """The cleaned props for ``name``, or None when the name is unknown.

    Unknown props are dropped; a listed prop with a value outside its enum or
    bucket is dropped too. Bucket props accept the raw number under their source
    name (``duration_s``) or the label itself under the prop name (``duration``).
    """
    spec = EVENTS.get(str(name or ""))
    if spec is None:
        return None
    raw = props if isinstance(props, dict) else {}
    out: dict[str, str] = {}
    for prop, rule in spec.items():
        if rule == YES_NO:
            if prop in raw:
                out[prop] = yes_no(raw[prop])
            continue
        kind = rule[0]
        if kind == "bucket":
            _, buckets, source = rule
            label: str | None = None
            if source in raw:
                label = bucket(raw[source], buckets)
            elif prop in raw and any(raw[prop] == lab for _, lab in buckets):
                label = str(raw[prop])
            if label is not None:
                out[prop] = label
            continue
        if kind == "slug":
            if prop in raw:
                s = slug(raw[prop])
                if s is not None:
                    out[prop] = s
            continue
        if kind == "version":
            v = str(raw.get(prop) or "").strip()
            if VERSION_RE.match(v):
                out[prop] = v
            continue
        # A plain enum.
        if prop in raw:
            v = str(raw[prop] or "").strip().lower()
            if v in rule:
                out[prop] = v
    return out


def provider_of(coding_agent: str = "", provider: str = "") -> str:
    """One of PROVIDERS for a chat's coding agent / model provider."""
    agent = (coding_agent or "").strip().lower()
    if agent in ("claude_code", "claude"):
        return "claude"
    if agent in ("codex", "cursor"):
        return agent
    p = (provider or "").strip().lower()
    if p in ("ollama",):
        return "ollama"
    if p in ("ducky_ai", "duckyai", "ducky-ai", "ducky"):
        return "ducky_ai"
    if agent and agent != "ducky":
        return "other"
    if p in ("anthropic",):
        return "claude"
    if p in ("openai",):
        return "codex"
    return "other" if p else "ducky_ai"


def model_family(model: str = "", provider: str = "") -> str:
    m = (model or "").strip().lower()
    if m.startswith("claude") or provider == "anthropic":
        return "claude"
    if m.startswith(("gpt", "o1", "o3", "o4")) or provider == "openai":
        return "gpt"
    if m.startswith("gemini") or provider == "google":
        return "gemini"
    if provider == "ollama":
        return "ollama"
    if provider in ("ducky_ai", "ducky"):
        return "ducky_ai"
    return "other"


def where_of(source: str) -> str:
    """Map an error log source tag to the ``where`` enum."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", (source or "").strip().lower()) if t]

    def has(*starts: str) -> bool:
        return any(tok.startswith(starts) for tok in tokens)

    if has("bridge", "listener", "mcp", "daemon"):
        return "bridge"
    if has("agent", "model", "llm", "stream", "coding"):
        return "agent"
    if has("uefn", "verse", "fortnite", "toolset"):
        return "uefn"
    if has("store", "plugin", "install"):
        return "store"
    if has("sync", "account", "duckyos", "remote"):
        return "sync"
    if has("workflow", "automation"):
        return "workflow"
    if has("ui", "panel", "webview", "window", "chat"):
        return "ui"
    return "other"


# First match wins: "timed out waiting for listener idle" is busy, "503 overloaded" a rate limit.
_KIND_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(pattern))
    for kind, pattern in (
        ("session_dead", r"session unavailable|taskgroup|session (is )?closed"),
        ("busy", r"\bbusy\b|waiting for listener idle"),
        ("timeout", r"timed? ?out\b|timeout"),
        ("offline", r"not reachable|offline|connection (refused|reset|aborted)|winerror 100(54|61)"),
        ("rate_limit", r"\b429\b|rate.?limit|overloaded|usage limit"),
        ("auth", r"\b40[13]\b|unauthori[sz]ed|forbidden|not logged in|authentication|invalid api key"),
        # Two 400s told apart from the rest: the chat outgrew the model's window, a picture was refused.
        ("too_long", r"context.?(window|length)|prompt is too long|too many tokens|maximum context|string too long"),
        ("bad_picture", r"\bimage\b.{0,80}(exceed|too (big|large)|invalid|dimension|format|could not|unsupported)"
                        r"|invalid.?image|image_url|image\.source"),
        ("bad_request", r"\b400\b|invalid_request|bad request"),
        ("server_error", r"\b50[0-4]\b|internal server error|bad gateway"),
        ("memory", r"memoryerror|out of memory|\boom\b"),
        ("permission", r"permission denied|access is denied|permissionerror|winerror 5\b"),
        ("not_found", r"not found|no such file|filenotfounderror|does not exist"),
        ("crash", r"traceback|crash|exit code|exited with"),
    )
)


def kind_of(message: str) -> str:
    """One of ERROR_KINDS for an error message; only this word is reported."""
    text = (message or "").lower()
    for kind, rule in _KIND_RULES:
        if rule.search(text):
            return kind
    return "other"


_CMD_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(pattern))
    for kind, pattern in (
        ("test", r"\b(pytest|vitest|jest|unittest|cargo (\+\S+ )?(test|nextest)|go test|(npm|pnpm|yarn|bun)( run)? test|dotnet test|ctest)\b"),
        ("build", r"\b(cargo (\+\S+ )?(build|check|clippy)|tsc|(npm|pnpm|yarn|bun) run build|vite build|msbuild|dotnet build|make|cmake|nuitka|pyinstaller)\b"),
        ("git", r"^\s*(git|gh)\b"),
        ("install", r"\b(pip install|uv (pip|add|sync)|(npm|pnpm|yarn|bun) (i|install|add|ci)|winget|choco|cargo install)\b"),
        ("script", r"^\s*(&\s*)?(py|python3?|node|deno|bun|pwsh|powershell|bash|sh|cmd)\b|\.(py|ps1|js|ts|sh|bat|cmd)\b"),
    )
)


def cmd_kind(command: Any) -> str:
    """One of CMD_KINDS for a terminal command; only this word is reported."""
    text = str(command or "").strip().lower()
    for kind, rule in _CMD_RULES:
        if rule.search(text):
            return kind
    return "other"


def code_of(source: str) -> str:
    """A slug for an error log source tag (never the message)."""
    s = re.sub(r"[^a-z0-9_.-]+", "_", (source or "").strip().lower()).strip("_")
    return (s or "unknown")[:48]
