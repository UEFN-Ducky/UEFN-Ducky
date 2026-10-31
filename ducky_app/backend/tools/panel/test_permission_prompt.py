"""Approval cards for Claude Code's --permission-prompt-tool."""

from __future__ import annotations

import json

import pytest

from backend.tools.panel import permission_prompt as pp


class _Conv:
    def __init__(self) -> None:
        self.agent_allow_rules: list[str] = []


@pytest.fixture
def conv(monkeypatch):
    c = _Conv()
    saved: list[list[str]] = []
    monkeypatch.setattr(pp, "_load_conv", lambda _cid: c)
    monkeypatch.setattr("frontend.chat_store.save_conversation", lambda cv, *_a, **_k: saved.append(list(cv.agent_allow_rules)))
    c.saved = saved
    return c


def _answer(monkeypatch, selected: list[str], text: str = "") -> list[dict]:
    asked: list[dict] = []

    def fake_ask(questions, title=""):
        asked.append({"questions": questions, "title": title})
        return json.dumps({"ok": True, "answers": {"agent_permission": {"selected": selected, "text": text, "skipped": False}}})

    monkeypatch.setattr("backend.tools.panel.panel_ui.ducky_ask_user", fake_ask)
    return asked


def test_plain_command_can_be_always_allowed(monkeypatch, conv) -> None:
    asked = _answer(monkeypatch, ["always"])
    out = pp.decide("Bash", {"command": "git status"}, conv_id="c1")
    assert out == {"behavior": "allow", "updatedInput": {"command": "git status"}}
    question = asked[0]["questions"][0]
    assert question["detail"] == "git status"
    assert [o["id"] for o in question["options"]] == ["once", "always", "all", "deny"]
    assert pp._rules("c1") == ["Bash:git status"]
    # Remembered: the next git status runs without a card.
    asked.clear()
    assert pp.decide("Bash", {"command": "git status --short"}, conv_id="c1")["behavior"] == "allow"
    assert asked == []


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git push --force",
        "git reset --hard HEAD~1",
        "gh pr create --fill",
        "rm -rf build",
        "py build/build_exes.py",
        "py scripts/release.py --publish",
        "cd repo && git push",
        "git -C C:/repo push origin main",
        'git -C "C:/My Repo" push',
        "git -c core.autocrlf=false reset --hard",
        "git --no-pager -C repo clean -fd",
    ],
)
def test_risky_commands_never_offer_always(monkeypatch, conv, command: str) -> None:
    asked = _answer(monkeypatch, ["once"])
    assert pp.decide("Bash", {"command": command}, conv_id="c1")["behavior"] == "allow"
    ids = [o["id"] for o in asked[0]["questions"][0]["options"]]
    assert ids == ["once", "all", "deny"]  # never "always <this>", but Allow everything is offered
    assert pp._rules("c1") == []


@pytest.mark.parametrize(
    "command",
    [
        "gh auth status 2>&1 | head -5",
        "git add -A && git commit -m x",
        'py -3 -c "\nimport json\nprint(json.dumps({}))\n"',
    ],
)
def test_chained_commands_offer_only_allow_everything(monkeypatch, conv, command: str) -> None:
    asked = _answer(monkeypatch, ["once"])
    assert pp.decide("Bash", {"command": command}, conv_id="c1")["behavior"] == "allow"
    ids = [o["id"] for o in asked[0]["questions"][0]["options"]]
    assert ids == ["once", "all", "deny"]
    assert pp._rules("c1") == []


def test_allow_everything_never_asks_again(monkeypatch, conv) -> None:
    script = 'py -3 -c "\nimport json\nd=json.load(open(r\'C:/x.txt\'))\nprint(list(d.keys()))\n"'
    asked = _answer(monkeypatch, ["all"])
    assert pp.decide("Bash", {"command": script}, conv_id="c1")["behavior"] == "allow"
    assert pp._rules("c1") == ["*"]
    asked.clear()
    for tool, payload in (
        ("Bash", {"command": "npm run test && npm run lint"}),
        ("PowerShell", {"command": "Get-ChildItem | Select-Object -First 5"}),
        ("Write", {"file_path": r"D:\elsewhere\a.txt", "content": "x"}),
        ("WebFetch", {"url": "https://example.com"}),
    ):
        assert pp.decide(tool, payload, conv_id="c1")["behavior"] == "allow"
    # Pushes, deletes, publishes and deploys too: the person said everything.
    for command in ("git push origin main", "rm -rf build", "py scripts/release.py --publish", "git reset --hard HEAD~1"):
        assert pp.decide("Bash", {"command": command}, conv_id="c1")["behavior"] == "allow"
    assert asked == []


def test_a_remembered_command_rule_never_covers_a_risky_command(monkeypatch, conv) -> None:
    pp._remember("c1", "Bash:git push")
    asked = _answer(monkeypatch, ["deny"])
    assert pp.decide("Bash", {"command": "git push"}, conv_id="c1")["behavior"] == "deny"
    assert asked, "only Allow everything stops the card for a push"


def test_allow_everything_pushes_an_ai_plugin_like_any_repo(monkeypatch, conv) -> None:
    # Oct 22 2026: AI plugins are Store plugins (owner, Oct 9); the old local-only refusal
    # stopped an agent pushing the Anthropic plugin's own fix.
    pp._remember("c1", "*")
    asked = _answer(monkeypatch, ["deny"])
    plugin = r"C:\GitHub\uefn-plugins\uefn-plugin-anthropic"
    for command in ("git push", "git -C C:/x/uefn-plugin-openai push origin main", "cd C:/x/uefn-plugin-kimi && git push"):
        assert pp.decide("Bash", {"command": command}, conv_id="c1", project_root=plugin)["behavior"] == "allow", command
    assert asked == []


def test_allow_everything_covers_the_runs_a_chat_starts(monkeypatch, conv) -> None:
    monkeypatch.setattr(pp, "_chat_exists", lambda cid: cid != "deleted")
    pp._remember("lead", "*")
    pp.note_started_by("sub", "lead")  # lead sent it work (a sub-agent, a workflow ducky)
    pp.note_started_by("member", "sub")
    pp.note_started_by("loop-a", "loop-b")
    pp.note_started_by("loop-b", "loop-a")
    pp._remember("deleted", "*")
    pp.note_started_by("orphan", "deleted")
    assert pp.allows_everything("lead") and pp.allows_everything("sub") and pp.allows_everything("member")
    for other in ("loop-a", "orphan", "stranger", ""):
        assert not pp.allows_everything(other), other
    asked = _answer(monkeypatch, ["deny"])
    assert pp.decide("Bash", {"command": "npm test && git push"}, conv_id="member")["behavior"] == "allow"
    assert asked == []
    assert pp.allow_state("member") == {"on": True, "own": False, "from_title": "the chat that started it"}
    pp.note_started_by("sub", "")  # its next run came from its own chat, a schedule or the Workflows screen
    assert not pp.allows_everything("sub") and not pp.allows_everything("member")


def test_who_started_a_run_is_kept_by_follow_ups(monkeypatch, conv) -> None:
    from frontend.ui_web.agent_modes import _note_run_starter

    monkeypatch.setattr(pp, "_chat_exists", lambda cid: True)
    pp._remember("lead", "*")
    _note_run_starter("member", "lead", None)  # the lead started it
    assert pp.allows_everything("member")
    _note_run_starter("member", "", None)  # a follow-up (send a message, a2a, a group round) keeps it
    assert pp.allows_everything("member")
    _note_run_starter("member", "hub", "lead")  # recycled twin: the hub nests it, the lead still covers it
    assert pp.allows_everything("member")
    _note_run_starter("member", "", "")  # a workflow run no chat started resets a reused seat
    assert not pp.allows_everything("member")


def test_allow_everything_turns_off_and_goes_with_its_chat(monkeypatch, conv) -> None:
    pp._remember("c1", "Bash:git status")
    pp.set_allow_everything("c1", True)
    assert pp.allow_state("c1") == {"on": True, "own": True, "from_title": ""}
    pp.set_allow_everything("c1", False)
    assert pp._rules("c1") == ["Bash:git status"] and not pp.allows_everything("c1")
    pp.set_allow_everything("c1", True)
    pp.note_started_by("c1", "lead")
    kept = pp.approvals_of("c1")  # a recycled member's twin keeps it
    pp.forget_chat("c1")  # deleting the chat drops its rows
    assert pp._rules("c1") == [] and pp._started_by("c1") == ""
    pp.restore_approvals("twin", kept)
    assert pp.allows_everything("twin") and pp._started_by("twin") == "lead"


def test_allow_everything_in_every_chat_never_shows_a_permission_card(monkeypatch, conv) -> None:
    # Oct 31 2026: harness-race chats an outside agent started had no chat to take Allow
    # everything from, and their sandbox asked though the person allows everything.
    from types import SimpleNamespace

    from backend.sandbox import policy

    settings = SimpleNamespace(allow_everything_all_chats=False)
    monkeypatch.setattr("frontend.settings.PanelSettings.load", lambda *a, **k: settings)
    asked = _answer(monkeypatch, ["deny"])
    assert not pp.allows_everything("race") and pp.permission_mode("race") == pp.MODE_EDITS
    settings.allow_everything_all_chats = True
    assert pp.allows_everything("race") and pp.allows_sandbox("race") and pp.permission_mode("race") == pp.MODE_ALL
    assert pp.decide("Bash", {"command": "git push --force"}, conv_id="race")["behavior"] == "allow"
    assert policy.ask_to_run("race", "Start a program", "python -m http.server") == "sandbox"
    assert asked == []
    assert pp.allow_state("race") == {"on": True, "own": False, "from_title": "Settings → Permissions"}
    # A chat's own Allow everything stays its own, so it can still be turned off there.
    pp.set_allow_everything("own", True)
    assert pp.allow_state("own")["own"] is True
    # The switch is the person's: no AI settings tool can turn it on.
    from frontend.settings_schema import settable_keys

    assert "allow_everything_all_chats" not in settable_keys()


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "LS"])
def test_read_only_tools_never_ask(monkeypatch, conv, tool: str) -> None:
    asked = _answer(monkeypatch, ["deny"])
    payload = {"file_path": r"C:\Users\me\AppData\Local\UEFN-Ducky\brainrot_tcg\assets\a.png"}
    assert pp.decide(tool, payload, conv_id="c1") == {"behavior": "allow", "updatedInput": payload}
    assert asked == []


def test_quoted_body_is_not_part_of_the_rule() -> None:
    assert pp._command_key('git commit -m "fix the thing"') == "git commit"
    assert pp._command_key('py -3 -c "print(1)"') == "py"


def test_deny_passes_the_users_reason(monkeypatch, conv) -> None:
    _answer(monkeypatch, [], text="use the staging branch")
    out = pp.decide("Bash", {"command": "npm test"}, conv_id="c1")
    assert out == {"behavior": "deny", "message": "The user denied this and said: use the staging branch"}


def test_push_in_an_ai_plugin_asks_like_any_push(monkeypatch, conv) -> None:
    asked = _answer(monkeypatch, ["once"])
    out = pp.decide("Bash", {"command": "git push"}, conv_id="c1", project_root=r"C:\GitHub\uefn-plugins\uefn-plugin-anthropic")
    assert out["behavior"] == "allow"
    assert asked[0]["questions"][0]["warning"] == "Pushes commits to a remote."
    assert [o["id"] for o in asked[0]["questions"][0]["options"]] == ["once", "all", "deny"]


def test_panel_unreachable_denies(monkeypatch, conv) -> None:
    monkeypatch.setattr(
        "backend.tools.panel.panel_ui.ducky_ask_user", lambda *_a, **_k: json.dumps({"error": "panel not running"})
    )
    out = pp.decide("Bash", {"command": "ls"}, conv_id="c1")
    assert out == {"behavior": "deny", "message": "No approval: panel not running"}


def test_file_tool_card_shows_path(monkeypatch, conv) -> None:
    asked = _answer(monkeypatch, ["once"])
    out = pp.decide("Write", {"file_path": r"D:\elsewhere\a.txt", "content": "x"}, conv_id="c1", project_root=r"C:\repo")
    assert out["behavior"] == "allow"
    question = asked[0]["questions"][0]
    assert question["detail"] == r"D:\elsewhere\a.txt"
    assert question["warning"] == "Outside the project folder."


def test_a_folder_always_allowed_once_is_allowed_for_every_ducky(monkeypatch, conv) -> None:
    asked = _answer(monkeypatch, ["always"])
    monkeypatch.setattr(pp, "_chat_agent", lambda _cid: "ducky")
    assert pp.approve_outside_folder("c1", r"D:\Work\Repo", r"D:\Work\Repo\a.py") == ""
    assert asked[0]["questions"][0]["options"][1]["label"] == "Always allow this folder"
    # Another ducky reaches it with no card; it shows in any chat's permissions and goes from there.
    asked.clear()
    assert pp.approve_outside_folder("c2", r"D:\Work\Repo", r"D:\Work\Repo\b.py") == ""
    assert asked == []
    assert r"D:\Work\Repo" in pp.approved_folders("c2")
    assert {"rule": r"folder:D:\Work\Repo", "label": r"folder: D:\Work\Repo"} in pp.allowed_rules("c2")
    pp.forget_rule("c2", r"folder:D:\Work\Repo")
    assert pp.approved_folders("c1") == []


def test_tool_returns_claude_code_json(monkeypatch, conv) -> None:
    _answer(monkeypatch, ["once"])
    monkeypatch.setattr(pp, "_conv_id", lambda: "c1")
    raw = pp.ducky_permission_prompt("Bash", {"command": "ls"}, tool_use_id="t1")
    assert json.loads(raw) == {"behavior": "allow", "updatedInput": {"command": "ls"}}


def test_hook_is_hidden_from_the_embedded_agent() -> None:
    from backend.agent.toolsets.excluded import EXCLUDED_TOOLS

    assert "ducky_permission_prompt" in EXCLUDED_TOOLS


def test_a_chat_save_cannot_drop_a_remembered_approval(monkeypatch, conv) -> None:
    """The app saves its own copy of the chat during a turn; the rule is not on that record."""
    asked = _answer(monkeypatch, ["all"])
    assert pp.decide("Bash", {"command": "npm test"}, conv_id="c1")["behavior"] == "allow"
    assert conv.saved == [] and conv.agent_allow_rules == []  # nothing written onto the chat
    assert pp._rules("c1") == ["*"] and pp._rules("other") == []
    asked.clear()
    script = 'py -3 -c "\nprint(1)\n"'  # multi-line: covered by "allow everything"
    assert pp.decide("Bash", {"command": script}, conv_id="c1")["behavior"] == "allow"
    assert asked == []


def test_a_chat_keeps_its_approval_mode(conv) -> None:
    assert pp.permission_mode("c1") == "edits"  # the default: edits go ahead, commands ask
    pp._remember("c1", "Bash:git status")
    pp.set_permission_mode("c1", "ask")
    assert pp.permission_mode("c1") == "ask" and not pp.allows_everything("c1")
    pp.set_permission_mode("c1", "all")
    assert pp.permission_mode("c1") == "all" and pp.allow_state("c1")["own"]
    pp.set_permission_mode("c1", "edits")
    assert pp.permission_mode("c1") == "edits" and not pp.allows_everything("c1")
    assert pp._rules("c1") == ["Bash:git status"]  # picking a mode never drops a rule
    assert pp.permission_mode("other") == "edits"
    with pytest.raises(ValueError):
        pp.set_permission_mode("c1", "sometimes")


def test_allow_everything_from_a_card_wins_and_turning_it_off_returns_to_the_mode(monkeypatch, conv) -> None:
    pp.set_permission_mode("c1", "ask")
    _answer(monkeypatch, ["all"])
    assert pp.decide("Bash", {"command": "npm test"}, conv_id="c1")["behavior"] == "allow"
    assert pp.permission_mode("c1") == "all"
    pp.set_allow_everything("c1", False)  # the context panel's Turn off
    assert pp.permission_mode("c1") == "ask"


def test_claude_code_asks_before_edits_only_when_the_chat_asks_before_changes(conv) -> None:
    assert pp.claude_permission_mode("c1", "acceptEdits") == "acceptEdits"
    assert pp.claude_permission_mode("c1", "") == "acceptEdits"
    assert pp.claude_permission_mode("c1", "bypassPermissions") == "bypassPermissions"
    pp.set_permission_mode("c1", "ask")
    assert pp.claude_permission_mode("c1", "acceptEdits") == "default"
    pp.set_permission_mode("c1", "all")
    assert pp.claude_permission_mode("c1", "acceptEdits") == "acceptEdits"


@pytest.mark.parametrize(
    "mode,tool,destructive,gate",
    [
        ("edits", "workspace_write_file", False, "run"),
        ("edits", "workspace_delete_file", False, "run"),
        ("edits", "destroy_entity", True, "refuse"),
        ("edits", "workspace_read_file", False, "run"),
        ("ask", "workspace_edit_file", False, "ask"),
        ("ask", "workspace_move_file", False, "ask"),
        ("ask", "destroy_entity", True, "ask"),
        ("ask", "workspace_read_file", False, "run"),
        ("all", "workspace_write_file", False, "run"),
        ("all", "destroy_entity", True, "run"),
    ],
)
def test_the_embedded_agent_gate_follows_the_mode(conv, mode, tool, destructive, gate) -> None:
    pp.set_permission_mode("c1", mode)
    assert pp.ducky_tool_gate("c1", tool, destructive=destructive) == gate


def test_ducky_file_edit_card_can_be_always_allowed(monkeypatch, conv) -> None:
    asked = _answer(monkeypatch, ["always"])
    assert pp.approve_ducky_tool("c1", "workspace_write_file", {"relative_path": "Verse/game.verse", "content": "x"})
    question = asked[0]["questions"][0]
    assert question["prompt"] == "Allow Ducky to edit this file?" and question["detail"] == "Verse/game.verse"
    assert [o["label"] for o in question["options"]][1] == "Always allow file edits in this chat"
    assert pp._rules("c1") == ["ducky:edits"]
    asked.clear()
    assert pp.approve_ducky_tool("c1", "workspace_edit_file", {"relative_path": "Verse/other.verse"})
    assert asked == []


def test_ducky_delete_and_destructive_cards_never_offer_always(monkeypatch, conv) -> None:
    asked = _answer(monkeypatch, ["deny"])
    assert not pp.approve_ducky_tool("c1", "workspace_delete_file", {"relative_path": "Verse/old.verse"})
    assert not pp.approve_ducky_tool("c1", "destroy_entity", {"entity": "Boss"})
    for card in asked:
        assert [o["id"] for o in card["questions"][0]["options"]] == ["once", "all", "deny"]
    assert asked[1]["questions"][0]["warning"] == "This change is hard to undo."
    assert pp._rules("c1") == []
    _answer(monkeypatch, ["all"])
    assert pp.approve_ducky_tool("c1", "destroy_entity", {})
    assert pp.permission_mode("c1") == "all"


def test_the_popup_lists_rules_and_removes_them(conv) -> None:
    for rule in ("*", "Bash:git status", "Write:c:/repo/src", "ducky:edits", "WebFetch"):
        pp._remember("c1", rule)
    assert pp.allowed_rules("c1") == [
        {"rule": "Bash:git status", "label": "Bash: git status"},
        {"rule": "Write:c:/repo/src", "label": "Write: c:/repo/src"},
        {"rule": "ducky:edits", "label": "Ducky: file edits"},
        {"rule": "WebFetch", "label": "WebFetch"},
    ]
    pp.forget_rule("c1", "Write:c:/repo/src")
    assert [r["rule"] for r in pp.allowed_rules("c1")] == ["Bash:git status", "ducky:edits", "WebFetch"]
    pp.clear_rules("c1")
    assert pp.allowed_rules("c1") == [] and pp.permission_mode("c1") == "all"


def test_the_mode_goes_with_its_chat_and_to_a_recycled_twin(conv) -> None:
    pp.set_permission_mode("c1", "ask")
    kept = pp.approvals_of("c1")
    pp.forget_chat("c1")
    assert pp.permission_mode("c1") == "edits"
    pp.restore_approvals("twin", kept)
    assert pp.permission_mode("twin") == "ask"


_LABELS = {"ducky": "Ducky", "claude_code": "Claude Code", "old_claude": "Claude Code", "codex": "Codex", "cursor": "Cursor"}


@pytest.fixture
def agents(monkeypatch):
    regs = {
        "claude_code": {"plugin_id": "anthropic", "chat_permission_modes": ("ask", "edits", "all"),
                        "settings_defaults": {"permission_mode": "acceptEdits"}},
        "old_claude": {"plugin_id": "anthropic", "settings_defaults": {"permission_mode": "acceptEdits"}},
        "codex": {"plugin_id": "openai", "settings_defaults": {}},
        "cursor": {"plugin_id": "cursor", "settings_defaults": {}},
    }
    manifests = {"anthropic": {"label": "Anthropic", "version": "1.0.54", "source": "store"}}
    monkeypatch.setattr(pp, "_agent_registration", lambda aid: regs.get(aid, {}))
    monkeypatch.setattr("backend.agent.coding_agents.base.coding_agent_label", lambda aid: _LABELS.get(aid, aid))
    monkeypatch.setattr("backend.uefn_plugins.host.registered_coding_agent_ids", lambda: tuple(sorted(regs)))
    monkeypatch.setattr("backend.uefn_plugins.store.load_plugin_manifest", lambda pid: manifests.get(pid))
    return regs, manifests


def test_each_agent_shows_the_modes_it_can_honour(agents, conv) -> None:
    def reasons(agent: str) -> dict[str, str]:
        return {m["id"]: m["reason"] for m in pp.chat_permissions("c1", agent)["modes"]}

    assert reasons("ducky") == {"ask": "", "edits": "", "all": ""}
    assert reasons("claude_code") == {"ask": "", "edits": "", "all": ""}
    assert pp.chat_permissions("c1", "claude_code")["update"] is None
    # An older Claude Code plugin: the missing mode names the plugin and the version on this PC.
    old = pp.chat_permissions("c1", "old_claude")
    assert {m["id"]: m["reason"] for m in old["modes"]} == {
        "ask": "Needs a newer Anthropic plugin (this PC has 1.0.54).", "edits": "", "all": ""}
    assert old["update"] == {"plugin_id": "anthropic", "label": "Anthropic", "version": "1.0.54", "from_store": True}
    assert old["note"] == ""


def test_an_agent_that_never_asks_says_what_it_does_and_which_agents_do_ask(agents, conv) -> None:
    state = pp.chat_permissions("c1", "codex")
    assert not state["asks"]
    assert state["note"] == "Codex edits files and runs commands without asking. Ducky can't stop it to ask first."
    # Only agents on this PC that really stop for a card: not the older Claude Code, not Cursor.
    assert state["hint"] == "To approve changes first, switch this chat to Ducky or Claude Code."
    assert all(not m["available"] and m["reason"] == state["note"] for m in state["modes"])
    assert state["update"] is None
    # The chat's own mode is kept for when it goes back to an agent that asks.
    assert state["mode"] == "edits"


def test_the_update_button_installs_the_store_plugin_and_never_a_local_copy(agents, conv, monkeypatch) -> None:
    _regs, manifests = agents
    calls: list[tuple[str, bool]] = []

    def fake_install(slug, *, replace=True, is_update=False, version=None):
        calls.append((slug, is_update))
        return {"ok": True, "version": "1.0.56"}

    monkeypatch.setattr("frontend.duckyos_account.store_download_and_install", fake_install)
    assert pp.update_agent_plugin("old_claude") == {"ok": True, "plugin_id": "anthropic", "version": "1.0.56"}
    assert calls == [("anthropic", True)]

    manifests["anthropic"]["source"] = "local"
    assert pp.chat_permissions("c1", "old_claude")["update"]["from_store"] is False
    with pytest.raises(ValueError, match="local copy"):
        pp.update_agent_plugin("old_claude")
    assert len(calls) == 1
    with pytest.raises(ValueError, match="which plugin"):
        pp.update_agent_plugin("nobody")


def test_a_mode_set_by_the_starting_chat_cannot_be_changed_here(monkeypatch, conv) -> None:
    monkeypatch.setattr(pp, "_chat_exists", lambda cid: True)
    pp._remember("lead", "*")
    pp.note_started_by("member", "lead")
    state = pp.chat_permissions("member", "ducky")
    assert state["mode"] == "all" and not state["own"] and state["from_title"] == "the chat that started it"
    reasons = {m["id"]: m["reason"] for m in state["modes"]}
    assert reasons["all"] == ""
    assert reasons["ask"] == reasons["edits"] == "Allow everything is on from the chat that started it. Turn it off there."


def test_a_plugin_with_no_recorded_source_is_updated_from_the_store(agents, conv) -> None:
    _regs, manifests = agents
    manifests["anthropic"].pop("source", None)
    assert pp.chat_permissions("c1", "old_claude")["update"]["from_store"] is True


def test_a_failed_read_of_a_chats_rules_is_retried_and_logged(monkeypatch, caplog) -> None:
    # Oct 12 2026: a read that failed counted as "no rules", so a team whose chats allowed
    # everything had its commands wait on approval cards nobody saw.
    from backend.store.repos import kv

    monkeypatch.setattr(pp, "_rules_in_db", lambda: True)
    monkeypatch.setattr(pp.time, "sleep", lambda s: None)
    calls = []

    def flaky(table, key):
        calls.append(key)
        if len(calls) < 3:
            raise RuntimeError("database is locked")
        return ["*"]

    monkeypatch.setattr(kv, "get_doc", flaky)
    assert pp._rules("c-flaky") == ["*"] and len(calls) == 3
    monkeypatch.setattr(kv, "get_doc", lambda table, key: (_ for _ in ()).throw(RuntimeError("database is locked")))
    with caplog.at_level("WARNING"):
        assert pp._rules("c-down") == []
    assert "could not read the approval rules of chat c-down" in caplog.text


def test_a_card_also_shows_at_the_top_of_its_started_by_links(monkeypatch, conv) -> None:
    # Oct 15 2026: a team member's terminal card waited in its own chat, which nobody had
    # open, and the whole team stopped. Its cards also show in the chat that leads it.
    monkeypatch.setattr(pp, "_chat_exists", lambda cid: cid != "deleted")
    pp.note_started_by("member", "group-lead")
    pp.note_started_by("group-lead", "lead")
    pp.note_started_by("loop-a", "loop-b")
    pp.note_started_by("loop-b", "loop-a")
    pp.note_started_by("orphan", "deleted")
    assert pp.starter_root("member") == "lead" and pp.starter_root("group-lead") == "lead"
    for alone in ("lead", "stranger", "orphan", ""):
        assert pp.starter_root(alone) == "", alone
    assert pp.starter_root("loop-a") == "loop-b"  # a loop ends instead of spinning
