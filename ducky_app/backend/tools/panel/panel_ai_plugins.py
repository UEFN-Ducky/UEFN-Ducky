"""AI-made desktop plugins: draft under AppData/ai_plugins, install via existing zip path.

Shared per-install workspace — any duckie / IDE agent can edit any draft.
Drafts are never loaded; only ``import_plugin_from_bytes(source=\"ai\")`` installs.
"""

from __future__ import annotations

import io
import json
import os
import py_compile
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from backend.util.json_util import tool_json
from backend.server import mcp

_SKIP_DIR_NAMES = frozenset({"scripts", "deploy", ".git", "__pycache__", ".venv", "node_modules"})
_SECRET_SUFFIXES = frozenset({".dat", ".env", ".pem", ".key"})
_MAX_FILE_CHARS = 512 * 1024
# A whole big file in an agent's context costs every later call of its turn: one team read
# 4.7 MB of plugin files in 50 minutes (logic.js 6 times, background.py 18 times). Bigger files
# without a line range come back as an outline and their first lines.
_OUTLINE_OVER_CHARS = 48 * 1024
_HEAD_LINES = 150
_OUTLINE_RE = re.compile(
    r"^\s*(?:(?:async\s+)?def\s+\w+|class\s+\w+"
    r"|(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*\w+"
    r"|(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>|\w+\s*=>))"
    r"|^\s{0,2}\"[^\"]+\"\s*:"
)
_AI_PLUGINS_SKILL = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "skill_packs"
    / "ducky"
    / "references"
    / "ai_plugins.md"
)


def _strip_skill_frontmatter(text: str) -> str:
    raw = (text or "").lstrip("\ufeff")
    if not raw.startswith("---"):
        return raw.strip()
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return raw.strip()
    return parts[2].strip()


def plugin_api_markdown() -> str:
    """Every ``api.*`` a plugin's ``register(api)`` gets, read from the code so it never
    falls behind: signature and the first paragraph of each docstring."""
    import inspect

    from backend.uefn_plugins.plugin_host.api import _PluginApi

    rows = ["## The `api` object (live list of everything `register(api)` gets)", ""]
    for name in sorted(n for n in dir(_PluginApi) if not n.startswith("_")):
        member = getattr(_PluginApi, name)
        if isinstance(inspect.getattr_static(_PluginApi, name), property):
            head = f"`api.{name}`"
        else:
            try:
                sig = str(inspect.signature(member, eval_str=True))
            except Exception:  # noqa: BLE001 — an annotation that won't resolve: keep it as text
                sig = str(inspect.signature(member))
            sig = sig.replace("typing.", "").replace("(self, ", "(").replace("(self)", "()")
            head = f"`api.{name}{sig}`"
        doc = (inspect.getdoc(member) or "").split("\n\n", 1)[0].replace("\n", " ").strip()
        rows.append(f"- {head}: {doc}" if doc else f"- {head}")
    return "\n".join(rows)


def _load_plugin_reference() -> str:
    """Same recipe as skill_read_subskill('ducky', 'ai_plugins'), plus the live ``api`` list."""
    try:
        body = _strip_skill_frontmatter(_AI_PLUGINS_SKILL.read_text(encoding="utf-8"))
    except OSError:
        body = ""
    try:
        api = plugin_api_markdown()
    except Exception:  # noqa: BLE001 — the recipe still helps without it
        api = ""
    if body:
        return f"{body.rstrip()}\n\n{api}\n" if api else body
    return (
        "`ducky_plugin_list` is the census (drafts + installed). "
        "Never glob/shell AppData. Scaffold → write_file → every `@api.tool()` "
        "action → validate → install → ducky_store_set_enabled (stop on needs_trust)."
    )


# Lazy: tests and ducky_plugin_reference read the shipped skill (no gateway-cache fluff).
_PLUGIN_REFERENCE = _load_plugin_reference()


def _notify_plugins() -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "uefn_plugins_changed"})
    except Exception:
        pass


def _draft_root(plugin_id: str) -> Path:
    from backend.uefn_plugins.store import appdata_ai_plugins_dir, normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    return appdata_ai_plugins_dir() / pid


def _resolve_jailed(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``; raise ValueError on escape / absolute / .."""
    raw = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        raise ValueError("path required")
    parts = Path(raw).parts
    if not parts or ".." in parts or Path(raw).is_absolute():
        raise ValueError(f"path escapes draft: {rel!r}")
    target = (root / Path(*parts)).resolve()
    root_res = root.resolve()
    if not target.is_relative_to(root_res):
        raise ValueError(f"path escapes draft: {rel!r}")
    return target


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _minimal_manifest(pid: str, label: str, description: str) -> dict[str, Any]:
    clean_label = (label or "").strip() or pid.replace("_", " ").replace("-", " ").title()
    return {
        "id": pid,
        "kind": "plugin",
        "version": "1.0.0",
        "label": clean_label,
        "description": (description or "").strip() or clean_label,
        "min_app_version": "1.0.0",
        "default_enabled": False,
        "secret_keys": [],
        "contributes": {
            "agent.tools": {
                "category": pid,
                "intent_pattern": rf"\\b{pid}\\b",
            },
            "automations": {
                "nodes": [
                    {
                        "id": f"{pid}.run",
                        "label": clean_label,
                        "group": clean_label,
                    }
                ],
                "templates": [
                    {
                        "id": f"{pid}-run",
                        "label": f"Run {clean_label}",
                        "description": f"Chat start → Agent → {clean_label} → Finish.",
                        "graph": {
                            "nodes": [
                                {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                                {"id": "a", "type": "pipeline.agent", "x": 160, "y": 0, "config": {}},
                                {"id": "n", "type": f"{pid}.run", "x": 320, "y": 0, "config": {}},
                                {"id": "f", "type": "pipeline.finish", "x": 480, "y": 0, "config": {}},
                            ],
                            "edges": [
                                {"source": "s", "target": "a", "kind": "main"},
                                {"source": "a", "target": "n", "kind": "main"},
                                {"source": "n", "target": "f", "kind": "main"},
                            ],
                        },
                    }
                ],
            },
        },
        "backend": {"entry": "backend", "register": "register"},
    }


def _skill_stub(pid: str, label: str) -> str:
    fn = pid.replace("-", "_")
    safe = label.replace('"', "'")
    return (
        "---\n"
        f"name: {pid}\n"
        f'description: "{safe}: list and update its records with the {fn}_* tools."\n'
        f'keywords: "{safe}, {fn}, records"\n'
        "---\n"
        "\n"
        f"# {label}\n"
        "\n"
        "## Tools\n"
        "\n"
        f"- `{fn}_list(kind?)` lists the records.\n"
        f"- `{fn}_upsert(item_id, fields)` creates or updates one.\n"
        f"- Workflow node `{pid}.run` does what `{fn}_list` does.\n"
        "\n"
        "Describe each tool here as you add it, and when a chat should use it. Keep one topic per\n"
        "`##` section: skill search points agents at a single section by its heading.\n"
    )


def _register_stub(plugin_id: str) -> str:
    fn = plugin_id.replace("-", "_")
    return (
        '"""AI-made UEFN desktop plugin — MCP tools required.\n'
        "\n"
        "Data goes through api.data (never a folder you pick): one JSON doc per record,\n"
        "files via api.data.put_file. The host keeps it per account and scope.\n"
        "Tools that don't talk to the UEFN editor pass listener=False, so they work while\n"
        "UEFN is closed. Panel RPCs get the panel's params as keyword arguments.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import re\n"
        "\n"
        "\n"
        "def register(api) -> None:\n"
        "    data = api.data\n"
        "\n"
        f'    @api.tool(intent=r"\\b{plugin_id}\\b", listener=False)\n'
        f"    def {fn}_list(kind: str = \"\") -> dict:\n"
        '        """List records this plugin manages (one doc per record: key "item.<id>")."""\n'
        "        items = data.items(\"item.\")\n"
        "        rows = [v for v in items.values() if not kind or v.get(\"kind\") == kind]\n"
        "        return {\"ok\": True, \"items\": rows}\n"
        "\n"
        f'    @api.tool(intent=r"\\b{plugin_id}\\b", listener=False)\n'
        f"    def {fn}_upsert(item_id: str, fields: dict | None = None) -> dict:\n"
        '        """Create or update one record. Add get/delete for the domain."""\n'
        "        key = re.sub(r\"[^a-z0-9_-]+\", \"-\", str(item_id or \"\").strip().lower()).strip(\"-\")\n"
        "        if not key:\n"
        "            return {\"ok\": False, \"error\": \"Give item_id (letters, numbers, - or _).\"}\n"
        "        doc = {**(data.get(f\"item.{key}\") or {}), **(fields or {}), \"id\": key}\n"
        "        try:\n"
        "            data.put(f\"item.{key}\", doc)\n"
        "        except PermissionError as exc:  # a read-only or locked shared copy\n"
        "            return {\"ok\": False, \"error\": str(exc)}\n"
        "        return {\"ok\": True, \"item\": doc}\n"
        "\n"
        f'    @api.register_pipeline_node("{plugin_id}.run")\n'
        f"    def {fn}_run(ctx: dict) -> dict:\n"
        '        """Pipeline/automation node — same helper as the list tool."""\n'
        "        cfg = ctx.get(\"config\") or {}\n"
        f"        return {fn}_list(str(cfg.get(\"kind\") or \"\"))\n"
        "\n"
        "    # The host calls fn(**params) with what the panel sent: name each param.\n"
        f'    api.register_panel_rpc("list", lambda kind="": {fn}_list(str(kind or "")))\n'
        f'    api.register_panel_rpc("upsert", lambda item_id="", fields=None: {fn}_upsert(item_id, fields))\n'
        f'    api.log("{plugin_id} tools registered")\n'
    )


def scaffold_ai_plugin(
    plugin_id: str, label: str = "", description: str = "", data_sharing: bool | None = None
) -> dict[str, Any]:
    from backend.uefn_plugins.store import (
        appdata_ai_plugins_dir,
        load_plugin_manifest,
        normalize_plugin_id,
        note_draft_creator,
    )

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    installed = load_plugin_manifest(pid)
    if installed is not None:
        src = str(installed.get("source") or "local")
        if src != "ai":
            return {
                "ok": False,
                "error": (
                    f"Plugin id {pid!r} already installed from {src!r} — "
                    "pick another id or uninstall first"
                ),
            }
    root = appdata_ai_plugins_dir() / pid
    if root.is_dir() and (root / "plugin.json").is_file():
        return {
            "ok": False,
            "error": f"Draft already exists: {pid} — use write/read tools or delete_draft",
            "path": str(root),
        }
    root.mkdir(parents=True, exist_ok=True)
    note_draft_creator(pid)
    manifest = _minimal_manifest(pid, label, description)
    if data_sharing is not None:
        manifest["data_sharing"] = bool(data_sharing)
    _write_json(root / "plugin.json", manifest)
    backend = root / "backend"
    backend.mkdir(exist_ok=True)
    (backend / "__init__.py").write_text(_register_stub(pid), encoding="utf-8")
    skill = root / "skills" / pid
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(_skill_stub(pid, str(manifest["label"])), encoding="utf-8")
    return {"ok": True, "id": pid, "path": str(root), "manifest": manifest}


def _slim_installed_plugins() -> list[dict[str, Any]]:
    from backend.uefn_plugins.store import list_uefn_plugins

    out: list[dict[str, Any]] = []
    for row in list_uefn_plugins():
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        out.append(
            {
                "id": pid,
                "label": str(row.get("label") or pid),
                "version": str(row.get("version") or ""),
                "source": str(row.get("source") or ""),
                "enabled": bool(row.get("enabled")),
            }
        )
    return out


def list_ai_plugin_drafts(plugin_id: str = "") -> dict[str, Any]:
    from backend.uefn_plugins.store import appdata_ai_plugins_dir, normalize_plugin_id

    sid = (plugin_id or "").strip()
    if not sid:
        drafts: list[dict[str, Any]] = []
        root = appdata_ai_plugins_dir()
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                man_path = child / "plugin.json"
                label = child.name
                version = ""
                if man_path.is_file():
                    try:
                        man = json.loads(man_path.read_text(encoding="utf-8"))
                        if isinstance(man, dict):
                            label = str(man.get("label") or label)
                            version = str(man.get("version") or "")
                    except (OSError, json.JSONDecodeError):
                        pass
                drafts.append(
                    {"id": child.name, "label": label, "version": version, "path": str(child)}
                )
        return {"ok": True, "drafts": drafts, "installed": _slim_installed_plugins()}
    root = appdata_ai_plugins_dir()
    if not root.is_dir():
        return {"ok": False, "error": f"draft not found: {sid}"}
    try:
        pid = normalize_plugin_id(sid)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    draft = root / pid
    if not draft.is_dir():
        return {"ok": False, "error": f"draft not found: {pid}"}
    files: list[str] = []
    for path in sorted(draft.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(draft).as_posix()
        except ValueError:
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.relative_to(draft).parts):
            continue
        files.append(rel)
    return {"ok": True, "id": pid, "path": str(draft), "files": files}


def write_ai_plugin_file(plugin_id: str, path: str, content: str, *, max_chars: int | None = _MAX_FILE_CHARS) -> dict[str, Any]:
    """``max_chars``: the most an agent may write in one go (None: an edit of a file the
    draft already has, judged by what it adds instead)."""
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    root = _draft_root(pid)
    if not root.is_dir():
        return {"ok": False, "error": f"draft not found: {pid} — call ducky_plugin_scaffold first"}
    try:
        target = _resolve_jailed(root, path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    text = content if isinstance(content, str) else str(content)
    if max_chars is not None and len(text) > max_chars:
        return {"ok": False, "error": f"content exceeds {max_chars} chars"}
    if target.suffix.lower() in _SECRET_SUFFIXES:
        return {"ok": False, "error": f"refusing secret-looking file: {target.name}"}
    root_res = root.resolve()
    # Keep plugin.json id in sync with folder.
    if target.name == "plugin.json" and target.parent == root_res:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"plugin.json invalid JSON: {exc}"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "plugin.json must be an object"}
        data["id"] = pid
        text = json.dumps(data, indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    try:
        rel = target.relative_to(root_res).as_posix()
    except ValueError:
        rel = path
    return {"ok": True, "id": pid, "path": rel, "bytes": len(text.encode("utf-8"))}


def read_ai_plugin_file(plugin_id: str, path: str, *, start_line: int = 0, end_line: int = 0) -> dict[str, Any]:
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    root = _draft_root(pid)
    if not root.is_dir():
        return {"ok": False, "error": f"draft not found: {pid}"}
    try:
        target = _resolve_jailed(root, path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not target.is_file():
        return {"ok": False, "error": f"file not found: {path}"}
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "file is not UTF-8 text"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    rel = path.replace("\\", "/")
    lines = content.splitlines()
    if start_line or end_line:
        first = max(1, int(start_line or 1))
        last = min(len(lines), int(end_line or len(lines)))
        if first > last:
            return {"ok": False, "error": f"no lines {first}-{last}: the file has {len(lines)} lines"}
        return {"ok": True, "id": pid, "path": rel, "start_line": first, "end_line": last,
                "total_lines": len(lines), "content": "\n".join(lines[first - 1:last]) + "\n"}
    if len(content) > _OUTLINE_OVER_CHARS:
        outline = [f"{n}: {line.strip()[:120]}" for n, line in enumerate(lines, 1) if _OUTLINE_RE.match(line)]
        return {
            "ok": True, "id": pid, "path": rel, "partial": True, "total_lines": len(lines),
            "outline": outline[:400], "content": "\n".join(lines[:_HEAD_LINES]) + "\n",
            "hint": (f"{rel} is {len(content) // 1024} KB, so this is its outline and first {_HEAD_LINES} lines. "
                     "Read the part you need with start_line/end_line, and change it with "
                     "ducky_plugin_edit_file instead of rewriting the file."),
        }
    if len(content) > _MAX_FILE_CHARS:
        content = content[:_MAX_FILE_CHARS] + "\n…(truncated)\n"
    return {"ok": True, "id": pid, "path": rel, "content": content}


def edit_ai_plugin_file(plugin_id: str, path: str, old_string: str, new_string: str,
                        replace_all: bool = False) -> dict[str, Any]:
    """Replace an exact snippet in a draft file (unique unless ``replace_all``)."""
    from backend.uefn_plugins.store import normalize_plugin_id

    if not old_string:
        return {"ok": False, "error": "old_string is empty: to create a file use ducky_plugin_write_file"}
    try:
        pid = normalize_plugin_id(plugin_id)
        target = _resolve_jailed(_draft_root(pid), path)
        text = target.read_text(encoding="utf-8")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except (OSError, UnicodeDecodeError):
        return {"ok": False, "error": f"file not found or not UTF-8 text: {path}"}
    count = text.count(old_string)
    if count == 0:
        return {"ok": False, "error": "old_string was not found: read those lines again (start_line/end_line) and copy them exactly"}
    if count > 1 and not replace_all:
        return {"ok": False, "error": f"old_string is in the file {count} times: add surrounding lines so it is unique, or pass replace_all=true"}
    if len(new_string) > _MAX_FILE_CHARS:
        return {"ok": False, "error": f"new_string exceeds {_MAX_FILE_CHARS} chars"}
    # An edit is judged by what it adds: a file already past the limit (Island Media Kit's
    # plugin.json is 17k lines) could otherwise never be changed by a line.
    result = write_ai_plugin_file(pid, path, text.replace(old_string, new_string), max_chars=None)
    if result.get("ok"):
        result["replacements"] = count if replace_all else 1
    return result


def _draft_target(plugin_id: str, path: str) -> tuple[str, Path, Path]:
    """(plugin id, draft root, jailed target) for a file tool; raises ValueError."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    root = _draft_root(pid)
    if not root.is_dir():
        raise ValueError(f"draft not found: {pid}")
    target = _resolve_jailed(root, path)
    if target == root.resolve():
        raise ValueError("that is the whole draft: use ducky_plugin_delete_draft")
    if target.name == "plugin.json" and target.parent == root.resolve():
        raise ValueError("plugin.json stays where it is: edit it instead")
    return pid, root, target


def move_ai_plugin_file(plugin_id: str, path: str, to: str) -> dict[str, Any]:
    """Move or rename a file or folder inside a draft (development evidence to tests/, which
    never ships). Teams could read, write and edit plugin files but not move them, so a
    cleanup step left twenty files with a personal path in the shipped folders."""
    try:
        pid, root, source = _draft_target(plugin_id, path)
        _, _, dest = _draft_target(plugin_id, to)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not source.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if dest.exists():
        return {"ok": False, "error": f"{to} already exists: move it to a new path"}
    if dest.is_relative_to(source):
        return {"ok": False, "error": "a folder can't move inside itself"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    base = root.resolve()
    return {"ok": True, "id": pid, "from": source.relative_to(base).as_posix(), "to": dest.relative_to(base).as_posix()}


def delete_ai_plugin_file(plugin_id: str, path: str, *, recursive: bool = False) -> dict[str, Any]:
    """Delete a file from a draft, or a whole folder with ``recursive``."""
    try:
        pid, root, target = _draft_target(plugin_id, path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not target.exists():
        return {"ok": False, "error": f"not found: {path}"}
    rel = target.relative_to(root.resolve()).as_posix()
    if target.is_dir():
        if not recursive:
            return {"ok": False, "error": f"{rel} is a folder: pass recursive=true to delete it and everything in it"}
        count = sum(1 for p in target.rglob("*") if p.is_file())
        shutil.rmtree(target)
        return {"ok": True, "id": pid, "deleted": rel, "files": count}
    target.unlink()
    return {"ok": True, "id": pid, "deleted": rel, "files": 1}


ICON_PX = 512


def set_ai_plugin_icon(plugin_id: str, image_path: str) -> dict[str, Any]:
    """Make a picture the draft's Store icon: ``assets/icon.png`` (fitted into a 512 px
    square, transparent around it), or ``assets/icon.svg`` for an SVG kept as it is."""
    from PIL import Image, UnidentifiedImageError

    from backend.uefn_plugins.plugin_lint import STORE_ICON_MAX_BYTES, STORE_ICONS, store_icon_problem

    try:
        root = _draft_root(plugin_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not (root / "plugin.json").is_file():
        return {"ok": False, "error": f"draft not found: {root.name}"}
    src = Path(str(image_path or "").strip())
    if not src.is_file():
        return {"ok": False, "error": f"no picture at {image_path!r}"}
    if src.suffix.lower() == ".svg":
        rel, data = "assets/icon.svg", src.read_bytes()
    else:
        try:
            with Image.open(src) as im:
                pic = im.convert("RGBA")
        except (OSError, UnidentifiedImageError) as exc:
            return {"ok": False, "error": f"{src.name} isn't a picture: {exc}"}
        pic.thumbnail((ICON_PX, ICON_PX), Image.LANCZOS)
        square = Image.new("RGBA", (ICON_PX, ICON_PX), (0, 0, 0, 0))
        square.paste(pic, ((ICON_PX - pic.width) // 2, (ICON_PX - pic.height) // 2))
        buf = io.BytesIO()
        square.save(buf, "PNG", optimize=True)
        if buf.tell() > STORE_ICON_MAX_BYTES:  # a busy photo: 256 colors still reads as an icon
            buf = io.BytesIO()
            square.quantize(256).save(buf, "PNG", optimize=True)
        rel, data = "assets/icon.png", buf.getvalue()
    if len(data) > STORE_ICON_MAX_BYTES:
        return {"ok": False, "error": f"{src.name} is {len(data) // 1024} KB; the Store takes an icon up to 700 KB"}
    # The Store takes the first icon file it finds: never hide this one behind another.
    first = next((r for r in STORE_ICONS if (root / r).is_file()), "")
    if first and STORE_ICONS.index(first) < STORE_ICONS.index(rel):
        return {"ok": False, "error": f"{first} is the Store icon (the Store picks it first). Delete it with "
                                      "ducky_plugin_delete_file, then set this picture again."}
    (root / "assets").mkdir(exist_ok=True)
    (root / rel).write_bytes(data)
    return {"ok": True, "id": root.name, "icon": rel, "bytes": len(data), "store_ready": not store_icon_problem(root)}


def validate_ai_plugin(plugin_id: str) -> dict[str, Any]:
    from backend.uefn_plugins.store import normalize_plugin_id, validate_plugin_skills

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    root = _draft_root(pid)
    man_path = root / "plugin.json"
    if not man_path.is_file():
        return {"ok": False, "error": f"draft missing plugin.json: {pid}"}
    try:
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"plugin.json: {exc}"}
    if not isinstance(manifest, dict):
        return {"ok": False, "error": "plugin.json must be an object"}
    mid = str(manifest.get("id") or "")
    if mid != pid:
        return {"ok": False, "error": f"plugin.json id {mid!r} must equal folder id {pid!r}"}
    if not isinstance(manifest.get("contributes", {}), dict):
        return {"ok": False, "error": "contributes must be an object"}
    errors: list[str] = []
    backend_dir = root / "backend"
    if backend_dir.is_dir():
        for py in sorted(backend_dir.rglob("*.py")):
            try:
                py_compile.compile(str(py), doraise=True)
            except py_compile.PyCompileError as exc:
                errors.append(f"{py.relative_to(root).as_posix()}: {exc}")
    try:
        skill_ids = validate_plugin_skills(root, pid)
    except ValueError as exc:
        errors.append(str(exc))
        skill_ids = []
    from backend.uefn_plugins.plugin_lint import lint_draft

    # The ai_plugins reference rules, each with how to fix it.
    errors.extend(lint_draft(root, manifest, pid))
    from backend.uefn_plugins.compile import shipped_personal_paths
    from backend.uefn_plugins.personal_paths import personal_path_message

    personal = shipped_personal_paths(root)
    # Not an error while building (installs keep working); publishing refuses it.
    warnings = [personal_path_message(personal)] if personal else []
    from backend.uefn_plugins.plugin_lint import check_findability, check_store_ready

    find_errors, find_warnings = check_findability(root, pid)
    errors.extend(find_errors)
    warnings.extend(find_warnings)
    # Not an error while building (installs keep working); a Store build refuses the errors.
    store_errors, store_warnings = check_store_ready(root, manifest, pid)
    warnings.extend(store_errors + store_warnings)
    if errors:
        return {"ok": False, "id": pid, "errors": errors, **({"warnings": warnings} if warnings else {})}
    return {
        "ok": True,
        "id": pid,
        "label": str(manifest.get("label") or pid),
        "version": str(manifest.get("version") or ""),
        "skills": skill_ids,
        **({"warnings": warnings} if warnings else {}),
    }


def _zip_draft(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(root).parts
            if any(p in _SKIP_DIR_NAMES for p in rel_parts):
                continue
            if path.suffix.lower() in _SECRET_SUFFIXES:
                raise ValueError(f"refusing to pack secret-looking file: {path.name}")
            zf.writestr("/".join(rel_parts), path.read_bytes())
    return buf.getvalue()


def install_ai_plugin(plugin_id: str) -> dict[str, Any]:
    from backend.uefn_plugins.store import import_plugin_from_bytes, normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    root = _draft_root(pid)
    if not (root / "plugin.json").is_file():
        return {"ok": False, "error": f"draft not found: {pid}"}
    validated = validate_ai_plugin(pid)
    if not validated.get("ok"):
        return validated
    try:
        raw = _zip_draft(root)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    dropped = _drop_mcp_tools(pid)
    result = import_plugin_from_bytes(raw, source="ai", replace=True)
    if result.get("ok"):
        _notify_plugins()
    elif dropped:
        # Nothing was replaced: the plugin that is still installed gets its tools back.
        from backend.uefn_plugins.plugin_host.lifecycle import is_plugin_enabled, reload_single_plugin

        if is_plugin_enabled(pid):
            reload_single_plugin(pid)
    return result


def _drop_mcp_tools(pid: str) -> list[str]:
    """Take ``pid``'s tools off the MCP server before a reinstall. ``api.tool`` keeps a
    name that is already on the server across a reload (and with it the old code), so
    without this a reinstalled draft would go on running its previous tools."""
    from backend.uefn_plugins.plugin_host.tool_registry import plugin_for_tool
    from backend.uefn_plugins.plugin_host.ui_registry import get_contributions

    tools = getattr(getattr(mcp, "_tool_manager", None), "_tools", None)
    if not isinstance(tools, dict):
        return []
    names = ((get_contributions().get("agent_tools") or {}).get(pid) or {}).get("tools") or []
    dropped = [n for n in names if plugin_for_tool(n) == pid and tools.pop(n, None) is not None]
    return dropped


def delete_ai_plugin_draft(plugin_id: str, *, confirm: bool = False) -> dict[str, Any]:
    from backend.uefn_plugins.store import normalize_plugin_id

    if not confirm:
        return {"ok": False, "error": "confirm=true required to delete draft"}
    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    root = _draft_root(pid)
    if not root.is_dir():
        return {"ok": False, "error": f"draft not found: {pid}"}
    shutil.rmtree(root)
    return {"ok": True, "id": pid, "deleted": True}


# --- Release checks: run before a plugin is uploaded to the Store --------------------------

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Same rule as the Store's security scan (DuckyOS artifact_scan.rs is_hidden_char and
# is_scannable_path): one of these in a UTF-8 text file and the Store refuses the upload.
_HIDDEN_CHAR_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‍‪-‮⁦-⁩﻿]")
_STORE_TEXT_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".css", ".py", ".json",
                        ".toml", ".md", ".html", ".htm", ".sh", ".ps1", ".bat", ".cmd")


def _git(cwd: Path, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)


def _rmtree(path: Path) -> None:
    def _writable(func, p, _exc) -> None:  # git marks its objects read-only on Windows
        os.chmod(p, stat.S_IWRITE)
        func(p)

    kw = {"onexc": _writable} if sys.version_info >= (3, 12) else {"onerror": _writable}
    shutil.rmtree(path, **kw)


def clean_clone_test_check(repo: str | Path, *, test_cmd: list[str] | None = None,
                           timeout: float = 900.0) -> dict[str, Any]:
    """Run the plugin's tests in a fresh clone of HEAD: no ignored, untracked or uncommitted
    files, the way the release workflow sees the repo. A failure names the ignored files the
    output mentions (a test reading the git-ignored tests/evidence/ passes only in the draft)."""
    repo = Path(repo).resolve()
    try:
        top = _git(repo, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"git failed: {exc}"}
    if top.returncode != 0:
        return {"ok": False, "error": f"not a git repository: {repo}"}
    repo = Path(top.stdout.strip())
    ignored = _git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory").stdout.split()
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").stdout.split()
    tmp = Path(tempfile.mkdtemp(prefix="ducky-release-clone-"))
    try:
        clone = tmp / "clone"
        cloned = _git(tmp, "clone", "--quiet", "--no-hardlinks", str(repo), str(clone), timeout=300)
        if cloned.returncode != 0:
            return {"ok": False, "error": f"clean clone failed: {cloned.stderr.strip()[-500:]}"}
        cmd = test_cmd or [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            run = subprocess.run(cmd, cwd=str(clone), capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=timeout, env=env, creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"clean clone: tests ran over {int(timeout)} s"}
        output = (run.stdout or "") + (run.stderr or "")
        if run.returncode in (0, 5):  # 5 = pytest found no tests
            return {"ok": True, "clone_tests": "passed" if run.returncode == 0 else "no tests"}
        seen = output.replace("\\", "/")
        missing = [p for p in ignored + untracked if p.rstrip("/") and p.rstrip("/") in seen]
        reason = (f"tests read files missing from a clean clone: {', '.join(missing)}"
                  if missing else "tests fail")
        return {
            "ok": False,
            "error": f"clean clone: {reason} → fix before publishing",
            "missing_files": missing,
            "output": output[-4000:],
        }
    finally:
        _rmtree(tmp)


def scan_zip_hidden_chars(zip_file: str | Path | bytes) -> dict[str, Any]:
    """Scan every text file in a built zip for the hidden characters the Store refuses (a
    minifier turning "\\u0001" into a raw control character). First hit per file, with offset."""
    data = zip_file if isinstance(zip_file, bytes) else Path(zip_file).read_bytes()
    findings: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(_STORE_TEXT_SUFFIXES):
                continue
            try:
                text = zf.read(name).decode("utf-8")
            except UnicodeDecodeError:  # the Store's scan skips non-UTF-8 files too
                continue
            hit = _HIDDEN_CHAR_RE.search(text)
            if hit:
                code = f"U+{ord(hit.group()):04X}"
                findings.append({"path": name, "offset": hit.start(), "char": code,
                                 "message": f"{name} offset {hit.start()}: {code} — the Store will refuse this"})
    if findings:
        return {"ok": False, "error": "; ".join(f["message"] for f in findings), "findings": findings}
    return {"ok": True}


# One release path at a time: a release workflow run started for a plan step holds its plugin
# until it ends, so ducky_plugin_publish or release.py can't publish under it.


def _releases_dir() -> Path:
    from backend.uefn_plugins.store import appdata_ai_plugins_dir

    return appdata_ai_plugins_dir() / ".releases"


def _append_step_note(chat_id: str, step_id: str, line: str) -> None:
    from backend.agent.coding_agents import plans

    plan = plans.load_plan(chat_id) or {}
    _, _, node = plans._walk_find(list(plan.get("nodes") or []), step_id)
    body = str((node or {}).get("body_markdown") or "")
    plans.update_node(chat_id, step_id, body_markdown=f"{body}\n\n{line}".strip())


def start_release_run(plugin_id: str, workflow_id: str, run_id: str, *, chat_id: str = "",
                      step_id: str = "", clone: str = "") -> dict[str, Any]:
    """Record that release workflow ``run_id`` publishes ``plugin_id`` (and its run id on the
    plan step). ``clone`` is the folder the workflow works in, checked for leftovers at the end."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    rec = {"plugin": pid, "workflow_id": workflow_id, "run_id": run_id,
           "chat_id": chat_id, "step_id": step_id, "clone": clone}
    _write_json(_releases_dir() / f"{pid}.json", rec)
    if chat_id and step_id:
        try:
            _append_step_note(chat_id, step_id, f"Release workflow run `{run_id}` started.")
        except Exception:  # noqa: BLE001 — the lock holds without the note
            pass
    return {"ok": True, **rec}


def release_busy(plugin_id: str, *, run_id: str = "") -> str:
    """"release workflow <run> is running" while another release run holds ``plugin_id``,
    else "". A run that has ended is finished here (notes, leftovers) and frees the plugin."""
    from backend.automations import live_runs
    from backend.uefn_plugins.store import normalize_plugin_id

    path = _releases_dir() / f"{normalize_plugin_id(plugin_id)}.json"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if run_id and rec.get("run_id") == run_id:
        return ""
    data = next((d for d in live_runs.runs(str(rec.get("workflow_id") or ""))
                 if d.get("run") == rec.get("run_id")), None)
    if data and data.get("state") == "started":
        return f"release workflow {rec.get('run_id')} is running"
    finish_release_run(plugin_id, data)
    return ""


def finish_release_run(plugin_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the ended run's result and its clone's uncommitted files into the step's notes,
    then free the plugin. ``data`` is the live_runs record (None: it is gone)."""
    from backend.uefn_plugins.store import normalize_plugin_id

    path = _releases_dir() / f"{normalize_plugin_id(plugin_id)}.json"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "no release run recorded"}
    state = str((data or {}).get("state") or "ended")
    errors = [e.get("error") for e in (data or {}).get("events") or []
              if e.get("type") == "workflow_run" and e.get("error")]
    word = {"done": "PASSED", "error": "FAILED", "stopped": "STOPPED"}.get(state, state.upper())
    line = f"workflow run {rec.get('run_id')} {word}" + (f": {errors[-1]}" if errors else "")
    dirty: list[str] = []
    clone = Path(str(rec.get("clone") or ""))
    if str(rec.get("clone") or "") and clone.is_dir():
        try:
            dirty = [ln[3:] for ln in _git(clone, "status", "--porcelain").stdout.splitlines() if ln.strip()]
        except (OSError, subprocess.SubprocessError):
            pass
    if dirty:
        line += f"; clone has {len(dirty)} uncommitted files: {', '.join(dirty[:10])}"
    if rec.get("chat_id") and rec.get("step_id"):
        try:
            _append_step_note(rec["chat_id"], rec["step_id"], line)
        except Exception:  # noqa: BLE001 — a finished step refuses notes; the result is returned
            pass
    path.unlink(missing_ok=True)
    return {"ok": True, "state": state, "dirty": dirty, "note": line}


def release_check(plugin_id: str, *, repo: str = "", zip_path: str = "", run_id: str = "") -> dict[str, Any]:
    """Every pre-upload check: no other release run holds the plugin, its tests pass in a
    clean clone of ``repo``, and the built ``zip_path`` has no hidden characters."""
    busy = release_busy(plugin_id, run_id=run_id)
    if busy:
        return {"ok": False, "error": busy}
    out: dict[str, Any] = {"ok": True}
    for name, arg, check in (("clean_clone", repo, clean_clone_test_check),
                             ("hidden_chars", zip_path, scan_zip_hidden_chars)):
        if arg:
            res = check(arg)
            out[name] = res
            if not res.get("ok"):
                out.update(ok=False, error=res.get("error"))
                return out
    return out


@mcp.tool()
def ducky_plugin_scaffold(
    id: str,
    label: str = "",
    description: str = "",
    data_sharing: bool | None = None,
    pretty: bool = False,
) -> str:
    """Create a shared AI plugin draft under AppData/ai_plugins/<id>/.

    Writes plugin.json (MCP tools, one workflow node, a template), backend/__init__.py
    (tools with listener=False, panel RPCs that take the panel's params as keyword
    arguments) and skills/<id>/SKILL.md. Any duckie can edit it. Does not install — use
    ducky_plugin_validate, ducky_plugin_install, then ducky_plugin_test.

    Before authoring: skill_read_subskill("ducky", "ai_plugins"), and reuse existing tools
    (skill_search(kind="tools")) with api.call_tool. Every user action is an @api.tool()
    (listener=False unless it calls UEFN) and an automations/pipeline node with a template;
    bundled skills/<id>/SKILL.md; changeset on mutators. Panels link the UI kit
    (../../_kit/ducky.js) and use Appearance vars only (var(--bg) / --fg / --accent): never
    hardcode colors (no hex, rgb(), named colors; ducky_plugin_validate rejects them). After
    every change: ducky_plugin_validate → ducky_plugin_test → ducky_plugin_errors until
    clean. Share only through the Store (ducky_plugin_publish).

    data_sharing: the user's answer to "should your team share this plugin's data (a team
    copy with changes, locks, history and live editing)?", asked once before scaffolding.
    Omit it only when you haven't asked yet: a Store build refuses a plugin without it.
    """
    return tool_json(
        scaffold_ai_plugin(id, label=label, description=description, data_sharing=data_sharing), pretty=pretty
    )


@mcp.tool()
def ducky_plugin_list(id: str = "", pretty: bool = False) -> str:
    """Census of AI plugin drafts and installed desktop plugins.

    Omit id: {drafts, installed} (id/label/version/source/enabled). This is the
    inventory — never glob, ls, or PowerShell AppData ai_plugins / uefn_plugins.
    Empty drafts and no matching installed id → ducky_plugin_scaffold.
    With id: files inside that draft only.
    """
    return tool_json(list_ai_plugin_drafts(id), pretty=pretty)


@mcp.tool()
def ducky_plugin_write_file(
    id: str,
    path: str,
    content: str,
    pretty: bool = False,
) -> str:
    """Write a UTF-8 file inside an AI plugin draft (path-jailed to the draft folder).

    Cannot touch core app files. Refuses .dat/.env/.pem/.key.
    """
    return tool_json(write_ai_plugin_file(id, path, content), pretty=pretty)


@mcp.tool()
def ducky_plugin_vendor(
    id: str,
    package: str,
    version: str,
    files: list[str] | None = None,
    pretty: bool = False,
) -> str:
    """Download an exact npm package version into the plugin's ui/vendor/ folder.

    Plugin panels never load code from the internet (CDN scripts, styles and imports are
    blocked), so every library ships inside the plugin. Give the exact version (0.160.0,
    never latest or a range). Leave ``files`` empty to list the package's files, then call
    again with the built file(s) the panel loads (e.g. ["build/three.min.js"]). The
    package's license is copied alongside; the download is checked against npm's hash.
    Returns the <script>/<link> tags to put in the page.
    """
    from backend.uefn_plugins.store import normalize_plugin_id
    from backend.uefn_plugins.vendor import vendor_npm

    try:
        root = _draft_root(normalize_plugin_id(id))
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if not root.is_dir():
        return tool_json({"ok": False, "error": f"draft not found: {id} — call ducky_plugin_scaffold first"}, pretty=pretty)
    return tool_json(vendor_npm(root, package, version, files), pretty=pretty)


@mcp.tool()
def ducky_plugin_read_file(id: str, path: str, start_line: int = 0, end_line: int = 0, pretty: bool = False) -> str:
    """Read a UTF-8 file from an AI plugin draft (path-jailed).

    start_line/end_line (1-based, inclusive) read only those lines. A file over 48 KB read
    without them returns its outline (definitions with line numbers) and first lines."""
    return tool_json(read_ai_plugin_file(id, path, start_line=start_line, end_line=end_line), pretty=pretty)


@mcp.tool()
def ducky_plugin_edit_file(id: str, path: str, old_string: str, new_string: str,
                           replace_all: bool = False, pretty: bool = False) -> str:
    """Change part of a file in an AI plugin draft: replace old_string (exact text, unique in the
    file unless replace_all) with new_string. Use it instead of rewriting a whole file with
    ducky_plugin_write_file; read the lines first with ducky_plugin_read_file(start_line, end_line)."""
    return tool_json(edit_ai_plugin_file(id, path, old_string, new_string, replace_all=replace_all), pretty=pretty)


@mcp.tool()
def ducky_plugin_move_file(id: str, path: str, to: str, pretty: bool = False) -> str:
    """Move or rename a file or folder inside an AI plugin draft (both paths inside the draft;
    the target must not exist). Move development evidence and check scripts under tests/:
    tests never ship in a build."""
    return tool_json(move_ai_plugin_file(id, path, to), pretty=pretty)


@mcp.tool()
def ducky_plugin_delete_file(id: str, path: str, recursive: bool = False, pretty: bool = False) -> str:
    """Delete a file from an AI plugin draft; a folder needs recursive=true. plugin.json and the
    draft itself can't be deleted here (ducky_plugin_delete_draft removes a whole draft)."""
    return tool_json(delete_ai_plugin_file(id, path, recursive=recursive), pretty=pretty)


@mcp.tool()
def ducky_plugin_validate(id: str, pretty: bool = False) -> str:
    """Validate an AI plugin draft: plugin.json, py_compile backend, bundled skills, and
    the reference rules (an MCP tool per panel RPC, a handled workflow node + template,
    skills/<id>/SKILL.md, Appearance variables only and no color literals in UI files,
    :focus-visible styles for panels, no reading .py files by path). Each error says
    how to fix it; then ducky_plugin_install, and ducky_plugin_test to run it. Warnings
    include what the Store would refuse (no icon, no "data_sharing", a team folder inside
    the island): fix them before publishing."""
    return tool_json(validate_ai_plugin(id), pretty=pretty)


@mcp.tool()
def ducky_plugin_set_icon(id: str, image_path: str, pretty: bool = False) -> str:
    """Make a picture the plugin draft's Store icon (assets/icon.png, 512 px; an .svg is kept as is).

    The Store rejects a new plugin without an icon. image_path is a picture on this PC: one
    ducky_ai_image made (ask the user first, it is billed), one from their own image model, or
    a file they picked. Refuses when another icon file the Store would pick first is there.
    """
    return tool_json(set_ai_plugin_icon(id, image_path), pretty=pretty)


@mcp.tool()
def ducky_plugin_install(id: str, pretty: bool = False) -> str:
    """Zip the AI plugin draft and install it into uefn_plugins/ with source=ai.

    Does not auto-enable. First enable needs a user trust confirm
    (ducky_store_set_enabled → needs_trust; user confirms in Settings → Store).
    """
    return tool_json(install_ai_plugin(id), pretty=pretty)


@mcp.tool()
def ducky_plugin_release_check(id: str, repo: str = "", zip_path: str = "", run_id: str = "",
                               pretty: bool = False) -> str:
    """Checks before a plugin is uploaded to the Store; publish only when ok.

    Refuses while another release workflow run holds the plugin (run_id = your own run, if
    you are that workflow). repo: the plugin's git repo, whose tests must pass in a clean clone
    of HEAD (no ignored or untracked files). zip_path: the built zip, scanned for the hidden
    characters the Store refuses (reports file and offset).
    """
    return tool_json(release_check(id, repo=repo, zip_path=zip_path, run_id=run_id), pretty=pretty)


@mcp.tool()
def ducky_plugin_delete_draft(id: str, confirm: bool = False, pretty: bool = False) -> str:
    """Delete an AI plugin draft folder. Requires confirm=true. Does not uninstall."""
    return tool_json(delete_ai_plugin_draft(id, confirm=bool(confirm)), pretty=pretty)


@mcp.tool()
def ducky_plugin_reference(pretty: bool = False) -> str:
    """Return the AI desktop-plugin recipe (same text as skill ducky/ai_plugins)."""
    return tool_json({"ok": True, "markdown": _load_plugin_reference()}, pretty=pretty)


def _self_check() -> None:
    """ponytail: scaffold → write → jail refuse → validate → zip (no AppData install)."""
    import os

    tmp = tempfile.mkdtemp(prefix="ducky-ai-plugin-")
    try:
        os.environ["LOCALAPPDATA"] = tmp
        os.environ["USERPROFILE"] = tmp
        os.environ["HOME"] = tmp
        from backend.uefn_plugins.store import appdata_ai_plugins_dir

        assert "ai_plugins" in str(appdata_ai_plugins_dir())
        sc = scaffold_ai_plugin("hello_ai", label="Hello AI", description="test")
        assert sc.get("ok"), sc
        stub = (_draft_root("hello_ai") / "backend" / "__init__.py").read_text(encoding="utf-8")
        assert "@api.tool" in stub and "hello_ai_list" in stub
        assert "register_pipeline_node" in stub and "hello_ai.run" in stub
        man0 = json.loads((_draft_root("hello_ai") / "plugin.json").read_text(encoding="utf-8"))
        contrib0 = man0.get("contributes") or {}
        assert contrib0.get("agent.tools")
        auto0 = contrib0.get("automations") or {}
        assert any(n.get("id") == "hello_ai.run" for n in auto0.get("nodes") or [])
        assert auto0.get("templates")
        bad = write_ai_plugin_file("hello_ai", "../escape.py", "x = 1")
        assert not bad.get("ok"), bad
        bad2 = write_ai_plugin_file("hello_ai", "backend/../../escape.py", "x = 1")
        assert not bad2.get("ok"), bad2
        wr = write_ai_plugin_file(
            "hello_ai",
            "ui/theme.css",
            ":root { --accent: #0ff; }\n",
        )
        assert wr.get("ok"), wr
        man = json.loads((_draft_root("hello_ai") / "plugin.json").read_text(encoding="utf-8"))
        contrib = dict(man.get("contributes") or {})
        contrib["appearance.css"] = [{"entry": "ui/theme.css"}]
        man["contributes"] = contrib
        wrj = write_ai_plugin_file("hello_ai", "plugin.json", json.dumps(man))
        assert wrj.get("ok"), wrj
        val = validate_ai_plugin("hello_ai")
        assert val.get("ok"), val
        zipped = _zip_draft(_draft_root("hello_ai"))
        assert b"plugin.json" in zipped and len(zipped) > 50
        listed = list_ai_plugin_drafts("hello_ai")
        assert "ui/theme.css" in listed.get("files", [])
        census = list_ai_plugin_drafts("")
        assert census.get("ok") and "installed" in census and "drafts" in census
        assert any(d.get("id") == "hello_ai" for d in census.get("drafts") or [])
        deleted = delete_ai_plugin_draft("hello_ai", confirm=True)
        assert deleted.get("ok"), deleted
        empty = list_ai_plugin_drafts("")
        assert empty.get("ok") and empty.get("drafts") == [] and "installed" in empty
        print("panel_ai_plugins.py self-check ok")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _self_check()
