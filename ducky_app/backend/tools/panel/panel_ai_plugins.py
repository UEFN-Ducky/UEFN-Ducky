"""AI-made desktop plugins: draft under AppData/ai_plugins, install via existing zip path.

Shared per-install workspace — any duckie / IDE agent can edit any draft.
Drafts are never loaded; only ``import_plugin_from_bytes(source=\"ai\")`` installs.
"""

from __future__ import annotations

import io
import json
import py_compile
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from backend.util.json_util import tool_json
from backend.server import mcp

_SKIP_DIR_NAMES = frozenset({"scripts", "deploy", ".git", "__pycache__", ".venv", "node_modules"})
_SECRET_SUFFIXES = frozenset({".dat", ".env", ".pem", ".key"})
_MAX_FILE_CHARS = 512 * 1024

_PLUGIN_REFERENCE = """# AI desktop plugin reference

Drafts live in `%LOCALAPPDATA%/UEFN-Ducky/ai_plugins/<id>/` (shared across all chats).
Flow: `ducky_plugin_scaffold` → write files → `ducky_plugin_validate` → `ducky_plugin_install`
→ user trusts once in Settings → Store → iterate by editing draft + reinstall.

## Minimal plugin.json

```json
{
  "id": "hello",
  "kind": "plugin",
  "version": "1.0.0",
  "label": "Hello",
  "description": "…",
  "min_app_version": "1.0.0",
  "default_enabled": false,
  "secret_keys": [],
  "contributes": {},
  "backend": { "entry": "backend", "register": "register" }
}
```

Id: `^[a-z][a-z0-9_-]{0,63}$`. Never edit core app files — only this draft + contributions.
Never write into a UEFN project outside `Content/**` and `.ducky/**`. Never mutate
`*.digest.verse` (UEFN auto-edits those on Verse build). Scratch files go in
`%LOCALAPPDATA%/UEFN-Ducky/`, not `Saved/` or the project root.

## Contribution hooks (when plugin enabled)

| Hook | Effect |
|------|--------|
| `appearance.profiles` | Theme profiles in Appearance |
| `appearance.css` | Stylesheet after ThemeProvider (`[{ "entry": "ui/theme.css" }]`) |
| `appearance.effects` | Background FX into `#ducky-fx-root` |
| `appearance.skin` | Full chrome swap (frame/header portals) |
| `ui.panels` | Sandboxed HTML at `/plugin-ui/<id>/…` |
| `dock.panels` | Dock panels (`id`, `title`, `defaultSide`, `ui`) |
| `editor.kinds` | Editor kinds |
| `settings.tabs` / `settings.sections` | Settings UI |
| `header.buttons` | Header icons |
| `shell.boot` | Main-window script + `__duckyPluginHost` |
| `sounds` / `hooks` | Appearance → Sounds |
| `verse.templates` | New-file Verse scaffolds |
| `agent.tools` | Optional category / intent for MCP tools |
| `walkthrough` | First-enable coachmarks |

## backend/register(api)

```python
def register(api) -> None:
    @api.tool(intent=r"\\bhello\\b")
    def hello_ping(msg: str = "hi") -> str:
        '''Ping.'''
        return f"pong: {msg}"
    api.log("hello tools registered")
```

Also: `api.listener(cmd, params)`, `api.is_enabled()`, `api.log()`, `api.plugin_id`,
`api.register_secret_test`, `api.register_llm_provider`, `api.register_ide_hookup`.

Enable/disable/uninstall the installed copy with `ducky_store_set_enabled` /
`ducky_store_remove`. First enable of an AI plugin needs a user trust confirm
(agents cannot auto-trust).

## Gateway prompt caching

Bytes before the growth point change only at an epoch.

Host guarantees: byte-stable frozen system, append-only message view, sticky
tool set (union during a chat; floor reset only at an epoch), and
`prompt_cache_key` = conversation id. Volatile memory/plan/status is a last
user message (`[Live context — …]`), never the system prefix. `enable_cache`
on the payload only means “emit provider markers”; the frozen/dynamic split
is unconditional.

Plugin must:

| Family | You do | Verify with |
|--------|--------|-------------|
| Anthropic-style | Use host `cache_utils` as-is (tools + system + last-history + mid-loop every ~15 content blocks when over 20) | `cache_read_input_tokens` grows turn over turn |
| OpenAI-compatible | `openai_system_messages` + forward `cache.prompt_cache_key` | `prompt_tokens_details.cached_tokens` |
| Ollama / local | Long `keep_alive`; `num_ctx` = host high-water + output headroom, allocated once, never resized; serialize identically | `prompt_eval_count` ≈ tail size on warm turns |
| Gemini | Nothing — implicit; parse usage with the host helper | `cached_content_token_count` |
"""


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
        "contributes": {},
        "backend": {"entry": "backend", "register": "register"},
    }


def _register_stub() -> str:
    return (
        '"""AI-made UEFN desktop plugin — register MCP tools when enabled."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "\n"
        "def register(api) -> None:\n"
        '    api.log("plugin registered")\n'
    )


def scaffold_ai_plugin(plugin_id: str, label: str = "", description: str = "") -> dict[str, Any]:
    from backend.uefn_plugins.store import (
        appdata_ai_plugins_dir,
        load_plugin_manifest,
        normalize_plugin_id,
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
    manifest = _minimal_manifest(pid, label, description)
    _write_json(root / "plugin.json", manifest)
    backend = root / "backend"
    backend.mkdir(exist_ok=True)
    (backend / "__init__.py").write_text(_register_stub(), encoding="utf-8")
    return {"ok": True, "id": pid, "path": str(root), "manifest": manifest}


def list_ai_plugin_drafts(plugin_id: str = "") -> dict[str, Any]:
    from backend.uefn_plugins.store import appdata_ai_plugins_dir, normalize_plugin_id

    root = appdata_ai_plugins_dir()
    if not root.is_dir():
        return {"ok": True, "drafts": [], "files": []}
    sid = (plugin_id or "").strip()
    if not sid:
        drafts: list[dict[str, Any]] = []
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
            drafts.append({"id": child.name, "label": label, "version": version, "path": str(child)})
        return {"ok": True, "drafts": drafts}
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


def write_ai_plugin_file(plugin_id: str, path: str, content: str) -> dict[str, Any]:
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
    if len(text) > _MAX_FILE_CHARS:
        return {"ok": False, "error": f"content exceeds {_MAX_FILE_CHARS} chars"}
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


def read_ai_plugin_file(plugin_id: str, path: str) -> dict[str, Any]:
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
    if len(content) > _MAX_FILE_CHARS:
        content = content[:_MAX_FILE_CHARS] + "\n…(truncated)\n"
    return {"ok": True, "id": pid, "path": path.replace("\\", "/"), "content": content}


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
    if errors:
        return {"ok": False, "id": pid, "errors": errors}
    return {
        "ok": True,
        "id": pid,
        "label": str(manifest.get("label") or pid),
        "version": str(manifest.get("version") or ""),
        "skills": skill_ids,
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
    result = import_plugin_from_bytes(raw, source="ai", replace=True)
    if result.get("ok"):
        _notify_plugins()
    return result


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


@mcp.tool()
def ducky_plugin_scaffold(
    id: str,
    label: str = "",
    description: str = "",
    pretty: bool = False,
) -> str:
    """Create a shared AI plugin draft under AppData/ai_plugins/<id>/.

    Writes minimal plugin.json + backend/register stub. Any duckie can edit it.
    Does not install — use ducky_plugin_validate then ducky_plugin_install.
    """
    return tool_json(scaffold_ai_plugin(id, label=label, description=description), pretty=pretty)


@mcp.tool()
def ducky_plugin_list(id: str = "", pretty: bool = False) -> str:
    """List AI plugin drafts, or files inside one draft when id is set."""
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
def ducky_plugin_read_file(id: str, path: str, pretty: bool = False) -> str:
    """Read a UTF-8 file from an AI plugin draft (path-jailed)."""
    return tool_json(read_ai_plugin_file(id, path), pretty=pretty)


@mcp.tool()
def ducky_plugin_validate(id: str, pretty: bool = False) -> str:
    """Validate an AI plugin draft (plugin.json, py_compile backend, bundled skills)."""
    return tool_json(validate_ai_plugin(id), pretty=pretty)


@mcp.tool()
def ducky_plugin_install(id: str, pretty: bool = False) -> str:
    """Zip the AI plugin draft and install it into uefn_plugins/ with source=ai.

    Does not auto-enable. First enable needs a user trust confirm
    (ducky_store_set_enabled → needs_trust; user confirms in Settings → Store).
    """
    return tool_json(install_ai_plugin(id), pretty=pretty)


@mcp.tool()
def ducky_plugin_delete_draft(id: str, confirm: bool = False, pretty: bool = False) -> str:
    """Delete an AI plugin draft folder. Requires confirm=true. Does not uninstall."""
    return tool_json(delete_ai_plugin_draft(id, confirm=bool(confirm)), pretty=pretty)


@mcp.tool()
def ducky_plugin_reference(pretty: bool = False) -> str:
    """Return the AI desktop-plugin authoring reference (contributions + register(api))."""
    return tool_json({"ok": True, "markdown": _PLUGIN_REFERENCE}, pretty=pretty)


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
        man["contributes"] = {"appearance.css": [{"entry": "ui/theme.css"}]}
        wrj = write_ai_plugin_file("hello_ai", "plugin.json", json.dumps(man))
        assert wrj.get("ok"), wrj
        val = validate_ai_plugin("hello_ai")
        assert val.get("ok"), val
        zipped = _zip_draft(_draft_root("hello_ai"))
        assert b"plugin.json" in zipped and len(zipped) > 50
        listed = list_ai_plugin_drafts("hello_ai")
        assert "ui/theme.css" in listed.get("files", [])
        deleted = delete_ai_plugin_draft("hello_ai", confirm=True)
        assert deleted.get("ok"), deleted
        print("panel_ai_plugins.py self-check ok")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _self_check()
