"""Sensitive plugin tools: only models the person allowed may see or call them.

A plugin marks tools sensitive in ``plugin.json`` → ``contributes.agent.tools.sensitive``
(``true`` = every tool of the plugin, or a list of tool names). Each plugin keeps its own
Allowed models list (Settings → that plugin's tab). An empty list means no agent gets the
plugin's sensitive tools; the person's own clicks (no agent run bound) always pass.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_FILE = "plugin_allowed_models.json"
_LOCK = threading.Lock()


def _path() -> Path:
    from backend.skills.discovery import appdata_dir

    return appdata_dir() / _FILE


def _read_all() -> dict[str, list[str]]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for pid, models in raw.items():
        if isinstance(pid, str) and isinstance(models, list):
            out[pid] = [str(m).strip() for m in models if str(m).strip()]
    return out


def allowed_models(plugin_id: str) -> list[str]:
    return list(_read_all().get(str(plugin_id or "").strip().lower(), []))


def set_allowed_models(plugin_id: str, models: list[Any]) -> list[str]:
    pid = str(plugin_id or "").strip().lower()
    if not pid:
        raise ValueError("plugin id required")
    clean: list[str] = []
    for m in models or []:
        s = str(m or "").strip()
        if s and s not in clean:
            clean.append(s)
    with _LOCK:
        data = _read_all()
        if clean:
            data[pid] = clean
        else:
            data.pop(pid, None)
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return clean


def _same_model(a: str, b: str) -> bool:
    """Catalog ids may carry a provider prefix (``anthropic/claude-opus-5-5``) the run's
    model lacks, or the other way round."""
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b:
        return False
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def model_allowed(plugin_id: str, model: str) -> bool:
    return any(_same_model(m, model) for m in allowed_models(plugin_id))


def block_reason(plugin_id: str, tool_name: str, *, label: str = "", listing: bool = False) -> str:
    """Why this caller may not use a sensitive tool ('' = allowed).

    No bound run in the app process is the person. ``listing`` hides nothing without a
    bound run either: the shared catalog is listed for every client at once, so the call
    itself is the gate there.
    """
    from backend.workspace import identity

    ctx = identity.resolve_context()
    if ctx is None:
        if listing:
            return ""
        from backend.process_role import is_app

        if not (identity.is_shared_call() or not is_app()):
            return ""
        model = ""  # an MCP client that did not say which model it runs
    else:
        model = ctx.model or ""
    if model and model_allowed(plugin_id, model):
        return ""
    name = label or plugin_id
    who = f"this chat's model ({model})" if model else "this caller's model (unknown)"
    return (
        f"{tool_name} is a sensitive {name} tool and {who} is not on {name}'s Allowed models "
        f"list. Only the person can allow a model: Settings → {name} → Allowed models. "
        "Do not retry; tell the person."
    )
