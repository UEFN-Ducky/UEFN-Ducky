"""First-run Store install of popular gateways and UEFN editor plugins."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

STARTER_LLM_GATEWAY_SLUGS: tuple[str, ...] = ("anthropic", "cursor", "openai")

STARTER_UEFN_PLUGIN_SLUGS: tuple[str, ...] = (
    "uefn",
    "verse",
    "leveldesign",
    "materials",
    "vfx",
    "animation",
    "modeling",
    "scenegraph",
    "uefn-physics",
    "metahuman",
    "tester",
    "translation",
)

POPULAR_PLUGIN_SLUGS: tuple[str, ...] = STARTER_LLM_GATEWAY_SLUGS + STARTER_UEFN_PLUGIN_SLUGS

STARTER_PLUGIN_LABELS: dict[str, str] = {
    "anthropic": "Anthropic",
    "cursor": "Cursor",
    "openai": "OpenAI",
    "uefn": "UEFN",
    "verse": "Verse",
    "leveldesign": "Level Design",
    "materials": "Materials",
    "vfx": "VFX",
    "animation": "Animation",
    "modeling": "Modeling",
    "scenegraph": "Scene Graph",
    "uefn-physics": "Physics",
    "metahuman": "MetaHuman",
    "tester": "Tester",
    "translation": "Translation",
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


def starter_plugins() -> list[dict[str, Any]]:
    """The bundle the first-run setup installs, in install order, with what is on disk now."""
    from backend.uefn_plugins.store import is_plugin_installed

    return [
        {
            "slug": slug,
            "label": STARTER_PLUGIN_LABELS.get(slug, slug),
            "group": "gateway" if slug in STARTER_LLM_GATEWAY_SLUGS else "editor",
            "installed": bool(is_plugin_installed(slug)),
        }
        for slug in POPULAR_PLUGIN_SLUGS
    ]


def starter_setup_status() -> dict[str, Any]:
    """Whether the first-run setup is pending, which gateway plugins exist, and the bundle."""
    pending = starter_llm_onboard_pending()
    return {
        "ok": True,
        "pending_first_run": bool(pending.get("pending")),
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
                installed.append(slug)
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
    """Install gateways, then UEFN editor plugins.

    The auto path (``force=False``) runs once and skips machines that already
    used Ducky. The corner button passes ``force=True`` so a missing bundle
    still downloads. The seed flag flips only after Anthropic, Cursor, and
    OpenAI are all on disk.
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
