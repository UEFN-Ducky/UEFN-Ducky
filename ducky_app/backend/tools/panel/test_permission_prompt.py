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


def test_allow_everything_still_refuses_to_push_a_local_only_ai_plugin(monkeypatch, conv) -> None:
    pp._remember("c1", "*")
    asked = _answer(monkeypatch, ["once"])
    out = pp.decide("Bash", {"command": "git push"}, conv_id="c1", project_root=r"C:\GitHub\uefn-plugins\uefn-plugin-anthropic")
    assert out["behavior"] == "deny" and "local-only AI plugin" in out["message"]
    assert asked == []
    assert pp.decide("Bash", {"command": "git status"}, conv_id="c1",
                     project_root=r"C:\GitHub\uefn-plugins\uefn-plugin-anthropic")["behavior"] == "allow"


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


def test_a_cd_into_a_local_only_plugin_then_push_is_refused(monkeypatch, conv) -> None:
    pp._remember("c-cd", "*")
    pp._SHELL_DIRS.pop("chat:c-cd", None)
    repo = r"C:\GitHub\UEFN-Ducky-Release"
    assert pp.decide("Bash", {"command": "cd C:/GitHub/uefn-plugins/uefn-plugin-anthropic"}, conv_id="c-cd", project_root=repo)["behavior"] == "allow"
    out = pp.decide("Bash", {"command": "git push origin main"}, conv_id="c-cd", project_root=repo)
    assert out["behavior"] == "deny" and "local-only AI plugin" in out["message"]
    assert pp.decide("Bash", {"command": "cd C:/GitHub/UEFN-Ducky-Release"}, conv_id="c-cd", project_root=repo)["behavior"] == "allow"
    assert pp.decide("Bash", {"command": "git push origin main"}, conv_id="c-cd", project_root=repo)["behavior"] == "allow"


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


def test_the_local_only_refusal_knows_every_push_and_publish(tmp_path) -> None:
    (tmp_path / "uefn-plugin-anthropic").mkdir()
    (tmp_path / "uefn-plugin-meshy").mkdir()
    plugins, repo = str(tmp_path), r"C:\GitHub\UEFN-Ducky-Release"
    for command, where in (
        ("git -C C:/x/uefn-plugin-kimi push origin main", repo),
        ("gh repo create me/uefn-plugin-anthropic --public --source . --push", repo),
        ("cd C:/x/uefn-plugin-openai && gh release create v1.0.0", repo),
        ("py scripts/release.py --publish", r"C:\x\uefn-plugin-google"),
        ("npm publish", r"C:\x\uefn-plugin-ollama"),
        ("git push origin main", plugins),  # a folder of plugins: the shell may sit in a local-only one
        ('bash -c "cd ../uefn-plugins/uefn-plugin-anthropic && git push"', repo),  # wrapped in a quoted shell call
        ('cmd /c "git -C C:\\x\\uefn-plugin-anthropic push"', repo),
        ('powershell -Command "Set-Location ../uefn-plugin-kimi; git push origin main"', repo),
        ("gh api -X POST repos/UEFN-Ducky/uefn-plugin-anthropic/releases", repo),
        ("gh workflow run release.yml -R UEFN-Ducky/uefn-plugin-openai", repo),
    ):
        assert pp._never_runs(command, where), command
    # A bare push after an earlier `cd` into a local-only plugin.
    assert pp._never_runs("git push", repo, "C:/x/uefn-plugins/uefn-plugin-anthropic")
    for command, where in (
        ("git -C uefn-plugin-meshy push", plugins),  # another plugin, named
        ("git push origin main", repo),
        ('git commit -am "no publish step"', r"C:\x\uefn-plugin-anthropic"),
        ('git commit -m "then git push it"', r"C:\x\uefn-plugin-anthropic"),
        ('git log --grep="git push"', r"C:\x\uefn-plugin-anthropic"),
        ("grep -rn publish backend/", r"C:\x\uefn-plugin-anthropic"),
        ("git log --oneline -3", r"C:\x\uefn-plugin-anthropic"),
    ):
        assert not pp._never_runs(command, where), command


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


def test_push_in_local_only_ai_plugin_warns(monkeypatch, conv) -> None:
    asked = _answer(monkeypatch, ["deny"])
    pp.decide("Bash", {"command": "git push"}, conv_id="c1", project_root=r"C:\GitHub\uefn-plugins\uefn-plugin-anthropic")
    assert "local-only AI plugin" in asked[0]["questions"][0]["warning"]
    assert [o["id"] for o in asked[0]["questions"][0]["options"]] == ["once", "deny"]  # no Allow everything here


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
    pp.set_permission_mode("c1", "all")  # the approval card tool still refuses local-only plugin pushes
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


def test_each_agent_shows_the_modes_it_can_honour(monkeypatch, conv) -> None:
    regs = {
        "claude_code": {"chat_permission_modes": ("ask", "edits", "all"), "settings_defaults": {"permission_mode": "acceptEdits"}},
        "old_claude": {"settings_defaults": {"permission_mode": "acceptEdits"}},
        "codex": {"settings_defaults": {}},
    }
    monkeypatch.setattr(pp, "_agent_registration", lambda aid: regs.get(aid, {}))
    monkeypatch.setattr("backend.agent.coding_agents.base.coding_agent_label",
                        lambda aid: {"codex": "Codex", "old_claude": "Claude Code"}.get(aid, aid))

    def reasons(agent: str) -> dict[str, str]:
        return {m["id"]: m["reason"] for m in pp.chat_permissions("c1", agent)["modes"]}

    assert reasons("ducky") == {"ask": "", "edits": "", "all": ""}
    assert reasons("claude_code") == {"ask": "", "edits": "", "all": ""}
    assert reasons("old_claude") == {"ask": "Update Claude Code in the Store to use this.", "edits": "", "all": ""}
    state = pp.chat_permissions("c1", "codex")
    assert not state["asks"] and {m["reason"] for m in state["modes"]} == {"Codex doesn't ask for approval in Ducky."}
    assert state["mode"] == "edits" and state["label"] == "Accept edits"


def test_a_mode_set_by_the_starting_chat_cannot_be_changed_here(monkeypatch, conv) -> None:
    monkeypatch.setattr(pp, "_chat_exists", lambda cid: True)
    pp._remember("lead", "*")
    pp.note_started_by("member", "lead")
    state = pp.chat_permissions("member", "ducky")
    assert state["mode"] == "all" and not state["own"] and state["from_title"] == "the chat that started it"
    reasons = {m["id"]: m["reason"] for m in state["modes"]}
    assert reasons["all"] == ""
    assert reasons["ask"] == reasons["edits"] == "Allow everything is on from the chat that started it. Turn it off there."
