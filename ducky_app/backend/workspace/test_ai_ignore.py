"""Security regressions use synthetic secrets; never read the user's .env."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from backend.workspace import ai_ignore as guard, identity
from frontend.settings import PanelSettings


@pytest.fixture
def protected(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(root))
    monkeypatch.delenv("UEFN_VSCODE_WORKSPACE_FOLDERS", raising=False)
    s = PanelSettings(uefn_project_root=str(root), ai_ignore_patterns=["secrets/", "config/private.json", "*.key"],
                      ai_ignore_strict=True)
    s.save()
    (root / ".env").write_text("SYNTHETIC_SECRET")
    (root / ".env.production").write_text("SYNTHETIC_SECRET")
    (root / "public.txt").write_text("public text")
    (root / "secrets").mkdir()
    (root / "secrets" / "hidden.txt").write_text("SYNTHETIC_SECRET")
    (root / "config").mkdir()
    (root / "config" / "private.json").write_text("SYNTHETIC_SECRET")
    return root


@pytest.mark.parametrize("name", [".env", ".ENV", ".env.production", "sub/.env", "secrets/hidden.txt",
                                  "config/private.json", "sub/token.key", ".env::$DATA"])
def test_path_rules(protected, name):
    with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
        guard.require_ai_access(str(protected / name))


def test_rules_reject_negation_and_parent_traversal():
    for value in ("*.key", ["!secrets"], ["../secret"], [123], ["x\ny"]):
        with pytest.raises(ValueError):
            guard.normalize_rules(value)
    assert guard.normalize_rules([" secrets/ ", "# comment", "", "secrets/"]) == ["secrets/"]


def test_patterns_are_root_relative_and_absolute(tmp_path):
    root = tmp_path / "p"
    policy = guard.IgnorePolicy(("config/private.json", str(root / "named.txt")), (str(root),))
    assert policy.denied(str(root / "config/private.json"))
    assert policy.denied(str(root / "named.txt"))
    assert not policy.denied(str(root / "config/public.json"))
    assert not policy.denied(str(root / "other/config/private.json"))


def test_symlink_and_hardlink_aliases(protected):
    alias = protected / "alias.txt"
    try:
        alias.symlink_to(protected / ".env")
    except OSError:
        pass  # Windows may require symlink privilege; hard links do not.
    else:
        assert guard.current_policy().denied(str(alias))
        alias.unlink()
    os.link(protected / ".env", alias)
    assert guard.current_policy().denied(str(alias))


def test_reads_searches_and_listings_never_return_secrets(protected):
    from backend.tools.core import system, workspace_code as wc
    for fn in (system.workspace_read_file, wc.workspace_file_outline):
        with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
            fn(path=".env")
    rows = json.loads(wc.workspace_read_files([".env", "public.txt"]))
    assert "error" in rows["files"][0]
    assert rows["files"][1]["content"] == "public text"
    for output in (system.workspace_list_dir(), wc.workspace_tree(), wc.workspace_find("*"),
                   wc.workspace_search("SYNTHETIC_SECRET")):
        assert "SYNTHETIC_SECRET" not in output
        assert ".env" not in output
        assert "hidden.txt" not in output
    with pytest.raises(ValueError, match="AI_FILE_PROTECTION"):
        wc.workspace_git("show", ["HEAD:.env"])


def test_write_delete_and_both_move_ends_are_guarded(protected, monkeypatch):
    from backend.tools.core import system, workspace_code as wc
    from backend.workspace import runtime
    from backend.workspace.writer import ProjectWriter
    monkeypatch.setattr("backend.workspace.writer._is_folder_project_root", lambda _: True)
    runtime.reset_for_tests(ProjectWriter.for_root(str(protected)))
    try:
        for fn in (
            lambda: system.workspace_write_file(".env", "overwrite"),
            lambda: wc.workspace_edit_file(".env", "SYNTHETIC_SECRET", "overwrite"),
            lambda: wc.workspace_delete_file(".env"),
            lambda: wc.workspace_move_file(".env", "public-copy.txt"),
            lambda: wc.workspace_move_file("public.txt", ".env"),
        ):
            with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
                fn()
        assert (protected / ".env").read_text() == "SYNTHETIC_SECRET"
        assert (protected / "public.txt").exists()
    finally:
        runtime.reset_for_tests(None)


def test_writer_guards_custom_resolvers_but_allows_human(protected, monkeypatch):
    from backend.workspace.writer import ProjectWriter
    monkeypatch.setattr("backend.workspace.writer._is_folder_project_root", lambda _: True)
    writer = ProjectWriter.for_root(str(protected))
    token = identity.bind(identity.RunContext(run_id="test"))
    try:
        with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
            writer.write_text(".env", "agent")
    finally:
        identity.reset(token)
    token = identity.bind(identity.RunContext(run_id="human", source="user"))
    try:
        writer.write_text(".env", "human", tool="save_project_file")
    finally:
        identity.reset(token)
    assert (protected / ".env").read_text() == "human"


@pytest.mark.parametrize("name", ["execute_python", "listener_command", "ducky_call_tool",
                                  "unreal__call_tool", "browser_evaluate", "plugin__read",
                                  "ducky_settings_set", "ducky_ui_show", "run_workflow"])
def test_dispatch_denies_unaudited_tools(protected, name):
    with pytest.raises(ValueError, match="AI_FILE_PROTECTION"):
        guard.require_safe_tool(name)
    from backend.server import mcp
    with pytest.raises(ValueError, match="AI_FILE_PROTECTION"):
        asyncio.run(mcp.call_tool(name, {}))
    from backend.agent.tools import execute_tool
    result = asyncio.run(execute_tool(name, {}))
    assert not result.ok and "AI_FILE_PROTECTION" in result.error


def test_mcp_protocol_handler_cannot_bypass_gate(protected, monkeypatch):
    # This protocol test is headless; do not inherit the launching chat identity.
    monkeypatch.setattr(identity, "resolve_context", lambda: None)
    from backend.server import ProtectedFastMCP
    from mcp.types import CallToolRequest, CallToolRequestParams
    server = ProtectedFastMCP("protection-test")
    executed = []

    @server.tool(name="execute_python")
    def unsafe_script() -> str:
        executed.append(True)
        return "SYNTHETIC_SECRET"

    @server.tool(name="workspace_read_file")
    def safe_read() -> str:
        return "public"

    handler = server._mcp_server.request_handlers[CallToolRequest]
    async def check():
        result = await handler(CallToolRequest(
            method="tools/call", params=CallToolRequestParams(name="execute_python", arguments={})))
        assert result.root.isError
        assert "AI_FILE_PROTECTION" in str(result)
        result = await handler(CallToolRequest(
            method="tools/call", params=CallToolRequestParams(name="workspace_read_file", arguments={})))
        assert not result.root.isError and "public" in str(result)
    asyncio.run(check())
    assert not executed


def test_agent_cannot_change_ignore_settings(protected):
    from backend.tools.panel.panel_settings import apply_settings_patch
    assert "not settable" in apply_settings_patch({"ai_ignore_patterns": []})["error"]
    assert "not settable" in apply_settings_patch({"ai_ignore_strict": False})["error"]


def test_attachment_filter(protected):
    from backend.agent.attachments import parse_attachment_dict
    raw = {"kind": "file", "name": ".env", "text": "SYNTHETIC_SECRET"}
    with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
        parse_attachment_dict(raw, current=True)
    assert parse_attachment_dict(raw) is None
    assert parse_attachment_dict({"kind": "file", "name": "public.txt", "text": "public"})


def test_hydration_checks_before_reading(protected):
    from frontend.ui_web.conversation_attachments import hydrate_attachment_dict
    conversations = protected / "chats"
    folder = conversations / "c"
    folder.mkdir(parents=True)
    (folder / ".env").write_text("SYNTHETIC_SECRET")
    result = hydrate_attachment_dict({"kind": "file", "name": ".env", "path": ".env"}, "c", conversations)
    assert result["kind"] == "ignored" and "SYNTHETIC_SECRET" not in str(result)


def test_settings_persist_across_loads(protected):
    s = PanelSettings.load()
    assert s.ai_ignore_patterns == ["secrets/", "config/private.json", "*.key"]
    assert s.ai_ignore_strict
    s.ai_ignore_strict = False
    s.save()
    assert PanelSettings.load().ai_ignore_strict is False
    guard.require_safe_tool("plugin__read")  # Weaker mode is explicit and does not claim isolation.
    with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
        guard.require_ai_access(str(protected / ".env"))


@pytest.mark.parametrize("backend", ["files", "db"])
def test_strict_protection_is_opt_in(monkeypatch, backend):
    monkeypatch.setenv("DUCKY_STORE_BACKEND", backend)
    assert PanelSettings().ai_ignore_strict is False
    assert PanelSettings.load(fail_closed=True).ai_ignore_strict is False
    assert guard.current_policy().strict is False
    guard.require_safe_tool("unreal__call_tool")


@pytest.mark.parametrize("backend", ["files", "db"])
@pytest.mark.parametrize("stored_strict", [None, False, True])
def test_legacy_protection_choice_survives_upgrade(monkeypatch, backend, stored_strict):
    from frontend.settings import default_app_data_dir
    monkeypatch.setenv("DUCKY_STORE_BACKEND", backend)
    data = {"ai_ignore_patterns": ["secrets/"]}
    if stored_strict is not None:
        data["ai_ignore_strict"] = stored_strict
    settings_dir = default_app_data_dir()
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "panel_settings.json").write_text(json.dumps(data))
    settings = PanelSettings.load(fail_closed=True)
    assert settings.ai_ignore_strict is (stored_strict is True)
    assert settings.ai_ignore_patterns == ["secrets/"]
    settings.save()
    assert PanelSettings.load(fail_closed=True).ai_ignore_strict is (stored_strict is True)
    with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
        guard.require_ai_access(str(settings_dir / ".env"))
    with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
        guard.require_ai_access(str(settings_dir / "secrets" / "hidden.txt"))


@pytest.mark.parametrize("backend", ["files", "db"])
def test_strict_protection_persists_as_only_override(monkeypatch, backend):
    monkeypatch.setenv("DUCKY_STORE_BACKEND", backend)
    PanelSettings(ai_ignore_strict=True).save()
    assert PanelSettings.load(fail_closed=True).ai_ignore_strict is True
    with pytest.raises(ValueError, match="AI_FILE_PROTECTION"):
        guard.require_safe_tool("unreal__call_tool")


def test_external_agent_can_run_without_strict_protection(monkeypatch):
    from types import SimpleNamespace
    from backend.agent.coding_agents import runner
    from frontend.ui_web.live_agent_runs import get_live_run_ids
    result = {"ok": True, "reply": "completed"}
    seen = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs["run_id"])
        assert kwargs["run_id"] in get_live_run_ids()
        return result

    monkeypatch.setattr(runner, "_run_coding_agent_message", fake_run)
    assert runner.run_coding_agent_message(
        SimpleNamespace(id="test", coding_agent="codex"), "hello", model="",
        push=lambda _: None, run_id="default-agent",
    ) == result
    assert seen == ["default-agent"]
    assert "default-agent" not in get_live_run_ids()


def test_policy_errors_fail_closed(protected, monkeypatch):
    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(OSError("broken"))))
    with pytest.raises(ValueError, match="AI_FILE_POLICY_UNAVAILABLE"):
        guard.require_ai_access(str(protected / "public.txt"))


def test_temporary_files_also_respect_ignore_rules(protected, monkeypatch):
    from backend.workspace.writer import ProjectWriter
    monkeypatch.setattr("backend.workspace.writer._is_folder_project_root", lambda _: True)
    settings = PanelSettings.load()
    settings.ai_ignore_patterns = ["*.tmp"]
    settings.save()
    writer = ProjectWriter.for_root(str(protected))
    with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
        writer.write_text("public.txt", "changed", tool="workspace_write_file")
    assert (protected / "public.txt").read_text() == "public text"
    assert not list(protected.glob("*.tmp"))


@pytest.mark.parametrize("bad", [None, "private.txt", [123]])
def test_invalid_stored_policy_fails_closed(protected, monkeypatch, bad):
    from frontend.settings import default_app_data_dir
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    (default_app_data_dir() / "panel_settings.json").write_text(json.dumps({"ai_ignore_patterns": bad}))
    with pytest.raises(ValueError, match="AI_FILE_POLICY_UNAVAILABLE"):
        guard.require_ai_access(str(protected / "public.txt"))


def test_corrupt_file_policy_fails_closed(protected, monkeypatch):
    from frontend.settings import default_app_data_dir
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    (default_app_data_dir() / "panel_settings.json").write_text("{broken")
    with pytest.raises(ValueError, match="AI_FILE_POLICY_UNAVAILABLE"):
        guard.require_ai_access(str(protected / "public.txt"))


def test_whole_folder_mutation_denied_before_changes(protected):
    from backend.tools.core import workspace_code as wc
    nested = protected / "ordinary"
    nested.mkdir()
    (nested / ".env").write_text("SYNTHETIC_SECRET")
    for fn in (lambda: wc.workspace_move_file("ordinary", "renamed"),
               lambda: wc.workspace_delete_file("ordinary")):
        with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
            fn()
    assert (nested / ".env").exists()
    assert not (protected / "renamed").exists()


def test_destination_descendant_rule_denied(protected):
    (protected / "ordinary").mkdir()
    (protected / "ordinary" / "private.json").write_text("public")
    with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
        guard.require_ai_path_operation(str(protected / "ordinary"), str(protected / "config"))


def test_user_save_blocks_active_agents_and_preserves_rules(protected):
    from frontend.ui_web.live_agent_runs import add_live_run_id, discard_live_run_id
    add_live_run_id("test")
    try:
        with pytest.raises(ValueError, match="Stop active agents"):
            guard.save_user_policy({"ai_ignore_patterns": ["new.txt"]})
        assert "secrets/" in PanelSettings.load().ai_ignore_patterns
    finally:
        discard_live_run_id("test")
    assert guard.save_user_policy({"ai_ignore_patterns": ["new.txt"]}).startswith("Saved")
    assert PanelSettings.load().ai_ignore_patterns == ["new.txt"]


def test_external_agent_never_reaches_adapter(protected, monkeypatch):
    from types import SimpleNamespace
    from backend.agent.coding_agents import runner
    monkeypatch.setattr(runner, "get_adapter", lambda _: pytest.fail("adapter must not be touched"))
    events = []
    from frontend.ui_web.live_agent_runs import add_live_run_id
    add_live_run_id("blocked")  # The panel registers a run before starting its worker.
    result = runner.run_coding_agent_message(
        SimpleNamespace(id="test", coding_agent="codex"), "hello", model="",
        push=events.append, run_id="blocked",
    )
    assert not result["ok"] and "AI_FILE_PROTECTION" in result["error"]
    assert "Settings > General > Permissions and rules" in result["error"]
    assert "Turn off Strict protection" in result["error"]
    assert events[0]["type"] == "error"
    assert events[1]["type"] == "agent_stopped"
    assert events[1]["run_id"] == result["run_id"]
    from frontend.ui_web.live_agent_runs import get_live_run_ids
    assert result["run_id"] not in get_live_run_ids()
    assert guard.save_user_policy({"ai_ignore_strict": False}).startswith("Saved")


def test_human_can_disable_strict_during_a_live_run_without_removing_file_rules(protected):
    from frontend.ui_web.live_agent_runs import add_live_run_id, discard_live_run_id
    add_live_run_id("other-agent")
    try:
        assert guard.save_user_policy({"ai_ignore_strict": False}).startswith("Saved")
        assert guard.current_policy().strict is False
        with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
            guard.require_ai_access(str(protected / ".env"))
        with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
            guard.require_ai_access(str(protected / "secrets" / "hidden.txt"))
        with pytest.raises(ValueError, match="Stop active agents"):
            guard.save_user_policy({"ai_ignore_strict": True})
    finally:
        discard_live_run_id("other-agent")


def test_strict_tools_are_available_but_plugins_are_not_discovered(protected, monkeypatch):
    from backend.agent import tools
    from backend.mcp_plugins import client_pool
    monkeypatch.setattr(client_pool, "get_plugin_pool", lambda: pytest.fail("plugin discovery"))
    available = asyncio.run(tools.list_mcp_tools())
    assert "workspace_read_file" in {t.name for t in available}
    assert all(t.name in guard.SAFE_TOOLS for t in available)


def test_installed_enforcement_cannot_be_modified(protected):
    with pytest.raises(ValueError, match="installed enforcement"):
        guard.require_ai_mutation(guard.__file__)


@pytest.mark.parametrize("rule", ["clip.mp4", "*.jpg", "*.part.jpg"])
def test_media_extraction_denied_before_subprocess(protected, monkeypatch, rule):
    from backend.agent.video import frames

    settings = PanelSettings.load()
    settings.ai_ignore_patterns = [rule]
    settings.save()
    video = protected / "clip.mp4"
    video.write_bytes(b"synthetic video")
    monkeypatch.setattr(frames, "ensure_installed", lambda: pytest.fail("ffmpeg must not start"))
    with pytest.raises(frames.VideoError, match="AI_FILE_IGNORED"):
        frames.extract_frames(video, 2)
    assert list(protected.glob("*.jpg")) == []


@pytest.mark.parametrize("rule", ["clip.mp4", "*.transcript.txt", "*.part", "*.audio.mp3"])
def test_transcript_denial_precedes_cached_read_and_cleanup(protected, monkeypatch, rule):
    from backend.agent.video import audio

    settings = PanelSettings.load()
    settings.ai_ignore_patterns = [rule]
    settings.save()
    video = protected / "clip.mp4"
    video.write_bytes(b"synthetic video")
    text, _ = audio.transcript_paths(video)
    text.write_text("SYNTHETIC_SECRET")
    mp3 = video.with_name(video.name + ".audio.mp3")
    mp3.write_bytes(b"keep existing file")
    monkeypatch.setattr(audio, "_read", lambda _: pytest.fail("cache must not be read"))
    result = audio.transcribe_video(video)
    assert result.text == "" and "AI_FILE_IGNORED" in result.note
    assert mp3.read_bytes() == b"keep existing file"


def test_staged_video_not_touched_when_ignored(protected, monkeypatch):
    from backend.agent.video import staging

    settings = PanelSettings.load()
    settings.ai_ignore_patterns = ["*.mp4"]
    settings.save()
    sid = "a" * 32 + ".mp4"
    (protected / sid).write_bytes(b"synthetic video")
    monkeypatch.setattr(staging, "staging_dir", lambda **kwargs: protected)
    monkeypatch.setattr(staging.os, "utime", lambda _: pytest.fail("ignored timestamp must not change"))
    with pytest.raises(staging.VideoError, match="AI_FILE_IGNORED"):
        staging.resolve_staged(sid)


def test_history_media_uses_original_name_before_renamed_cache(protected, monkeypatch):
    from backend.agent.video import send

    settings = PanelSettings.load()
    settings.ai_ignore_patterns = ["private.mp4"]
    settings.save()
    row = {"kind": "video", "name": "private.mp4", "mime": "video/mp4",
           "path": "attachments/123_private.mp4", "size_bytes": 1,
           "frames": [{"path": "attachments/cached.jpg"}]}
    monkeypatch.setattr(send, "transcribe_video", lambda _: pytest.fail("ignored history must not be transcribed"))
    monkeypatch.setattr(send, "extract_frames", lambda *args: pytest.fail("ignored history must not be extracted"))
    assert not send.backfill_history_frames([{"role": "user", "attachments": [row]}],
                                           conv_dir=protected, provider="anthropic", external=False)
    with pytest.raises(send.VideoError, match="AI_FILE_IGNORED"):
        send.prepare_video_frames([row], conv_dir=protected, provider="anthropic", external=False)


def test_attachment_persistence_cannot_rename_ignored_input(protected):
    from backend.agent.message_attachment import MessageAttachment
    from frontend.ui_web.conversation_attachments import persist_message_attachments

    attachment = MessageAttachment(kind="file", name=".env", text="SYNTHETIC_SECRET")
    with pytest.raises(ValueError, match="AI_FILE_IGNORED"):
        persist_message_attachments("c", 1.0, [attachment], protected / "chats")
    assert not list((protected / "chats").rglob("*_env"))
