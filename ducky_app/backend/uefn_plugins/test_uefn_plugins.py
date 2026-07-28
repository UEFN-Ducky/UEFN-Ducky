"""Assert-based checks for UEFN desktop plugin install + enable gate."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path


def _zip_plugin(plugin_id: str = "demo", version: int = 1) -> bytes:
    manifest = {
        "id": plugin_id,
        "kind": "plugin",
        "version": version,
        "label": "Demo",
        "description": "test",
        "default_enabled": False,
        "contributes": {
            "settings.tabs": [{"id": "Demo", "label": "Demo", "ui": "builtin:demo"}],
            "settings.sections": [
                {
                    "tab": "Demo",
                    "id": "placement",
                    "title": "Placement",
                    "order": 10,
                    "properties": [
                        {
                            "id": "showInHeader",
                            "type": "boolean",
                            "default": True,
                            "label": "Show in header",
                        }
                    ],
                }
            ],
            "header.buttons": [
                {"id": "demo", "title": "Demo", "icon": "chat", "action": "builtin:open-discord", "order": 50},
                {"id": "game", "title": "Game", "icon": "duck", "action": "panel:game", "order": 51},
            ],
            "ui.panels": [
                {"id": "game", "title": "Demo Game", "icon": "duck", "entry": "ui/index.html"},
                {"id": "bad", "title": "Hostile", "entry": "../escape.html"},
            ],
            "appearance.profiles": [
                {
                    "id": "neon",
                    "name": "Neon Demo",
                    "foundation": {"accent": "#00ff88", "bg": "#050505"},
                    "overrides": {"fg": "#b8ffd0"},
                }
            ],
            "appearance.css": [
                {"entry": "ui/theme.css"},
                {"entry": "../escape.css"},
            ],
            "appearance.effects": [
                {"id": "sparkle", "label": "Sparkle", "entry": "ui/fx.js"},
                {"id": "bad", "label": "Bad", "entry": "../escape.js"},
            ],
            "appearance.skin": [
                {"id": "neon", "label": "Neon Skin", "entry": "ui/skin.js", "css": "ui/skin.css"},
                {"id": "bad", "label": "Bad", "entry": "../escape-skin.js"},
            ],
            "sounds": [
                {"id": "ping", "label": "Demo Ping", "file": "assets/ping.wav"},
                {"id": "bad", "label": "Bad", "file": "../escape.wav"},
            ],
            "hooks": [
                {"id": "message", "label": "Demo message"},
            ],
            "verse.templates": [
                {
                    "id": "demo_device",
                    "name": "Demo Device",
                    "icon": "🧪",
                    "description": "Demo creative_device scaffold",
                    "file": "templates/demo_device.verse",
                    "order": 10,
                },
                {
                    "id": "demo_pack",
                    "name": "Demo Pack",
                    "icon": "📦",
                    "description": "Multi-file pack",
                    "folder": "DemoPack",
                    "connects": ["needs:PlayerCore"],
                    "files": [
                        {
                            "path": "a.verse",
                            "file": "templates/pack/a.verse",
                        },
                        {
                            "path": "b.verse",
                            "file": "templates/pack/b.verse",
                        },
                    ],
                    "order": 11,
                },
                {
                    "id": "bad",
                    "name": "Bad",
                    "icon": "x",
                    "file": "../escape.verse",
                },
                {
                    "id": "bad_pack_path",
                    "name": "Bad Pack Path",
                    "icon": "x",
                    "files": [{"path": "../escape.verse", "file": "templates/pack/a.verse"}],
                },
                {
                    "id": "bad_pack_file",
                    "name": "Bad Pack File",
                    "icon": "x",
                    "files": [{"path": "x.verse", "file": "../escape.verse"}],
                },
            ],
            "agent.tools": {"category": "demo", "tools": ["demo_tool"]},
        },
        "backend": {"entry": "backend", "register": "register"},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr(
            "backend/__init__.py",
            "def register(api):\n    api.log('demo ok')\n",
        )
        zf.writestr("ui/index.html", "<!doctype html><title>demo</title>")
        zf.writestr("ui/theme.css", "/* demo theme */")
        zf.writestr("ui/fx.js", "/* demo fx */")
        zf.writestr("ui/skin.js", "/* demo skin */")
        zf.writestr("ui/skin.css", "/* demo skin css */")
        zf.writestr("assets/ping.wav", b"RIFF")
        zf.writestr(
            "templates/demo_device.verse",
            "using { /Fortnite.com/Devices }\n\ndemo_device := class(creative_device):\n    OnBegin<override>():void =\n        Print(\"demo\")\n",
        )
        zf.writestr("templates/pack/a.verse", "# pack file a\n")
        zf.writestr("templates/pack/b.verse", "# pack file b\n")
    return buf.getvalue()


def _zip_plugin_with_skills(
    plugin_id: str = "skillful",
    *,
    skill_id: str = "demo-tips",
    version: int = 1,
) -> bytes:
    manifest = {
        "id": plugin_id,
        "kind": "plugin",
        "version": version,
        "label": plugin_id,
        "description": "ships a skill",
        "default_enabled": False,
        "backend": {"entry": "backend", "register": "register"},
    }
    skill_md = (
        f"---\nname: {skill_id}\ndescription: tips\n"
        f"metadata:\n  label: Tips\n  version: 1\n---\n\n# Tips\n\nDo the thing.\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("backend/__init__.py", "def register(api):\n    pass\n")
        zf.writestr(f"skills/{skill_id}/SKILL.md", skill_md)
        zf.writestr(
            f"skills/{skill_id}/references/extra.md",
            "---\ndescription: extra\nmetadata:\n  label: Extra\n---\n\nExtra.\n",
        )
    return buf.getvalue()


def _zip_plugin_bad_skill_escape(plugin_id: str = "sneaky") -> bytes:
    """Skill folder name that normalizes differently / invalid id."""
    manifest = {
        "id": plugin_id,
        "kind": "plugin",
        "version": 1,
        "label": plugin_id,
        "default_enabled": False,
        "backend": {"entry": "backend", "register": "register"},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("backend/__init__.py", "def register(api):\n    pass\n")
        zf.writestr(
            "skills/Bad Id!/SKILL.md",
            "---\nname: bad\ndescription: x\nmetadata:\n  version: 1\n---\n\n# Bad\n",
        )
    return buf.getvalue()


def _zip_plugin_with_api_tools(plugin_id: str = "hookprobe", version: int = 1) -> bytes:
    """Plugin that registers tools only via api.tool() (no manifest tool list)."""
    manifest = {
        "id": plugin_id,
        "kind": "plugin",
        "version": version,
        "label": "Hook Probe",
        "description": "api.tool hook test",
        "default_enabled": False,
        "contributes": {
            "agent.tools": {"category": "hookprobe", "intent_pattern": r"\bhookprobe\b"},
        },
        "backend": {"entry": "backend", "register": "register"},
    }
    backend = """
def register(api):
    @api.tool()
    def hook_probe_ping():
        return "pong"

    @api.tool(name="hook_probe_named")
    def _named():
        return "named"

    api.log("hookprobe tools registered")
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("backend/__init__.py", backend)
    return buf.getvalue()


def _zip_plugin_with_secrets(plugin_id: str, secret_keys: list[str], version: int = 1) -> bytes:
    manifest = {
        "id": plugin_id,
        "kind": "plugin",
        "version": version,
        "label": plugin_id,
        "description": "secrets test",
        "default_enabled": False,
        "secret_keys": secret_keys,
        "backend": {"entry": "backend", "register": "register"},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("backend/__init__.py", "def register(api):\n    pass\n")
    return buf.getvalue()


def _zip_tts_plugin(plugin_id: str = "voxy", version: int = 1) -> bytes:
    """Plugin that ships a static voice + registers a synthesizer and a dynamic lister."""
    manifest = {
        "id": plugin_id,
        "kind": "plugin",
        "version": version,
        "label": plugin_id,
        "description": "tts test",
        "default_enabled": False,
        "contributes": {
            "tts.voices": [{"id": "premade1", "label": "Premade One"}],
        },
        "backend": {"entry": "backend", "register": "register"},
    }
    backend = (
        "def register(api):\n"
        "    api.register_tts(lambda text, voice: {'audio_base64': 'QUJD', 'mime': 'audio/mpeg'})\n"
        "    api.register_tts_voices(lambda: [{'id': 'dynamic1', 'label': 'Dynamic One'}])\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("backend/__init__.py", backend)
    return buf.getvalue()


def _zip_hostile() -> bytes:
    """Traversal entries must be skipped; plugin.json still installs."""
    manifest = {
        "id": "hostile",
        "kind": "plugin",
        "version": 1,
        "label": "Hostile",
        "default_enabled": False,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("../escape.txt", "nope")
        zf.writestr("..\\escape2.txt", "nope")
        zf.writestr("/abs.txt", "nope")
        zf.writestr("nested/../../escape3.txt", "nope")
    return buf.getvalue()


def main() -> None:
    import os
    import tempfile

    # Isolate AppData (+ IDE skill deploy roots) for this check.
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LOCALAPPDATA"] = tmp
        os.environ["USERPROFILE"] = tmp
        os.environ["HOME"] = tmp
        from backend.uefn_plugins.store import (
            import_plugin_from_bytes,
            is_plugin_installed,
            list_uefn_plugins,
            set_uefn_plugin_enabled,
            uninstall_uefn_plugin,
        )
        from backend.uefn_plugins.host import get_contributions, is_plugin_enabled, reload_plugins

        raw = _zip_plugin("demo", 1)
        result = import_plugin_from_bytes(raw, source="local", replace=True)
        assert result.get("ok"), result
        assert is_plugin_installed("demo")
        assert not is_plugin_enabled("demo")

        trust = set_uefn_plugin_enabled("demo", True, trust_local=False)
        assert trust.get("needs_trust"), trust
        enabled = set_uefn_plugin_enabled("demo", True, trust_local=True)
        assert enabled.get("ok"), enabled
        assert is_plugin_enabled("demo")

        reload_plugins()
        contrib = get_contributions()
        assert any(t.get("id") == "Demo" for t in contrib["settings_tabs"]), contrib
        assert any(
            s.get("id") == "placement" and s.get("tab") == "Demo" for s in contrib["settings_sections"]
        ), contrib
        assert any(b.get("id") == "demo" and b.get("action") == "builtin:open-discord" for b in contrib["header_buttons"]), contrib
        ui_panels = contrib.get("ui_panels") or []
        assert any(p.get("id") == "game" and p.get("entry") == "ui/index.html" for p in ui_panels), ui_panels
        assert not any(p.get("id") == "bad" for p in ui_panels), ui_panels
        assert "demo" in contrib["enabled_ids"]

        appearance_profiles = contrib.get("appearance_profiles") or []
        assert any(
            p.get("id") == "neon" and p.get("plugin_id") == "demo" and p.get("name") == "Neon Demo"
            for p in appearance_profiles
        ), appearance_profiles
        appearance_css = contrib.get("appearance_css") or []
        assert any(c.get("entry") == "ui/theme.css" and c.get("plugin_id") == "demo" for c in appearance_css), (
            appearance_css
        )
        assert not any("escape" in str(c.get("entry") or "") for c in appearance_css), appearance_css
        appearance_effects = contrib.get("appearance_effects") or []
        assert any(
            e.get("id") == "sparkle" and e.get("entry") == "ui/fx.js" and e.get("plugin_id") == "demo"
            for e in appearance_effects
        ), appearance_effects
        assert not any(e.get("id") == "bad" for e in appearance_effects), appearance_effects
        appearance_skins = contrib.get("appearance_skins") or []
        assert any(
            s.get("id") == "neon"
            and s.get("entry") == "ui/skin.js"
            and s.get("css") == "ui/skin.css"
            and s.get("plugin_id") == "demo"
            for s in appearance_skins
        ), appearance_skins
        assert not any(s.get("id") == "bad" for s in appearance_skins), appearance_skins
        sounds = contrib.get("sounds") or []
        assert any(
            s.get("id") == "ping"
            and s.get("file") == "assets/ping.wav"
            and s.get("plugin_id") == "demo"
            for s in sounds
        ), sounds
        assert not any(s.get("id") == "bad" for s in sounds), sounds
        hooks = contrib.get("hooks") or []
        assert any(
            h.get("id") == "message" and h.get("plugin_id") == "demo" for h in hooks
        ), hooks
        verse_templates = contrib.get("verse_templates") or []
        assert any(
            t.get("id") == "demo_device"
            and t.get("plugin_id") == "demo"
            and t.get("name") == "Demo Device"
            and "creative_device" in str(t.get("content") or "")
            and t.get("file") == "templates/demo_device.verse"
            for t in verse_templates
        ), verse_templates
        pack = next((t for t in verse_templates if t.get("id") == "demo_pack"), None)
        assert pack is not None, verse_templates
        assert pack.get("folder") == "DemoPack"
        assert pack.get("connects") == ["needs:PlayerCore"]
        pack_files = pack.get("files") or []
        assert len(pack_files) == 2, pack_files
        assert {f.get("path") for f in pack_files} == {"a.verse", "b.verse"}
        assert all("pack file" in str(f.get("content") or "") for f in pack_files)
        assert "# pack file a" in str(pack.get("content") or "")
        assert not any(t.get("id") == "bad" for t in verse_templates), verse_templates
        assert not any(t.get("id") == "bad_pack_path" for t in verse_templates), verse_templates
        assert not any(t.get("id") == "bad_pack_file" for t in verse_templates), verse_templates

        from backend.uefn_plugins.webview import (
            panel_post_origin_allowed,
            sanitize_entry,
        )

        demo_root = Path(tmp) / "UEFN-Ducky" / "uefn_plugins" / "demo"
        assert sanitize_entry("ui/index.html", demo_root) == "ui/index.html"
        assert sanitize_entry("../escape.html", demo_root) is None
        assert sanitize_entry("/abs.html", demo_root) is None
        assert panel_post_origin_allowed(None, "http://127.0.0.1:4199/")
        assert not panel_post_origin_allowed("null", "http://127.0.0.1:4199/")
        assert not panel_post_origin_allowed("http://evil.example", "http://127.0.0.1:4199/")
        assert panel_post_origin_allowed("http://127.0.0.1:4199/", "http://127.0.0.1:4199/")

        from backend.uefn_plugins.host import (
            filter_uefn_plugin_tools,
            plugin_intent_rows,
            set_active_uefn_agent_plugin_ids,
            uefn_agent_tool_rows,
            uefn_agent_tools_allowed,
        )

        rows = uefn_agent_tool_rows()
        assert any(r["id"] == "demo" for r in rows), rows
        demo_row = next(r for r in rows if r["id"] == "demo")
        assert demo_row["enabled"] is True
        assert demo_row["default_enabled"] is True

        # Unscoped (external MCP): tools allowed.
        set_active_uefn_agent_plugin_ids(None)
        assert uefn_agent_tools_allowed("demo")
        assert plugin_intent_rows()

        # Agent run with empty opt-in: blocked.
        set_active_uefn_agent_plugin_ids([])
        assert not uefn_agent_tools_allowed("demo")
        assert plugin_intent_rows() == []

        class _T:
            def __init__(self, name: str) -> None:
                self.name = name

        filtered = filter_uefn_plugin_tools([_T("demo_tool"), _T("spawn_actor")])
        assert [t.name for t in filtered] == ["spawn_actor"]

        set_active_uefn_agent_plugin_ids(["demo"])
        assert uefn_agent_tools_allowed("demo")
        filtered_on = filter_uefn_plugin_tools([_T("demo_tool"), _T("spawn_actor")])
        assert [t.name for t in filtered_on] == ["demo_tool", "spawn_actor"]
        set_active_uefn_agent_plugin_ids(None)

        # Disable: tools/skills gone immediately; chat allowlists are not wiped.
        set_uefn_plugin_enabled("demo", False)
        set_active_uefn_agent_plugin_ids(None)
        assert not uefn_agent_tools_allowed("demo")
        filtered_dis = filter_uefn_plugin_tools([_T("demo_tool"), _T("spawn_actor")])
        assert [t.name for t in filtered_dis] == ["spawn_actor"]
        assert set_uefn_plugin_enabled("demo", True, trust_local=True).get("ok")
        set_active_uefn_agent_plugin_ids(None)
        assert uefn_agent_tools_allowed("demo")

        # Store must not overwrite local.
        from backend.uefn_plugins.store import import_plugin_from_bytes as again

        blocked = again(_zip_plugin("demo", 2), source="store", replace=True)
        assert not blocked.get("ok"), blocked

        plugins = list_uefn_plugins()
        row = next(p for p in plugins if p["id"] == "demo")
        assert row["source"] == "local"
        assert row["enabled"] is True

        # Secrets must never be required in the zip — empty secret_keys is fine.
        assert "secret_keys" in row

        # Zip-slip: traversal entries are dropped, nothing lands outside the plugin dir.
        hostile = import_plugin_from_bytes(_zip_hostile(), source="local", replace=True)
        assert hostile.get("ok"), hostile
        root = Path(tmp) / "UEFN-Ducky" / "uefn_plugins"
        assert (root / "hostile" / "plugin.json").is_file()
        for escapee in ("escape.txt", "escape2.txt", "escape3.txt", "abs.txt"):
            assert not (root / escapee).exists(), escapee
            assert not (root.parent / escapee).exists(), escapee
            assert not (root / "hostile" / escapee).exists(), escapee

        # Zip bomb: refuse when declared uncompressed size exceeds the cap.
        import backend.uefn_plugins.store as store_mod

        saved_cap = store_mod.MAX_PLUGIN_UNCOMPRESSED_BYTES
        store_mod.MAX_PLUGIN_UNCOMPRESSED_BYTES = 1024
        try:
            big = io.BytesIO()
            with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("plugin.json", json.dumps({"id": "bomb", "kind": "plugin", "version": 1}))
                zf.writestr("payload.bin", b"\0" * 4096)
            bomb = import_plugin_from_bytes(big.getvalue(), source="local", replace=True)
            assert not bomb.get("ok"), bomb
            assert "expands" in str(bomb.get("error") or ""), bomb
            assert not (root / "bomb").exists()
        finally:
            store_mod.MAX_PLUGIN_UNCOMPRESSED_BYTES = saved_cap

        # Corrupt deflate payload: refuse before writing a half-installed folder.
        corrupt_buf = io.BytesIO()
        with zipfile.ZipFile(corrupt_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("plugin.json", json.dumps({"id": "corrupt", "kind": "plugin", "version": 1}))
            zf.writestr("ui/index.html", "<html>ok</html>")
        corrupt_bytes = bytearray(corrupt_buf.getvalue())
        # Flip a byte inside the compressed stream (past the local file header).
        mid = len(corrupt_bytes) // 2
        corrupt_bytes[mid] ^= 0xFF
        bad = import_plugin_from_bytes(bytes(corrupt_bytes), source="local", replace=True)
        assert not bad.get("ok"), bad
        assert "Corrupt" in str(bad.get("error") or "") or "Invalid" in str(bad.get("error") or ""), bad
        assert not (root / "corrupt").exists()

        # Bad CRC: refuse install and never create the plugin folder.
        bad_buf = io.BytesIO()
        with zipfile.ZipFile(bad_buf, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(
                "plugin.json",
                json.dumps({"id": "badcrc", "kind": "plugin", "version": 1, "label": "Bad"}),
            )
            zf.writestr("ui/planets.js", b"ORIGINAL_PAYLOAD_XYZ")
        bad_raw = bytearray(bad_buf.getvalue())
        needle = b"ORIGINAL_PAYLOAD_XYZ"
        idx = bad_raw.find(needle)
        assert idx >= 0, "stored payload not found in zip bytes"
        bad_raw[idx] ^= 0xFF
        bad_install = import_plugin_from_bytes(bytes(bad_raw), source="local", replace=True)
        assert not bad_install.get("ok"), bad_install
        err = str(bad_install.get("error") or "").lower()
        assert "crc" in err or "corrupt" in err, bad_install
        assert not (root / "badcrc").exists(), "corrupt zip must not leave an install folder"

        from backend.uefn_plugins.store import (
            get_uefn_plugin_secret_labels,
            secret_key_label,
        )

        assert secret_key_label("duckyos_account") == "DuckyOS sign-in / device key"
        assert secret_key_label("api_key") == "Api key"

        # Install a plugin with secrets, then erase on uninstall.
        secret_zip = import_plugin_from_bytes(
            _zip_plugin_with_secrets("wipe-me", ["discord", "discord_guild"]),
            source="local",
            replace=True,
        )
        assert secret_zip.get("ok"), secret_zip
        labels = get_uefn_plugin_secret_labels("wipe-me")
        assert labels["labels"] == ["Discord bot token", "Discord server id"]

        from backend.agent import secrets as secrets_mod

        secrets_mod.set_key("discord", "tok")
        secrets_mod.set_key("discord_guild", "gid")
        erased = uninstall_uefn_plugin("wipe-me", erase_data=True)
        assert erased.get("ok"), erased
        assert erased.get("erase_data") is True
        assert secrets_mod.get_key("discord") is None
        assert secrets_mod.get_key("discord_guild") is None

        uninstall_uefn_plugin("demo")
        assert not is_plugin_installed("demo")
        back = import_plugin_from_bytes(_zip_plugin("demo", 3), source="local", replace=True)
        assert back.get("ok"), back

        # api.tool() hook: register without listing tool names in the manifest.
        from backend.server import mcp as shared_mcp
        from backend.uefn_plugins.host import (
            _PluginApi,
            count_uefn_plugin_tools,
            plugin_tool_names,
            set_active_uefn_agent_plugin_ids,
            uefn_agent_tools_allowed,
            uefn_plugin_tool_group_rows,
        )

        # api.register_secret_test → Settings Test button backend.
        from backend.uefn_plugins.host import run_secret_test

        class _FakeSecretApi:
            def __init__(self) -> None:
                self.plugin_id = "wipe-me"

            def register_secret_test(self, secret_key: str, test_fn) -> None:
                from backend.uefn_plugins.host import register_secret_tester

                register_secret_tester(self.plugin_id, secret_key, test_fn)

        # Re-install wipe-me just for the tester registry check (already uninstalled above).
        assert import_plugin_from_bytes(
            _zip_plugin_with_secrets("wipe-me", ["meshy_api_key"]),
            source="local",
            replace=True,
        ).get("ok")
        set_uefn_plugin_enabled("wipe-me", True)
        _FakeSecretApi().register_secret_test(
            "meshy_api_key",
            lambda key: {"ok": key.startswith("msy_"), "detail": "ok" if key.startswith("msy_") else "bad"},
        )
        assert run_secret_test("wipe-me", "meshy_api_key", "msy_abc")["ok"] is True
        assert run_secret_test("wipe-me", "meshy_api_key", "bad")["ok"] is False
        uninstall_uefn_plugin("wipe-me", erase_data=True)

        hook_install = import_plugin_from_bytes(
            _zip_plugin_with_api_tools("hookprobe"), source="local", replace=True
        )
        assert hook_install.get("ok"), hook_install
        hook_en = set_uefn_plugin_enabled("hookprobe", True, trust_local=True)
        assert hook_en.get("ok"), hook_en
        reload_plugins()

        assert "hook_probe_ping" in plugin_tool_names()
        assert "hook_probe_named" in plugin_tool_names()
        assert shared_mcp._tool_manager.get_tool("hook_probe_ping") is not None
        assert count_uefn_plugin_tools("hookprobe") >= 2

        tab_rows = uefn_plugin_tool_group_rows()
        assert any(r["id"] == "hookprobe" and r["kind"] == "uefn_plugin" for r in tab_rows), tab_rows
        hook_row = next(r for r in tab_rows if r["id"] == "hookprobe")
        assert hook_row["enabled"] is True
        assert "hook_probe_ping" in (hook_row.get("tool_names") or [])

        set_active_uefn_agent_plugin_ids(None)
        assert uefn_agent_tools_allowed("hookprobe")
        ping_tool = shared_mcp._tool_manager.get_tool("hook_probe_ping")
        assert ping_tool is not None
        assert ping_tool.fn() == "pong"

        set_uefn_plugin_enabled("hookprobe", False)
        set_active_uefn_agent_plugin_ids(None)
        assert not uefn_agent_tools_allowed("hookprobe")
        try:
            ping_tool.fn()
            raise AssertionError("disabled plugin tool should raise")
        except ValueError as exc:
            assert "disabled" in str(exc).lower()

        # Name collision: skip registration, do not crash.
        set_uefn_plugin_enabled("hookprobe", True, trust_local=True)
        reload_plugins()
        api = _PluginApi("hookprobe")

        def steal_ping():
            return "stolen"

        api.tool(name="hook_probe_ping")(steal_ping)
        still = shared_mcp._tool_manager.get_tool("hook_probe_ping")
        assert still is not None and still.fn() == "pong"

        uninstall_uefn_plugin("hookprobe")

        # Plugin-bundled skills: install → discover → uninstall removes from pack list.
        from backend.skill import list_pack_ids, load_pack_manifest, plugin_owner_for_skill
        from backend.uefn_plugins.store import list_plugin_owned_skills

        sk_install = import_plugin_from_bytes(_zip_plugin_with_skills("skillpack"), source="local", replace=True)
        assert sk_install.get("ok"), sk_install
        assert "demo-tips" in (sk_install.get("skills") or [])
        # Installed-but-disabled: owned on disk, not available to agents yet.
        assert "demo-tips" not in list_pack_ids()
        assert plugin_owner_for_skill("demo-tips") == "skillpack"
        assert set_uefn_plugin_enabled("skillpack", True, trust_local=True).get("ok")
        assert "demo-tips" in list_pack_ids()
        man = load_pack_manifest("demo-tips")
        assert man and man.get("kind") == "plugin"
        assert man.get("source_plugin_id") == "skillpack"
        assert man.get("license") == "All Rights Reserved"
        assert man.get("allow_redistribute") is False
        from backend.skill import export_skill_pack_to_zip

        try:
            export_skill_pack_to_zip("demo-tips", Path(tmp) / "stolen.ducky-skill-pack")
            raise AssertionError("plugin-owned skill export should fail")
        except PermissionError as exc:
            assert "plugin-owned" in str(exc).lower()
        owned = {e["pack_id"]: e["plugin_id"] for e in list_plugin_owned_skills()}
        assert owned.get("demo-tips") == "skillpack"
        set_uefn_plugin_enabled("skillpack", False)
        assert "demo-tips" not in list_pack_ids()
        assert set_uefn_plugin_enabled("skillpack", True, trust_local=True).get("ok")

        # Collision: second plugin cannot claim the same skill id.
        clash = import_plugin_from_bytes(
            _zip_plugin_with_skills("other", skill_id="demo-tips"),
            source="local",
            replace=True,
        )
        assert not clash.get("ok"), clash
        assert "already owned" in str(clash.get("error") or "").lower()

        # Path escape / bad folder rejected.
        bad_skill = import_plugin_from_bytes(_zip_plugin_bad_skill_escape(), source="local", replace=True)
        assert not bad_skill.get("ok"), bad_skill

        uninstall_uefn_plugin("skillpack")
        assert "demo-tips" not in list_pack_ids()
        assert plugin_owner_for_skill("demo-tips") is None

        # --- Plugin TTS: static voices + synthesizer + dynamic voice lister ---
        from backend.uefn_plugins.host import get_tts_synthesizer, get_tts_voices_lister
        from frontend.ui_web.plugin_host_api import _tts_voices_work, _tts_work

        tts_install = import_plugin_from_bytes(_zip_tts_plugin("voxy"), source="local", replace=True)
        assert tts_install.get("ok"), tts_install
        assert set_uefn_plugin_enabled("voxy", True, trust_local=True).get("ok")
        reload_plugins()
        contrib2 = get_contributions()
        assert any(
            v.get("id") == "premade1" and v.get("plugin_id") == "voxy" for v in contrib2["tts_voices"]
        ), contrib2["tts_voices"]
        assert "voxy" in contrib2["tts_voice_plugins"], contrib2["tts_voice_plugins"]
        assert callable(get_tts_synthesizer("voxy"))
        assert callable(get_tts_voices_lister("voxy"))

        # Worker paths always return a dict (never raise across the bridge).
        synth_out = _tts_work("voxy", "hello", "premade1")
        assert synth_out.get("ok") and synth_out.get("audio_base64") == "QUJD", synth_out
        voices_out = _tts_voices_work("voxy")
        assert voices_out.get("ok") and any(v["id"] == "dynamic1" for v in voices_out["voices"]), voices_out

        # Reload (e.g. toggling any other plugin) must NOT drop a registered synthesizer.
        reload_plugins()
        assert callable(get_tts_synthesizer("voxy")), "synthesizer lost across reload"
        assert callable(get_tts_voices_lister("voxy")), "voice lister lost across reload"
        assert "voxy" in get_contributions()["tts_voice_plugins"]

        # A disabled plugin's worker refuses without invoking the synthesizer, and it
        # drops out of tts_voice_plugins so the picker never polls it.
        set_uefn_plugin_enabled("voxy", False)
        assert _tts_work("voxy", "hi", "premade1").get("ok") is False
        assert _tts_voices_work("voxy").get("ok") is False
        assert "voxy" not in get_contributions()["tts_voice_plugins"]
        uninstall_uefn_plugin("voxy")

        # Store install with default_enabled must land ON (no separate Enable click).
        from frontend.settings import PanelSettings, replace as settings_replace

        settings_replace(PanelSettings.load(), enabled_uefn_plugins=[]).save()
        auto_manifest = {
            "id": "autoon",
            "kind": "plugin",
            "version": 1,
            "label": "Auto On",
            "default_enabled": True,
            "contributes": {},
            "backend": {"entry": "backend", "register": "register"},
        }
        auto_buf = io.BytesIO()
        with zipfile.ZipFile(auto_buf, "w") as zf:
            zf.writestr("plugin.json", json.dumps(auto_manifest))
            zf.writestr("backend/__init__.py", "def register(api):\n    pass\n")
        store_zip = auto_buf.getvalue()
        auto = import_plugin_from_bytes(store_zip, source="store", replace=True)
        assert auto.get("ok"), auto
        assert auto.get("enabled") is True, auto
        assert is_plugin_enabled("autoon"), "store install should auto-enable default_enabled plugins"
        # Update while disabled must NOT force re-enable.
        set_uefn_plugin_enabled("autoon", False)
        assert not is_plugin_enabled("autoon")
        again_auto = import_plugin_from_bytes(store_zip, source="store", replace=True)
        assert again_auto.get("ok"), again_auto
        assert not is_plugin_enabled("autoon"), "update must keep user disable"
        uninstall_uefn_plugin("autoon")

        # Empty enabled_uefn_plugins list must persist (disable-all is not "no overrides").
        empty = settings_replace(PanelSettings.load(), enabled_uefn_plugins=[])
        empty.save()
        reloaded = PanelSettings.load()
        assert reloaded.enabled_uefn_plugins == [], reloaded.enabled_uefn_plugins

        # Colored plugin icons: assets/* → data URL for Settings left rail / header.
        from backend.uefn_plugins.host import _resolve_contrib_icon
        from backend.uefn_plugins.store import plugin_dir

        demo_root = plugin_dir("demo")
        (demo_root / "assets").mkdir(parents=True, exist_ok=True)
        # Minimal 1×1 PNG
        tiny_png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
        (demo_root / "assets" / "icon.png").write_bytes(tiny_png)
        icon_url = _resolve_contrib_icon("assets/icon.png", demo_root)
        assert icon_url and icon_url.startswith("data:image/png;base64,"), icon_url
        # Named flat keys prefer the plugin's color asset when present.
        assert _resolve_contrib_icon("chat", demo_root) == icon_url
        assert _resolve_contrib_icon("user", demo_root) == icon_url
        assert _resolve_contrib_icon("assets/../escape.png", demo_root) is None
        # Emoji stays (intentional), no asset override.
        assert _resolve_contrib_icon("🎨", demo_root) == "🎨"

        # AI-made plugins: trust gate + same-source overwrite guard.
        from backend.uefn_plugins.store import appdata_ai_plugins_dir
        from backend.tools.panel_ai_plugins import (
            install_ai_plugin,
            scaffold_ai_plugin,
            write_ai_plugin_file,
            validate_ai_plugin,
            delete_ai_plugin_draft,
            _resolve_jailed,
        )

        assert "ai_plugins" in str(appdata_ai_plugins_dir())
        sc = scaffold_ai_plugin("ai_hello", label="AI Hello")
        assert sc.get("ok"), sc
        try:
            _resolve_jailed(appdata_ai_plugins_dir() / "ai_hello", "../escape.py")
            raise AssertionError("jail should reject ..")
        except ValueError:
            pass
        assert not write_ai_plugin_file("ai_hello", "../x.py", "x=1").get("ok")
        assert write_ai_plugin_file(
            "ai_hello",
            "ui/theme.css",
            ":root{--x:1}\n",
        ).get("ok")
        assert validate_ai_plugin("ai_hello").get("ok")
        ai_inst = install_ai_plugin("ai_hello")
        assert ai_inst.get("ok"), ai_inst
        assert ai_inst.get("source") == "ai"
        assert not is_plugin_enabled("ai_hello"), "AI install must not auto-enable"
        ai_trust = set_uefn_plugin_enabled("ai_hello", True, trust_local=False)
        assert ai_trust.get("needs_trust"), ai_trust
        assert ai_trust.get("source") == "ai"
        assert set_uefn_plugin_enabled("ai_hello", True, trust_local=True).get("ok")
        assert is_plugin_enabled("ai_hello")
        # Store cannot overwrite AI; AI cannot overwrite local demo.
        blocked_ai = import_plugin_from_bytes(_zip_plugin("ai_hello", 2), source="store", replace=True)
        assert not blocked_ai.get("ok"), blocked_ai
        blocked_local = import_plugin_from_bytes(_zip_plugin("demo", 9), source="ai", replace=True)
        assert not blocked_local.get("ok"), blocked_local
        # Same-source replace OK.
        assert import_plugin_from_bytes(
            _zip_plugin("ai_hello", 2), source="ai", replace=True
        ).get("ok")
        uninstall_uefn_plugin("ai_hello")
        assert delete_ai_plugin_draft("ai_hello", confirm=True).get("ok")

        print("uefn_plugins self-check OK")
        print("appdata", Path(tmp) / "UEFN-Ducky" / "uefn_plugins")


if __name__ == "__main__":
    main()
