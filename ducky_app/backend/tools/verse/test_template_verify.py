"""Tests for verse_template_verify helpers (no UEFN needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tools.verse import template_verify as tv


def test_parse_compile_message_maps_errors_to_staged_files(tmp_path: Path) -> None:
    staged = tmp_path / "Economy" / "economy_manager.verse"
    written = {str(staged.resolve()).lower().replace("\\", "/"): "economy:Economy/economy_manager.verse"}
    msg = (
        f"{staged.as_posix()}(44,21, 44,21) : Script error 3100: vErr:S88: Expected expression\n"
        f"{staged.as_posix()}(70,54, 70,65) : Script warning 2304: something experimental\n"
        f"{(tmp_path / 'Other' / 'x.verse').as_posix()}(1,1, 1,2) : Script error 3588: Ambiguous identifier\n"
    )
    per_file, other = tv.parse_compile_message(msg, written)
    assert list(per_file) == ["economy:Economy/economy_manager.verse"]
    kinds = [e["kind"] for e in per_file["economy:Economy/economy_manager.verse"]]
    assert kinds == ["error", "warning"]
    assert per_file["economy:Economy/economy_manager.verse"][0]["code"] == "3100"
    assert len(other) == 1 and "3588" in other[0]


def test_stage_never_overwrites_and_cleanup_removes_only_ours(tmp_path: Path) -> None:
    from backend.workspace import runtime
    from backend.workspace.writer import ProjectWriter

    runtime.reset_for_tests(ProjectWriter.for_root(str(tmp_path)))
    try:
        _stage_and_cleanup(tmp_path)
    finally:
        runtime.reset_for_tests(None)


def test_stage_refuses_a_root_the_pipeline_does_not_own(tmp_path: Path) -> None:
    from backend.workspace import runtime
    from backend.workspace.writer import ProjectWriter

    other = tmp_path / "Other"
    other.mkdir()
    runtime.reset_for_tests(ProjectWriter.for_root(str(other)))
    try:
        verse_root = tmp_path / "Proj" / "Content" / "Verse"
        verse_root.mkdir(parents=True)
        with pytest.raises(ValueError, match="not under the write pipeline root"):
            tv._stage(verse_root, [{"id": "x", "folder": "X", "files": [{"path": "a.verse", "content": "# a"}]}])
        assert not (verse_root / "X").exists()
    finally:
        runtime.reset_for_tests(None)


def _stage_and_cleanup(tmp_path: Path) -> None:
    verse_root = tmp_path / "Content" / "Verse"
    (verse_root / "Existing").mkdir(parents=True)
    (verse_root / "Existing" / "keep.verse").write_text("# keep", encoding="utf-8")
    rows = [
        {"id": "existing", "folder": "Existing", "files": [{"path": "keep.verse", "content": "# overwrite?"}]},
        {"id": "pack", "folder": "NewPack", "files": [{"path": "a.verse", "content": "# a"}, {"path": "sub/b.verse", "content": "# b"}]},
        {"id": "single", "file": "templates/single.verse", "content": "# s"},
    ]
    written, files, dirs, skipped = tv._stage(verse_root, rows)
    assert (verse_root / "Existing" / "keep.verse").read_text(encoding="utf-8") == "# keep"
    assert any("already exists" in s for s in skipped)
    assert len(written) == 3
    assert (verse_root / tv.SINGLES_FOLDER / "single.verse").is_file()
    tv._cleanup(verse_root, files, dirs)
    assert not (verse_root / "NewPack").exists()
    assert not (verse_root / tv.SINGLES_FOLDER).exists()
    assert (verse_root / "Existing" / "keep.verse").is_file()
