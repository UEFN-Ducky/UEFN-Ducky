"""OpenCode IDE hookup: ~/.config/opencode/opencode.json gets mcp.uefn, nothing else changes."""

from __future__ import annotations

import json

import pytest

from frontend.ide_paths import IdeKind, opencode_config_path, path_for_ide
from frontend.opencode_mcp import merge_opencode_config, opencode_block_matches

BLOCK = {"command": "C:/Ducky/UEFN-Ducky.exe", "args": ["bridge", "--port", "4200"], "env": {"UEFN_DUCKY_PORT": "4200"}}


def test_path_follows_opencode_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert path_for_ide(IdeKind.OPENCODE) == tmp_path / ".config" / "opencode" / "opencode.json"
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path / "custom"))
    assert opencode_config_path() == tmp_path / "custom" / "opencode.json"


def test_merge_keeps_user_keys_and_is_idempotent(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text(json.dumps({"theme": "x", "mcp": {"mine": {"type": "remote", "url": "https://m"}}}))
    assert merge_opencode_config(cfg, BLOCK) is True
    data = json.loads(cfg.read_text())
    assert data["theme"] == "x" and data["mcp"]["mine"] == {"type": "remote", "url": "https://m"}
    assert data["mcp"]["uefn"] == {"type": "local", "command": ["C:/Ducky/UEFN-Ducky.exe", "bridge", "--port", "4200"],
                                   "environment": {"UEFN_DUCKY_PORT": "4200"}, "enabled": True}
    assert merge_opencode_config(cfg, BLOCK) is False
    assert opencode_block_matches(cfg, BLOCK) == (True, "OK")


def test_new_file_and_stale_bridge(tmp_path):
    cfg = tmp_path / "sub" / "opencode.json"
    assert opencode_block_matches(cfg, BLOCK)[0] is False
    merge_opencode_config(cfg, BLOCK)
    assert json.loads(cfg.read_text())["$schema"] == "https://opencode.ai/config.json"
    moved = {**BLOCK, "command": "D:/New/UEFN-Ducky.exe"}
    assert opencode_block_matches(cfg, moved) == (False, "Out of date — click Apply")


def test_jsonc_is_never_rewritten(tmp_path):
    cfg = tmp_path / "opencode.json"
    original = '{\n  // my comment\n  "theme": "x"\n}\n'
    cfg.write_text(original)
    with pytest.raises(ValueError, match="comments"):
        merge_opencode_config(cfg, BLOCK)
    assert cfg.read_text() == original


def test_apply_and_verify_route_opencode(monkeypatch, tmp_path):
    from frontend import ide_apply

    cfg = tmp_path / "opencode.json"
    monkeypatch.setattr(ide_apply, "path_for_ide", lambda kind, override="": cfg)
    monkeypatch.setattr(ide_apply, "build_uefn_server_block", lambda s: BLOCK)
    monkeypatch.setattr(ide_apply, "sync_skill_for_ide", lambda path: [])
    settings = type("S", (), {"antigravity_config_path": ""})()
    assert ide_apply.apply_ide_bridge(IdeKind.OPENCODE, settings) == str(cfg)
    assert ide_apply.verify_ide_bridge(IdeKind.OPENCODE, settings) == {"ok": True, "detail": "OK"}
    assert IdeKind.OPENCODE not in ide_apply.ALL_IDES  # only when the OpenCode gateway hooks it up
