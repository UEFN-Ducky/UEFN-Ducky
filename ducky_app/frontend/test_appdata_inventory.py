"""Settings → App Data inventory: sizes, jail, clear, delete, caches, projects."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from frontend.appdata_maintenance import (
    appdata_children,
    appdata_clear,
    appdata_clear_caches,
    appdata_delete,
    appdata_overview,
    appdata_projects,
    delete_project_appdata,
    resolve_appdata_rel,
)


def test_resolve_jail(tmp_path: Path) -> None:
    root = tmp_path / "UEFN-Ducky"
    root.mkdir()
    (root / "tool_captures").mkdir()
    assert resolve_appdata_rel("tool_captures", root) == (root / "tool_captures").resolve()
    assert resolve_appdata_rel("..", root) is None
    assert resolve_appdata_rel("../Windows", root) is None
    assert resolve_appdata_rel("tool_captures/../../Windows", root) is None
    assert resolve_appdata_rel("", root) == root.resolve()


def test_overview_sizes_and_flags(tmp_path: Path) -> None:
    root = tmp_path / "UEFN-Ducky"
    (root / "tool_captures").mkdir(parents=True)
    (root / "tool_captures" / "a.png").write_bytes(b"x" * 40)
    (root / "credentials.dat").write_bytes(b"secret")
    (root / "listener").mkdir()
    (root / "listener" / "boot.py").write_text("print(1)\n", encoding="utf-8")

    overview = appdata_overview(root)
    by_name = {row["name"]: row for row in overview["items"]}
    assert by_name["tool_captures"]["bytes"] == 40
    assert by_name["tool_captures"]["kind"] == "cache"
    assert by_name["tool_captures"]["clearable"] is True
    assert by_name["credentials.dat"]["protected"] is True
    assert by_name["credentials.dat"]["clearable"] is False
    assert by_name["listener"]["protected"] is True
    assert overview["bytes"] >= 40


def test_clear_keeps_folder_delete_removes(tmp_path: Path) -> None:
    root = tmp_path / "UEFN-Ducky"
    cap = root / "tool_captures"
    cap.mkdir(parents=True)
    (cap / "a.png").write_bytes(b"x")
    junk = root / "scratch"
    junk.mkdir()
    (junk / "f.txt").write_text("hi", encoding="utf-8")

    assert appdata_clear("credentials.dat", root)["error"] == "protected"
    assert appdata_clear("", root)["error"] == "refused_root"
    assert appdata_clear("tool_captures", root)["ok"] is True
    assert cap.is_dir()
    assert not (cap / "a.png").exists()

    assert appdata_delete("scratch", root)["ok"] is True
    assert not junk.exists()
    assert appdata_delete("", root)["error"] == "refused_root"
    assert appdata_delete("listener", root)["error"] == "protected"


def test_children_and_clear_caches(tmp_path: Path) -> None:
    root = tmp_path / "UEFN-Ducky"
    cap = root / "tool_captures"
    cap.mkdir(parents=True)
    (cap / "big.png").write_bytes(b"x" * 50)
    (cap / "tiny.png").write_bytes(b"y")
    chats = root / "chats" / "projects" / "keep_me"
    chats.mkdir(parents=True)
    (chats / "conversation.json").write_text("{}", encoding="utf-8")

    kids = appdata_children("tool_captures", root)
    assert kids["total"] == 2
    assert kids["items"][0]["name"] == "big.png"

    result = appdata_clear_caches(root)
    assert result["ok"] is True
    assert "tool_captures" in result["cleared"]
    assert not (cap / "big.png").exists()
    assert (chats / "conversation.json").is_file()


def test_project_delete_covers_memory_and_changesets(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "UEFN-Ducky"
    slug = "Island_abcd"
    for area in (
        "chats/projects",
        "workspace/projects",
        "file_history",
        "verse_diagnostics",
        "changesets",
        "memory/projects",
    ):
        d = root / Path(area) / slug
        d.mkdir(parents=True)
        (d / "x.bin").write_bytes(b"data")

    monkeypatch.setattr(
        "frontend.appdata_maintenance.load_recent_projects",
        lambda: [],
    )
    monkeypatch.setattr(
        "frontend.appdata_maintenance.PanelSettings.load",
        lambda: SimpleNamespace(uefn_project_root=""),
    )

    listed = appdata_projects(root)
    assert listed["projects"][0]["slug"] == slug
    assert listed["projects"][0]["bytes"] > 0
    assert len(listed["projects"][0]["areas"]) == 6

    assert delete_project_appdata(slug, root) == 6
    assert not (root / "chats" / "projects" / slug).exists()
    assert not (root / "changesets" / slug).exists()
    assert not (root / "memory" / "projects" / slug).exists()
