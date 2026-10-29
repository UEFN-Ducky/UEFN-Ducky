"""User/AI custom Verse templates (AppData) — never Store / plugin verse.templates."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from backend.util.json_util import tool_json
from backend.server import mcp


def _parse_files_json(files_json: str) -> Any:
    raw = (files_json or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid files_json: {exc}") from exc
    return parsed


def list_user_verse_templates() -> dict[str, Any]:
    from frontend.verse_template_assets import list_custom_verse_templates

    rows = [e.to_dict() for e in list_custom_verse_templates()]
    return {
        "ok": True,
        "count": len(rows),
        "templates": [
            {
                "id": r["id"],
                "name": r["name"],
                "icon": r.get("icon") or "",
                "folder": r.get("folder") or "",
                "file_count": len(r.get("files") or []) or (1 if r.get("content") else 0),
                "paths": [f.get("path") for f in (r.get("files") or []) if isinstance(f, dict)],
            }
            for r in rows
        ],
    }


def get_user_verse_template(template_id: str) -> dict[str, Any]:
    from frontend.verse_template_assets import get_custom_verse_template

    entry = get_custom_verse_template(template_id)
    if entry is None:
        return {
            "ok": False,
            "error": (
                f"Custom template not found: {template_id}. "
                "Only custom:… ids — Store/plugin templates use verse_template_get."
            ),
        }
    return {"ok": True, "template": entry.to_dict()}


def save_user_verse_template(
    name: str,
    icon: str = "📄",
    content: str = "",
    *,
    template_id: str = "",
    folder: str = "",
    files_json: str = "",
) -> dict[str, Any]:
    from frontend.verse_template_assets import save_custom_verse_template

    try:
        files = _parse_files_json(files_json)
        entry = save_custom_verse_template(
            name,
            icon or "📄",
            content,
            template_id=template_id or "",
            folder=folder or "",
            files=files,
        )
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "template": entry.to_dict()}


def delete_user_verse_template(template_id: str, *, confirm: bool = False) -> dict[str, Any]:
    from frontend.verse_template_assets import delete_custom_verse_template

    if not confirm:
        return {"ok": False, "error": "confirm=true required to delete"}
    if not (template_id or "").strip().startswith("custom:"):
        return {
            "ok": False,
            "error": "Only custom:… templates can be deleted (not Store/plugin packs)",
        }
    ok = delete_custom_verse_template(template_id)
    if not ok:
        return {"ok": False, "error": f"Custom template not found: {template_id}"}
    return {"ok": True, "id": template_id, "deleted": True}


def apply_user_verse_template(
    template_id: str,
    parent_relative: str = "Content/Verse",
) -> dict[str, Any]:
    from frontend.verse_template_assets import apply_custom_verse_template

    tid = (template_id or "").strip()
    if not tid.startswith("custom:"):
        return {"ok": False, "error": _store_template_hint(tid)}
    return apply_custom_verse_template(template_id, parent_relative=parent_relative)


def _store_template_hint(template_id: str) -> str:
    """Name the Store plugin that owns ``plugin:<pid>:<id>`` (or that it is off)."""
    from backend.uefn_plugins.host import get_contributions

    parts = template_id.split(":", 2)
    pid, bare = (parts[1], parts[2]) if len(parts) == 3 and parts[0] == "plugin" else ("", template_id)
    rows = [r for r in get_contributions().get("verse_templates") or [] if isinstance(r, dict)]
    row = next(
        (r for r in rows if r.get("id") == bare and (not pid or r.get("plugin_id") == pid)),
        None,
    )
    if row is not None:
        return (
            f"{template_id} is a Store template from plugin '{row.get('plugin_id')}': "
            f"call verse_template_apply(template_id='{template_id}') instead "
            "(this tool applies custom:… templates only)"
        )
    if pid:
        return (
            f"plugin '{pid}' is off or removed, so {template_id} is unavailable: "
            f"turn '{pid}' on in the Store, then call verse_template_apply"
        )
    return (
        f"unknown template_id: {template_id}. This tool applies custom:… templates; "
        "Store packs use verse_template_apply (verse_template_list shows their ids)"
    )


@mcp.tool()
def ducky_verse_template_list(pretty: bool = False) -> str:
    """List user/AI custom Verse templates (AppData). Never lists Store plugin packs.

    For Store/plugin scaffolds use verse_template_list (uefn-plugin-verse).
    """
    return tool_json(list_user_verse_templates(), pretty=pretty)


@mcp.tool()
def ducky_verse_template_get(template_id: str, pretty: bool = False) -> str:
    """Read a custom:… Verse template (single file or multi-file pack)."""
    return tool_json(get_user_verse_template(template_id), pretty=pretty)


@mcp.tool()
def ducky_verse_template_save(
    name: str,
    icon: str = "📄",
    content: str = "",
    template_id: str = "",
    folder: str = "",
    files_json: str = "",
    pretty: bool = False,
) -> str:
    """Create or update a custom Verse template (never Store/plugin templates).

    Single file: pass ``content``. Multi-file system pack: pass ``folder`` +
    ``files_json`` as a JSON array of ``{path, content}`` (paths relative to folder,
    nested dirs ok — same shape as plugin contributes.verse.templates).
    Pass ``template_id`` (custom:…) to update.
    """
    return tool_json(
        save_user_verse_template(
            name,
            icon=icon,
            content=content,
            template_id=template_id,
            folder=folder,
            files_json=files_json,
        ),
        pretty=pretty,
    )


@mcp.tool()
def ducky_verse_template_delete(
    template_id: str,
    confirm: bool = False,
    pretty: bool = False,
) -> str:
    """Delete a custom:… Verse template. Requires confirm=true. Never touches Store packs."""
    return tool_json(
        delete_user_verse_template(template_id, confirm=bool(confirm)),
        pretty=pretty,
    )


@mcp.tool()
def ducky_verse_template_apply(
    template_id: str,
    parent_relative: str = "Content/Verse",
    pretty: bool = False,
) -> str:
    """Materialize a custom:… template into the open project (file or folder pack)."""
    return tool_json(
        apply_user_verse_template(template_id, parent_relative=parent_relative),
        pretty=pretty,
    )


def _self_check() -> None:
    """ponytail: save single + pack → get → update → delete (temp AppData)."""
    tmp = tempfile.mkdtemp(prefix="ducky-vt-")
    try:
        os.environ["LOCALAPPDATA"] = tmp
        os.environ["USERPROFILE"] = tmp
        os.environ["HOME"] = tmp
        single = save_user_verse_template("Solo", content="using { /Verse.org/Simulation }\n")
        assert single.get("ok"), single
        tid = single["template"]["id"]
        assert tid.startswith("custom:")
        got = get_user_verse_template(tid)
        assert got.get("ok") and "Simulation" in got["template"]["content"]
        pack = save_user_verse_template(
            "PlayerCore",
            folder="PlayerCore",
            files_json=json.dumps(
                [
                    {"path": "player_api.verse", "content": "# api\n"},
                    {"path": "impl/player.verse", "content": "# impl\n"},
                ]
            ),
        )
        assert pack.get("ok"), pack
        assert len(pack["template"]["files"]) == 2
        updated = save_user_verse_template(
            "Solo2",
            content="# edited\n",
            template_id=tid,
        )
        assert updated.get("ok") and updated["template"]["name"] == "Solo2"
        refused = save_user_verse_template("X", template_id="plugin:verse:foo")
        assert not refused.get("ok")
        listed = list_user_verse_templates()
        assert listed["count"] >= 2
        deleted = delete_user_verse_template(tid, confirm=True)
        assert deleted.get("ok"), deleted
        print("panel_verse_templates.py self-check ok")
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _self_check()
