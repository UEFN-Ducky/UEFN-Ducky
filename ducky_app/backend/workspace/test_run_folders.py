"""A workflow run lends the folders it names to its Agent steps, one step's chat at a time."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from backend.workspace import identity, run_folders


@pytest.fixture
def out(tmp_path: Path) -> Path:
    folder = tmp_path / "Ducky Trailers"
    folder.mkdir()
    return folder


def _as(conv_id: str):
    return identity.bind(identity.RunContext(conv_id=conv_id, run_id="r"))


def test_a_named_folder_is_lent_but_never_a_broad_or_private_one(out: Path, monkeypatch) -> None:
    assert run_folders.usable_folder(str(out)) == os.path.realpath(out)
    monkeypatch.setenv("TRAILERS_TEST", str(out))
    assert run_folders.usable_folder("%TRAILERS_TEST%") == os.path.realpath(out)
    home = Path.home()
    for raw in (
        "",
        "relative/folder",
        "line one\nline two",
        str(home),
        str(home.parent),
        Path(str(home)).anchor,
        str(home / ".ssh"),
        str(out / ".git" / "objects"),
        os.path.join(os.environ["LOCALAPPDATA"], "UEFN-Ducky"),
    ):
        assert run_folders.usable_folder(raw) == "", raw
    (out / "bible.json").write_text("{}", encoding="utf-8")
    assert run_folders.usable_folder(str(out / "bible.json")) == ""  # a file, not a folder


def test_the_folders_a_workflow_names_are_its_text_inputs(out: Path) -> None:
    nodes = [
        {"type": "input.text", "config": {"value": str(out)}},
        {"type": "input.text", "config": {"value": "Formats: 16:9 trailer"}},
        {"type": "input.number", "config": {"value": 3}},
        {"type": "input.text", "config": {"value": str(out)}},
    ]
    assert run_folders.named_folders(nodes) == [os.path.realpath(out)]


def test_a_grant_is_one_chat_s_until_released_or_expired(out: Path, monkeypatch) -> None:
    release = run_folders.grant("step-chat", [str(out)], 60)
    assert run_folders.granted("step-chat") == [str(out)]
    assert run_folders.granted("another-chat") == []
    release()
    assert run_folders.granted("step-chat") == []
    run_folders.grant("step-chat", [str(out)], 1)
    later = time.time() + 5
    monkeypatch.setattr(run_folders.time, "time", lambda: later)  # a crashed run's grant runs out
    assert run_folders.granted("step-chat") == []


def test_the_step_s_agent_reads_and_writes_the_lent_folder_and_no_one_else(out: Path, monkeypatch) -> None:
    from backend.bridge import client
    from backend.workspace import writer

    project = out.parent / "Island"
    project.mkdir()
    monkeypatch.delenv("UEFN_VSCODE_WORKSPACE_FOLDERS", raising=False)
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(project))
    monkeypatch.setattr("frontend.ui_web.recent_projects.load_saved_projects", lambda: [])
    bible = out / "goblins_1430" / "bible.json"
    release = run_folders.grant("step-chat", [os.path.realpath(out)], 60)
    try:
        token = _as("step-chat")
        try:
            assert client.resolve_workspace_path(str(bible)) == os.path.realpath(bible)
            writer._require_writable(str(bible), os.path.realpath(out))  # plain files, no island rules
        finally:
            identity.reset(token)
        token = _as("another-chat")
        try:
            with pytest.raises(ValueError, match="outside allowed workspace folders"):
                client.resolve_workspace_path(str(bible))
        finally:
            identity.reset(token)
    finally:
        release()


def test_an_agent_reaching_outside_the_projects_asks_the_person_for_that_folder(out: Path, monkeypatch) -> None:
    # Oct 21 2026: agents told people they had "no permission" for a file the person had just
    # asked them to change: the file tools refused every path outside the projects outright.
    from backend.bridge import client
    from backend.tools.panel import permission_prompt

    project = out.parent / "Island"
    project.mkdir()
    (out.parent / ".hidden").mkdir()
    monkeypatch.delenv("UEFN_VSCODE_WORKSPACE_FOLDERS", raising=False)
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(project))
    monkeypatch.setattr("frontend.ui_web.recent_projects.load_saved_projects", lambda: [])
    monkeypatch.setattr(permission_prompt, "_chat_agent", lambda conv_id: "codex")
    answers: list[tuple[str, str]] = [("always", "")]
    cards: list[dict] = []

    def ask(card, tool_name):
        cards.append(card)
        return answers.pop(0)

    monkeypatch.setattr(permission_prompt, "_ask", ask)
    new_file = out / "sub" / "notes.txt"  # sub is new: the card names the folder that exists
    token = _as("chat-a")
    try:
        assert client.resolve_workspace_path(str(new_file)) == os.path.realpath(new_file)
        assert client.resolve_workspace_path(str(out / "other.txt"))  # remembered for this chat
        assert [c["rule"] for c in cards] == ["folder:" + os.path.realpath(out)]
        with pytest.raises(ValueError, match="never opens"):  # never even asked
            client.resolve_workspace_path(str(out.parent / ".hidden" / "key.txt"))
        assert len(cards) == 1
    finally:
        identity.reset(token)
    other = out.parent / "Elsewhere"
    other.mkdir()
    answers.append(("deny", "use the project"))
    token = _as("chat-b")
    try:
        # "Always" counts for every ducky: another chat reaches that folder with no card.
        assert client.resolve_workspace_path(str(new_file)) == os.path.realpath(new_file)
        assert len(cards) == 1
        with pytest.raises(ValueError, match="said no .* use the project"):
            client.resolve_workspace_path(str(other / "a.txt"))
    finally:
        identity.reset(token)


def test_a_verse_digest_points_the_agent_to_the_digest_tools() -> None:
    # Oct 30 2026: an agent retried workspace_read_file on UEFN's Fortnite.digest.verse four times.
    saved = os.path.join(os.environ["LOCALAPPDATA"], "UnrealEditorFortnite", "Saved", "VerseProject", "FortniteGame")
    for path in (os.path.join(saved, "Digests", "BuiltIn", "Fortnite", "Fortnite.digest.verse"),
                 os.path.join(saved, "Digests", "BuiltIn", "Fortnite")):
        why = run_folders.ask_to_lend(path)
        assert "search_verse_digest" in why and "get_verse_api" in why, why
