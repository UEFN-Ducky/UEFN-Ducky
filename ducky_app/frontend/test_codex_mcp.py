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
