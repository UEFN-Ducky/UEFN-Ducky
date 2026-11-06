"""Path rules shared by every write path."""

from __future__ import annotations

import pytest

from backend.workspace import paths


def test_normalize_rel_strips_prefixes_and_flips_slashes() -> None:
    assert paths.normalize_rel("  .\\Content\\Verse\\a.verse ") == "Content/Verse/a.verse"
    assert paths.normalize_rel("/Content/x") == "Content/x"
    assert paths.normalize_rel("") == ""


def test_content_hash_is_16_hex_chars_and_stable() -> None:
    h = paths.content_hash("hello\n")
    assert len(h) == paths.HASH_HEX_CHARS
    assert h == paths.content_hash("hello\n")
    assert h != paths.content_hash("hello")


def test_same_project_roots_treats_content_as_the_island(tmp_path) -> None:
    island = tmp_path / "ExampleProject1"
    content = island / "Content"
    content.mkdir(parents=True)
    other = tmp_path / "OtherProject" / "Content"
    other.mkdir(parents=True)
    assert paths.same_project_roots(str(content), str(island))
    assert paths.same_project_roots(str(island), str(content))
    assert paths.same_project_roots(str(island), str(island))
    assert not paths.same_project_roots(str(content), str(other))
    assert not paths.same_project_roots(str(island), str(tmp_path / "OtherProject"))
    assert not paths.same_project_roots("", str(island))


def test_rel_from_root(tmp_path) -> None:
    root = tmp_path / "Proj"
    inside = root / "Content" / "Verse" / "a.verse"
    assert paths.rel_from_root(str(inside), str(root)) == "Content/Verse/a.verse"
    assert paths.rel_from_root(str(tmp_path / "Other" / "b"), str(root)) is None


def test_line_delta_counts_insert_delete_replace() -> None:
    assert paths.line_delta("a\nb\n", "a\nb\nc\n") == (1, 0)
    assert paths.line_delta("a\nb\n", "a\n") == (0, 1)
    assert paths.line_delta("a\nb\n", "a\nx\n") == (1, 1)
    assert paths.line_delta("", "") == (0, 0)


@pytest.mark.parametrize(
    "path",
    [
        "Fortnite.digest.verse",
        r"C:\x\Saved\VerseProject\P\Digests\Verse.digest.verse",
        "Content/Verse/my_digest_notes.verse",
    ],
)
def test_digest_paths_are_refused(path: str) -> None:
    assert paths.is_uefn_digest_path(path)
    with pytest.raises(ValueError):
        paths.require_not_digest_path(path)


def test_non_digest_verse_is_allowed() -> None:
    paths.require_not_digest_path("Content/Verse/Shop/shop.verse")


def test_writable_project_path_rules() -> None:
    paths.require_writable_project_path(r"C:\P\Content\Verse\a.verse")
    paths.require_writable_project_path(r"C:\P\.ducky\tests\x.json")
    for bad in (
        r"C:\P\Saved\a.txt",
        r"C:\P\Intermediate\a.txt",
        r"C:\P\P.uproject",
        r"C:\P\Content",
        r"C:\P\Content\Python\evil.py",
    ):
        with pytest.raises(ValueError):
            paths.require_writable_project_path(bad)


windows_only = pytest.mark.skipif(paths.os.name != "nt", reason="Windows realpath race")


def _racing_realpath(monkeypatch, target: str, answer: str) -> None:
    """realpath of ``target`` answers like it does while another thread replaces it."""
    real = paths.os.path.realpath

    def fake(p, *a, **k):
        if paths.os.path.normcase(paths.os.path.abspath(p)) == paths.os.path.normcase(target):
            return answer
        return real(p, *a, **k)

    monkeypatch.setattr(paths.os.path, "realpath", fake)


@windows_only
def test_a_file_being_replaced_stays_inside_its_project(tmp_path, monkeypatch) -> None:
    root = paths.os.path.realpath(tmp_path)
    target = paths.os.path.join(root, "Content", "a.txt")
    _racing_realpath(monkeypatch, target, "C:\\$Extend\\$Deleted\\00190000006F8D9D5AC8A5A7")
    assert paths.real_path(target) == target
    assert paths.rel_from_root(target, root) == "Content/a.txt"


@windows_only
def test_a_kept_long_path_prefix_stays_inside_its_project(tmp_path, monkeypatch) -> None:
    root = paths.os.path.realpath(tmp_path)
    target = paths.os.path.join(root, "Content", "a.txt")
    _racing_realpath(monkeypatch, target, "\\\\?\\" + target)
    assert paths.rel_from_root(target, root) == "Content/a.txt"


def test_a_path_outside_the_root_is_still_outside(tmp_path) -> None:
    (tmp_path / "inner").mkdir()
    assert paths.rel_from_root(str(tmp_path / "x.txt"), str(tmp_path / "inner")) is None
