"""Bridge and panel must agree on the project root."""

from __future__ import annotations

from pathlib import Path

from backend.bridge import client


def test_uefnproject_file_selection_resolves_to_its_folder(tmp_path: Path) -> None:
    root = tmp_path / "Island"
    root.mkdir()
    marker = root / ".uefnproject"
    marker.write_text("{}", encoding="utf-8")
    assert client._normalize_project_root(str(marker)) == str(root.resolve())  # noqa: SLF001
    assert client._normalize_project_root(str(root)) == str(root.resolve())  # noqa: SLF001


def test_configured_roots_prefer_env_then_panel(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("UEFN_VSCODE_WORKSPACE_FOLDERS", raising=False)
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(tmp_path))
    roots = client._configured_project_roots()  # noqa: SLF001
    assert roots == [str(tmp_path.resolve())]


def test_content_prefix_never_doubles_under_a_content_root(monkeypatch, tmp_path: Path) -> None:
    # Nov 2 2026: a read of a not-yet-existing file came back as ...\Content\Content\Verse\...
    content = tmp_path / "Island" / "Content"
    content.mkdir(parents=True)
    monkeypatch.delenv("UEFN_VSCODE_WORKSPACE_FOLDERS", raising=False)
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(content))
    want = str((content / "Verse" / "Life" / "a.verse").resolve())
    assert client.resolve_workspace_path("Content/Verse/Life/a.verse") == want
    assert client.resolve_workspace_path("Verse/Life/a.verse") == want
    (content / "Content").mkdir()  # a real nested Content folder keeps the literal path
    assert client.resolve_workspace_path("Content/x.txt") == str((content / "Content" / "x.txt").resolve())


def test_absolute_path_under_a_recent_project_is_readable(monkeypatch, tmp_path: Path) -> None:
    active = tmp_path / "Active"
    other = tmp_path / "Roguelike"
    outside = tmp_path / "NotAProject"
    active.mkdir()
    other.mkdir()
    outside.mkdir()
    verse = other / "Content" / "Verse" / "ui.verse"
    verse.parent.mkdir(parents=True)
    verse.write_text("ok", encoding="utf-8")
    monkeypatch.delenv("UEFN_VSCODE_WORKSPACE_FOLDERS", raising=False)
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(active))
    monkeypatch.setattr(
        "frontend.ui_web.recent_projects.load_saved_projects",
        lambda: [(str(active), 0.0), (str(other), 0.0)],
    )
    assert client.resolve_workspace_path(str(verse)) == str(verse.resolve())
    try:
        client.resolve_workspace_path(str(outside / "secret.txt"))
    except ValueError as exc:
        # The refusal names where the tools do reach, so a model stops retrying the path.
        assert str(other.resolve()) in str(exc) and "Do not retry" in str(exc)
        return
    raise AssertionError("path outside every project should be refused")


def test_a_shared_server_call_uses_its_agents_project(monkeypatch, tmp_path: Path) -> None:
    # Oct 15 2026: the shared tool server was started while project B was open; agents started
    # later in project D wrote their relative paths into B. Each call carries its agent's project.
    from backend.bridge.shared_mcp import identity_from_payload
    from backend.workspace import identity
    from frontend import shared_mcp_adapter as adapter

    started_in, agent_in = tmp_path / "B", tmp_path / "D"
    started_in.mkdir()
    agent_in.mkdir()
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(agent_in))
    monkeypatch.setenv("UEFN_VSCODE_WORKSPACE_FOLDERS", f'["{agent_in.as_posix()}"]')
    monkeypatch.setenv("DUCKY_CONV_ID", "member")
    sent = adapter._identity()  # noqa: SLF001 - what the adapter sends in its hello
    assert sent["project_root"] == str(agent_in) and sent["conv_id"] == "member"

    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(started_in))  # the server's own env
    monkeypatch.setenv("UEFN_VSCODE_WORKSPACE_FOLDERS", f'["{started_in.as_posix()}"]')
    token = identity.bind(identity_from_payload(sent))
    try:
        assert client.workspace_roots() == [str(agent_in.resolve())]
        assert client.resolve_workspace_path("textkit/stats.py") == str((agent_in / "textkit" / "stats.py").resolve())
    finally:
        identity.reset(token)
    # A call without an agent project (an older adapter) keeps the server's own roots.
    assert client.workspace_roots() == [str(started_in.resolve())]


def test_an_ide_agent_follows_the_project_open_in_ducky(monkeypatch, tmp_path: Path) -> None:
    # Oct 21 2026: Claude Code's Ducky tools kept reading and writing Roguelike, the project
    # open when the shared tool server started, after the person had opened ExampleProject1.
    from backend.memory.project import resolve_project_root

    started_in, open_now = tmp_path / "Roguelike", tmp_path / "ExampleProject1"
    started_in.mkdir()
    open_now.mkdir()
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(started_in))  # set when the server started
    monkeypatch.setenv("UEFN_VSCODE_WORKSPACE_FOLDERS", f'["{started_in.as_posix()}"]')
    monkeypatch.delenv("DUCKY_CONV_ID", raising=False)
    monkeypatch.delenv("DUCKY_RUN_ID", raising=False)
    monkeypatch.setattr(client, "_open_project_setting", lambda: str(open_now))  # Ducky's header
    assert client.workspace_roots() == [str(open_now.resolve())]
    assert client.resolve_workspace_path("notes.txt") == str((open_now / "notes.txt").resolve())
    assert resolve_project_root() == str(open_now)  # its memory too
    # An agent Ducky started keeps the project it was started in.
    monkeypatch.setenv("DUCKY_CONV_ID", "member")
    assert client.workspace_roots() == [str(started_in.resolve())]
    assert resolve_project_root() == str(started_in)
