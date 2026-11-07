"""First-run Store install of the starter plugins (AI gateways, UEFN, art tools, extras)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

# Signs this PC in to a Ducky account, which brings Ducky AI (the "UEFN Ducky" provider).
STARTER_ACCOUNT_SLUG = "account"

STARTER_LLM_GATEWAY_SLUGS: tuple[str, ...] = ("anthropic", "openai", "google", "cursor")

STARTER_UEFN_PLUGIN_SLUGS: tuple[str, ...] = ("uefn", "playtester", "typesafe")

STARTER_CREATE_PLUGIN_SLUGS: tuple[str, ...] = ("blender", "forge", "meshy", "tripo", "adobe", "island-media-kit")

# The in-app web browser, translation, and offline neural voices so every ducky can read
# its replies aloud.
STARTER_EXTRA_PLUGIN_SLUGS: tuple[str, ...] = ("browser", "translation", "piper")

# Install order: account and gateways first, so Connect AI can start while the rest land.
POPULAR_PLUGIN_SLUGS: tuple[str, ...] = (
    (STARTER_ACCOUNT_SLUG,)
    + STARTER_LLM_GATEWAY_SLUGS
    + STARTER_UEFN_PLUGIN_SLUGS
    + STARTER_CREATE_PLUGIN_SLUGS
    + STARTER_EXTRA_PLUGIN_SLUGS
)

STARTER_PLUGIN_LABELS: dict[str, str] = {
    "account": "Ducky Account",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "cursor": "Cursor",
    "uefn": "UEFN",
    "playtester": "Play Tester",
    "typesafe": "TypeSafe",
    "blender": "Blender",
    "forge": "Ducky Forge",
    "meshy": "Meshy",
    "tripo": "Tripo",
    "adobe": "Adobe",
    "island-media-kit": "Island Media Kit",
    "browser": "Web Browser",
    "translation": "Translation",
    "piper": "Pipe voices",
}

_GATEWAY_CONTRIB_KEYS = (
    "llm.providers",
    "llm_providers",
    "llm.coding_agents",
    "llm_coding_agents",
)

_STATE = threading.Condition()
_ACTIVE = False


def _installed_plugin_ids() -> set[str]:
    from backend.uefn_plugins.store import PLUGIN_MANIFEST, appdata_uefn_plugins_dir, normalize_plugin_id

    root = appdata_uefn_plugins_dir()
    if not root.is_dir():
        return set()
    out: set[str] = set()
    for child in root.iterdir():
        if not child.is_dir() or not (child / PLUGIN_MANIFEST).is_file():
            continue
        try:
            out.add(normalize_plugin_id(child.name))
        except ValueError:
            continue
    return out


def _manifest_contributes_gateway(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    contrib = data.get("contributes")
    if not isinstance(contrib, dict):
        return False
    return any(contrib.get(key) for key in _GATEWAY_CONTRIB_KEYS)


def installed_gateway_ids() -> list[str]:
    """Installed plugins that register an LLM gateway. Empty means the nag may show."""
    from backend.uefn_plugins.store import PLUGIN_MANIFEST, appdata_uefn_plugins_dir, normalize_plugin_id

    root = appdata_uefn_plugins_dir()
    if not root.is_dir():
        return []
    found: list[str] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        manifest_path = child / PLUGIN_MANIFEST
        if not child.is_dir() or not manifest_path.is_file():
            continue
        try:
            pid = normalize_plugin_id(child.name)
        except ValueError:
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if _manifest_contributes_gateway(data):
            found.append(pid)
    return found


def _grandfather_existing_install(settings: Any) -> bool:
    """True for machines that already used Ducky — do not surprise-install plugins."""
    if bool(getattr(settings, "walkthrough_completed", None)):
        return True
    enabled = {
        str(x).strip().lower()
        for x in (getattr(settings, "enabled_uefn_plugins", None) or [])
        if str(x).strip()
    }
    extras = (_installed_plugin_ids() | enabled) - set(STARTER_LLM_GATEWAY_SLUGS)
    return bool(extras)


def _mark_seeded(settings: Any) -> None:
    settings.starter_llm_gateways_seeded = True
    settings.save()


def _noop(settings_seeded: bool, *, grandfathered: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "first_run": False,
        "installed": [],
        "skipped": list(POPULAR_PLUGIN_SLUGS),
        "errors": [],
    }
    if grandfathered:
        out["grandfathered"] = True
    if settings_seeded:
        out["seeded"] = True
    return out


def starter_llm_onboard_pending() -> dict[str, Any]:
    """Cheap first-run check. Grandfathers existing installs without downloading."""
    from frontend.settings import PanelSettings

    with _STATE:
        settings = PanelSettings.load()
        if bool(getattr(settings, "starter_llm_gateways_seeded", False)):
            return {"ok": True, "pending": False}
        if _grandfather_existing_install(settings):
            _mark_seeded(settings)
            return {"ok": True, "pending": False, "grandfathered": True}
        return {"ok": True, "pending": True}


# The welcome ducky the finished setup opens: its first message (typed out on screen, no
# model call) offers these walkthroughs (ids of the app's own tours) as buttons.
WELCOME_DUCKY_TITLE = "Welcome to Ducky"
WELCOME_DUCKY_TOURS: tuple[str, ...] = (
    "chat.composer",
    "app.shell",
    "workflows.intro",
    "workflows.first_build",
    "settings.duckies",
)
WELCOME_DUCKY_TEXT = (
    "## Hi, I'm Ducky!\n\n"
    "You're all set. I'm your first ducky: ask me about your island, Verse, or anything in UEFN.\n\n"
    "Want a quick walkthrough? Pick one below, now or any time."
)


# The setup's screens, in order; a restart reopens the one a new user was on.
SETUP_STEPS: tuple[str, ...] = ("welcome", "plugins", "ai", "defaults", "rating")


def _setup_open_state() -> str:
    """``first_run_setup`` after the fresh-install check: "" never needed, "open" / "open:<step>"
    the setup on that step, "tour:<n>:<chat id>" the first-run tour on step n in the welcome
    ducky, "done" finished."""
    from frontend.settings import PanelSettings

    fresh = bool(starter_llm_onboard_pending().get("pending"))
    with _STATE:
        settings = PanelSettings.load()
        if settings.first_run_setup == "" and fresh:
            settings.first_run_setup = "open"
            settings.save()
        if settings.first_run_setup.startswith("tour:") and (settings.walkthrough_completed or {}).get("first.run"):
            # The tour ends (finished or skipped) with its completion flag: one write, so no
            # second save can race it (save_agent_settings is load-change-save).
            settings.first_run_setup = "done"
            settings.save()
        return settings.first_run_setup


def setup_open() -> bool:
    """Whether the first-run setup must show. A fresh install opens it once and it stays open
    across restarts until Finish; an install that already used Ducky never sees it."""
    return _setup_open_state().startswith("open")


def setup_resume() -> dict[str, Any]:
    """Where a new user left off: the setup step on screen, or the first-run tour's step and chat."""
    state = _setup_open_state()
    if state.startswith("open"):
        step = state.partition(":")[2]
        return {"setup_step": step if step in SETUP_STEPS else "welcome"}
    if state.startswith("tour:"):
        _, n, chat_id = (state.split(":", 2) + ["", ""])[:3]
        return {"first_run_tour": {"step": int(n) if n.isdigit() else 0, "chat_id": chat_id}}
    return {}


def save_setup_progress(step: str) -> bool:
    """Remember where a new user is: a setup step while the setup is open, the first-run
    tour's step (a number) while it runs (its completion flag ends it). Never moves a
    finished setup back; False when the step does not fit where the setup is."""
    from frontend.settings import PanelSettings

    step = str(step or "").strip()
    with _STATE:
        settings = PanelSettings.load()
        cur = settings.first_run_setup
        if cur.startswith("open") and step in SETUP_STEPS:
            nxt = "open" if step == "welcome" else f"open:{step}"
        elif cur.startswith("tour:") and step.isdigit():
            nxt = f"tour:{int(step)}:{cur.split(':', 2)[2] if cur.count(':') >= 2 else ''}"
        else:
            return False
        if nxt != cur:
            settings.first_run_setup = nxt
            settings.save()
    return True


def finish_setup() -> bool:
    """Close the first-run setup for good. True once, when it was open and the welcome
    ducky is still to make: the caller makes it, then ``start_first_run_tour``."""
    from frontend.settings import PanelSettings

    with _STATE:
        settings = PanelSettings.load()
        was_open = settings.first_run_setup.startswith("open")
        make_welcome = was_open and not settings.welcome_ducky_offered
        settings.first_run_setup = "done"
        if make_welcome:
            settings.welcome_ducky_offered = True
        settings.save()
    return make_welcome


def start_first_run_tour(chat_id: str) -> None:
    """The welcome ducky is made: its first-run tour is what a restart resumes until it ends."""
    from frontend.settings import PanelSettings

    with _STATE:
        settings = PanelSettings.load()
        settings.first_run_setup = f"tour:0:{chat_id}"
        settings.save()


def _group(slug: str) -> str:
    if slug == STARTER_ACCOUNT_SLUG or slug in STARTER_LLM_GATEWAY_SLUGS:
        return "ai"
    if slug in STARTER_UEFN_PLUGIN_SLUGS:
        return "uefn"
    return "create" if slug in STARTER_CREATE_PLUGIN_SLUGS else "extras"


def starter_plugins() -> list[dict[str, Any]]:
    """The bundle the first-run setup installs, in install order, with what is on disk now."""
    from backend.uefn_plugins.store import is_plugin_installed

    return [
        {
            "slug": slug,
            "label": STARTER_PLUGIN_LABELS.get(slug, slug),
            "group": _group(slug),
            "installed": bool(is_plugin_installed(slug)),
        }
        for slug in POPULAR_PLUGIN_SLUGS
    ]


def starter_setup_status() -> dict[str, Any]:
    """Whether the first-run setup is open, which gateway plugins exist, and the bundle."""
    resume = setup_resume()
    return {
        "ok": True,
        "pending_first_run": "setup_step" in resume,
        **resume,
        "gateway_ids": installed_gateway_ids(),
        "plugins": starter_plugins(),
    }


def _emit(on_progress: Callable[[dict[str, Any]], None] | None, payload: dict[str, Any]) -> None:
    if on_progress is None:
        return
    try:
        on_progress(payload)
    except Exception:
        pass


def _count_plugin(slug: str, ok: bool) -> None:
    """Anonymous count of one setup plugin install and whether it worked (never the error text)."""
    try:
        from frontend import usage_stats

        usage_stats.track("setup.plugin", plugin_id=slug, ok=ok)
    except Exception:
        pass


def _install_missing(on_progress: Callable[[dict[str, Any]], None] | None) -> tuple[list[str], list[str], list[dict[str, str]]]:
    from backend.uefn_plugins.store import is_plugin_installed
    from frontend.duckyos_account import DuckyOSAccountError, store_download_and_install

    installed: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    total = len(POPULAR_PLUGIN_SLUGS)
    for index, slug in enumerate(POPULAR_PLUGIN_SLUGS, start=1):
        label = STARTER_PLUGIN_LABELS.get(slug, slug)
        if is_plugin_installed(slug):
            skipped.append(slug)
            _emit(
                on_progress,
                {
                    "type": "starter_plugins_progress",
                    "slug": slug,
                    "label": label,
                    "index": index,
                    "total": total,
                    "setup_phase": "skipped",
                },
            )
            continue
        _emit(
            on_progress,
            {
                "type": "starter_plugins_progress",
                "slug": slug,
                "label": label,
                "index": index,
                "total": total,
                "setup_phase": "installing",
            },
        )
        try:
            result = store_download_and_install(slug, replace=True, is_update=False)
            if result.get("ok"):
                if result.get("enabled") is False:
                    # Setup installs the bundle to use it: a plugin that ships off (Pipe) is turned on.
                    from backend.uefn_plugins.store import set_uefn_plugin_enabled

                    set_uefn_plugin_enabled(slug, True)
                installed.append(slug)
                _count_plugin(slug, True)
                _emit(
                    on_progress,
                    {
                        "type": "starter_plugins_progress",
                        "slug": slug,
                        "label": label,
                        "index": index,
                        "total": total,
                        "setup_phase": "installed",
                    },
                )
            else:
                err = str(result.get("error") or "install failed")
                errors.append({"slug": slug, "error": err, "code": str(result.get("code") or "error")})
                _count_plugin(slug, False)
                _emit(
                    on_progress,
                    {
                        "type": "starter_plugins_progress",
                        "slug": slug,
                        "label": label,
                        "index": index,
                        "total": total,
                        "setup_phase": "error",
                        "detail": err,
                    },
                )
        except DuckyOSAccountError as exc:
            errors.append({"slug": slug, "error": exc.message, "code": exc.code or "error"})
            _count_plugin(slug, False)
            _emit(
                on_progress,
                {
                    "type": "starter_plugins_progress",
                    "slug": slug,
                    "label": label,
                    "index": index,
                    "total": total,
                    "setup_phase": "error",
                    "detail": exc.message,
                },
            )
        except Exception as exc:
            errors.append({"slug": slug, "error": str(exc), "code": "error"})
            _count_plugin(slug, False)
            _emit(
                on_progress,
                {
                    "type": "starter_plugins_progress",
                    "slug": slug,
                    "label": label,
                    "index": index,
                    "total": total,
                    "setup_phase": "error",
                    "detail": str(exc),
                },
            )
    return installed, skipped, errors


def ensure_popular_plugins(
    *,
    force: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Install the bundle in :data:`POPULAR_PLUGIN_SLUGS` order (gateways first).

    The auto path (``force=False``) runs once and skips machines that already
    used Ducky. The setup passes ``force=True`` so a missing bundle still
    downloads. The seed flag flips only after every gateway in
    :data:`STARTER_LLM_GATEWAY_SLUGS` is on disk.
    """
    global _ACTIVE
    from backend.uefn_plugins.store import is_plugin_installed
    from frontend.settings import PanelSettings

    with _STATE:
        while _ACTIVE:
            _STATE.wait()
        settings = PanelSettings.load()
        seeded = bool(getattr(settings, "starter_llm_gateways_seeded", False))
        grandfathered = (not seeded) and _grandfather_existing_install(settings)
        if not force and seeded:
            return _noop(True)
        if not force and grandfathered:
            _mark_seeded(settings)
            return _noop(True, grandfathered=True)
        _ACTIVE = True

    try:
        installed, skipped, errors = _install_missing(on_progress)
    finally:
        with _STATE:
            _ACTIVE = False
            _STATE.notify_all()

    with _STATE:
        settings = PanelSettings.load()
        present = [slug for slug in STARTER_LLM_GATEWAY_SLUGS if is_plugin_installed(slug)]
        if len(present) == len(STARTER_LLM_GATEWAY_SLUGS):
            _mark_seeded(settings)

    return {
        "ok": not errors,
        "first_run": not seeded and not grandfathered,
        "installed": installed,
        "skipped": skipped,
        "errors": errors,
        "present": present,
    }


def ensure_starter_llm_gateways(
    *,
    force: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Popular bundle. Same entry the panel and the first-run popup call."""
    return ensure_popular_plugins(force=force, on_progress=on_progress)
