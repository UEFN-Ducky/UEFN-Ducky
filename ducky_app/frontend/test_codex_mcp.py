"""Codex global MCP is TOML, and a per-chat run id still counts as current."""

from __future__ import annotations

from frontend.codex_mcp import codex_block_matches, merge_codex_config


def _block() -> dict:
    return {
        "command": r"C:\UEFN-Ducky-Bridge.exe",
        "args": ["bridge", "--port", "9876"],
        "env": {"UEFN_DUCKY_PORT": "9876", "SystemRoot": r"C:\Windows"},
    }


def test_merge_keeps_other_codex_tables_and_is_idempotent(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mcp_servers.node_repl]\ncommand = "x"\n', encoding="utf-8")
    assert merge_codex_config(cfg, _block()) is True
    text = cfg.read_text(encoding="utf-8")
    assert "mcp_servers.node_repl" in text
    assert text.count("[mcp_servers.uefn]") == 1
    assert text.count("BEGIN UEFN-DUCKY") == 1
    assert merge_codex_config(cfg, _block()) is False
    ok, detail = codex_block_matches(cfg, _block())
    assert ok and detail == "Up to date"


def test_session_stamp_still_matches_and_stale_command_does_not(tmp_path):
    cfg = tmp_path / "config.toml"
    merge_codex_config(cfg, _block())
    text = cfg.read_text(encoding="utf-8")
    text = text.replace(
        'args = ["bridge", "--port", "9876"]',
        'args = ["bridge", "--port", "9876", "--ducky-run-id", "abc"]',
    ).replace(
        'SystemRoot = "C:\\\\Windows"',
        'SystemRoot = "C:\\\\Windows"\nDUCKY_RUN_ID = "abc"',
    )
    cfg.write_text(text, encoding="utf-8")
    ok, _detail = codex_block_matches(cfg, _block())
    assert ok
    stale = dict(_block())
    stale["command"] = r"C:\old.exe"
    ok, detail = codex_block_matches(cfg, stale)
    assert not ok and "Out of date" in detail


def test_unmarked_duplicate_table_is_dropped(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[mcp_servers.uefn]\ncommand = "old.exe"\n\n[mcp_servers.keep]\ncommand = "k"\n',
        encoding="utf-8",
    )
    merge_codex_config(cfg, _block())
    text = cfg.read_text(encoding="utf-8")
    assert text.count("[mcp_servers.uefn]") == 1
    assert "old.exe" not in text
    assert "mcp_servers.keep" in text


def test_block_leaves_persons_approval_policy_alone(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'approval_policy = "never"\n\n# BEGIN UEFN-DUCKY\napproval_policy = "on-failure"\n'
        '[mcp_servers.uefn]\ncommand = "x"\n# END UEFN-DUCKY\n',
        encoding="utf-8",
    )
    ok, detail = codex_block_matches(cfg, _block())
    assert not ok and "Out of date" in detail
    merge_codex_config(cfg, _block())
    text = cfg.read_text(encoding="utf-8")
    assert text.count("approval_policy") == 1 and text.startswith('approval_policy = "never"')
    assert codex_block_matches(cfg, _block())[0]


def test_auto_apply_never_hooks_up_codex_unasked(tmp_path, monkeypatch):
    from frontend import ide_apply
    from frontend.ide_paths import IdeKind

    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(ide_apply, "path_for_ide", lambda kind, override="": cfg)
    monkeypatch.setattr(ide_apply, "build_uefn_server_block", lambda s, *a: _block())
    monkeypatch.setattr(ide_apply, "sync_skill_for_ide", lambda path: [])
    settings = type("S", (), {"antigravity_config_path": ""})()
    ide_apply.apply_ide_bridge(IdeKind.CODEX, settings, auto=True)
    assert not cfg.exists()
    cfg.write_text('model = "gpt-5"\n', encoding="utf-8")
    ide_apply.apply_ide_bridge(IdeKind.CODEX, settings, auto=True)
    assert cfg.read_text(encoding="utf-8") == 'model = "gpt-5"\n'
    ide_apply.apply_ide_bridge(IdeKind.CODEX, settings)  # Settings → Apply
    assert "[mcp_servers.uefn]" in cfg.read_text(encoding="utf-8")
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('"bridge"', '"old"'), encoding="utf-8")
    ide_apply.apply_ide_bridge(IdeKind.CODEX, settings, auto=True)  # hooked up: kept current
    assert '"bridge"' in cfg.read_text(encoding="utf-8")


def test_startup_removes_the_block_older_ducky_wrote_unasked(tmp_path, monkeypatch):
    from frontend import ide_apply
    from frontend.ide_paths import IdeKind

    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(ide_apply, "path_for_ide", lambda kind, override="": cfg)
    monkeypatch.setattr(ide_apply, "build_uefn_server_block", lambda s, *a: _block())
    monkeypatch.setattr(ide_apply, "sync_skill_for_ide", lambda path: [])
    settings = type("S", (), {"antigravity_config_path": ""})()
    persons = 'model = "gpt-5"\n\n[mcp_servers.mine]\ncommand = "mine.exe"\n'
    cfg.write_text(
        persons
        + '\n[mcp_servers.uefn]\ncommand = "C:\\\\Ducky\\\\UEFN-Ducky.exe"\n\n'
        + '# BEGIN UEFN-DUCKY\napproval_policy = "on-failure"\n\n[mcp_servers.uefn]\n'
        + 'command = "C:\\\\Ducky\\\\UEFN-Ducky-Bridge.exe"\n# END UEFN-DUCKY\n',
        encoding="utf-8",
    )
    ide_apply.apply_ide_bridge(IdeKind.CODEX, settings, auto=True)
    assert cfg.read_text(encoding="utf-8") == persons
    ide_apply.apply_ide_bridge(IdeKind.CODEX, settings, auto=True)  # nothing left to do
    assert cfg.read_text(encoding="utf-8") == persons


def test_old_question_timeout_requires_config_refresh(tmp_path):
    cfg = tmp_path / "config.toml"
    merge_codex_config(cfg, _block())
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            "tool_timeout_sec = 1000000000000.0", "tool_timeout_sec = 180.0"
        ),
        encoding="utf-8",
    )
    ok, detail = codex_block_matches(cfg, _block())
    assert not ok and "question waits" in detail
    assert merge_codex_config(cfg, _block())
    assert codex_block_matches(cfg, _block())[0]
