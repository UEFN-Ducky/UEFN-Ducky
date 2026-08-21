"""First-run Store install of Anthropic, Cursor, and OpenAI — once."""

from __future__ import annotations

import threading
from typing import Any

STARTER_LLM_GATEWAY_SLUGS: tuple[str, ...] = ("anthropic", "cursor", "openai")

_SEED_LOCK = threading.Lock()


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


def _grandfather_existing_install(settings: Any) -> bool:
    """True for machines that already used Ducky — do not surprise-install gateways."""
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


def starter_llm_onboard_pending() -> dict[str, Any]:
    """Cheap first-run check. Grandfathers existing installs without downloading."""
    from frontend.settings import PanelSettings

    with _SEED_LOCK:
        settings = PanelSettings.load()
        if bool(getattr(settings, "starter_llm_gateways_seeded", False)):
            return {"ok": True, "pending": False}
        if _grandfather_existing_install(settings):
            _mark_seeded(settings)
            return {"ok": True, "pending": False, "grandfathered": True}
        return {"ok": True, "pending": True}


def ensure_starter_llm_gateways() -> dict[str, Any]:
    """Download Anthropic / Cursor / OpenAI from the Store. No-op after the first success."""
    from backend.uefn_plugins.store import is_plugin_installed
    from frontend.duckyos_account import DuckyOSAccountError, store_download_and_install
    from frontend.settings import PanelSettings

    with _SEED_LOCK:
        settings = PanelSettings.load()
        if bool(getattr(settings, "starter_llm_gateways_seeded", False)):
            return {
                "ok": True,
                "first_run": False,
                "installed": [],
                "skipped": list(STARTER_LLM_GATEWAY_SLUGS),
                "errors": [],
            }
        if _grandfather_existing_install(settings):
            _mark_seeded(settings)
            return {
                "ok": True,
                "first_run": False,
                "grandfathered": True,
                "installed": [],
                "skipped": list(STARTER_LLM_GATEWAY_SLUGS),
                "errors": [],
            }

        installed: list[str] = []
        skipped: list[str] = []
        errors: list[dict[str, str]] = []
        for slug in STARTER_LLM_GATEWAY_SLUGS:
            if is_plugin_installed(slug):
                skipped.append(slug)
                continue
            try:
                result = store_download_and_install(slug, replace=True, is_update=False)
                if result.get("ok"):
                    installed.append(slug)
                else:
                    errors.append(
                        {
                            "slug": slug,
                            "error": str(result.get("error") or "install failed"),
                            "code": str(result.get("code") or "error"),
                        }
                    )
            except DuckyOSAccountError as exc:
                errors.append({"slug": slug, "error": exc.message, "code": exc.code or "error"})
            except Exception as exc:
                errors.append({"slug": slug, "error": str(exc), "code": "error"})

        present = [slug for slug in STARTER_LLM_GATEWAY_SLUGS if is_plugin_installed(slug)]
        # Pin the flag only when all three are installed — a failed download can
        # retry on the next first-run launch. After this, never auto-download again.
        if len(present) == len(STARTER_LLM_GATEWAY_SLUGS):
            _mark_seeded(settings)

        return {
            "ok": not errors,
            "first_run": True,
            "installed": installed,
            "skipped": skipped,
            "errors": errors,
            "present": present,
        }
