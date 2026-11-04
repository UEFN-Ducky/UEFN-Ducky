"""verse_cleanup: what a revert tidies after it deletes a created file."""

from __future__ import annotations

from pathlib import Path

from backend.workspace.verse_cleanup import prune_empty_parents, revert_uefn_for_deleted_verse


def test_deleted_verse_device_asks_uefn_to_drop_the_class(monkeypatch) -> None:
    posted: list[tuple[str, dict, str]] = []
    monkeypatch.setattr(
        "backend.workspace.plugin_revert.post_inverse_step",
        lambda cmd, params, program="uefn": posted.append((cmd, dict(params), program)),
    )

    revert_uefn_for_deleted_verse(
        "Content/Verse/SnakeGame/game.verse",
        "class snake_game_device:\n",
        r"C:\islands\ExampleProject1",
    )
    ids = {(p[1].get("kind"), p[1].get("id")) for p in posted}
    assert ("asset", "/ExampleProject1/Verse/SnakeGame/snake_game_device") in ids
    assert ("actor", "snake_game_device") in ids


def test_a_file_with_no_device_class_asks_nothing(monkeypatch) -> None:
    posted: list[str] = []
    monkeypatch.setattr(
        "backend.workspace.plugin_revert.post_inverse_step",
        lambda cmd, params, program="uefn": posted.append(cmd),
    )
    revert_uefn_for_deleted_verse("Content/Verse/a.verse", "x := 1\n", r"C:\islands\P")
    assert posted == []


def test_prune_stops_at_content_verse(tmp_path: Path) -> None:
    verse = tmp_path / "Content" / "Verse"
    deep = verse / "Game" / "Sub"
    deep.mkdir(parents=True)
    prune_empty_parents(str(deep / "gone.verse"), str(tmp_path))
    assert not (verse / "Game").exists()
    assert verse.is_dir()
