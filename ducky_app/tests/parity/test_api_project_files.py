"""The Content pane's reads: the tree, a file's text or media URL, Quick Open's paths.
The same answer from both backends on an island and on a plain folder, hidden files
shown and not. Nothing here reads the person's own Documents or UEFN folders."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from backend.store.repos import projects, settings

from .parity_harness import plain

_BIG = 20_000  # files in the large folder project


def _put(root: Path, files: dict[str, bytes | str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode() if isinstance(body, str) else body)


_ISLAND = {
    "DuckyTestIsle.uefnproject": '{"title": "DuckyTestIsle"}',
    "Content/game.verse": "using { /Verse.org/Simulation }\n\nhello := 1\n",
    "Content/Sub/deep.verse": "a := 1\r\nb := 2\rc := 3\n",
    "Content/Sub/Inner/note.txt": "inner",
    "Content/bad_utf8.txt": b"caf\xe9 \xff ok\n",
    "Content/Map.umap": b"\x00umap\x00",
    "Content/Map_Default.uasset": b"\x00\x01",
    "Content/GameFeatureData.uasset": b"\x00\x02",
    "Content/Prop.uasset": b"\xc1\x83\x2a\x9e" + bytes(range(40)),
    "Content/__ExternalActors__/Map/A.uasset": b"\x00",
    "Content/Collections/c.collection": "x",
    "Content/picture.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 8,
    "Content/model.glb": b"glTF\x02\x00\x00\x00",
    "Content/sound.ogg": b"OggS\x00",
    "Content/clip.mp4": b"\x00\x00\x00\x18ftyp",
    "Content/blob.bin": b"\x00\x01\x02abc" * 9,
    "Content/LICENSE": "free",
    "Content/noext": "plain words\n",
    "Content/nuls": b"ab\x00cd",
    "Content/.gitignore": "*.tmp\n",
    "Content/.secret": "s",
    "Content/.vscode/settings.json": "{}",
    "Content/data.json": '{"a": 1}',
    "Content/Empty Folder/keep me.md": "# hi é\n",
    "Saved/log.txt": "log",
    "Plugins/readme.md": "outside Content",
}
_FOLDER = {
    "README.md": "# folder\n",
    "src/main.py": "print(1)\n",
    "src/pkg/mod.ts": "export {}\n",
    "node_modules/x/index.js": "x",
    ".git/config": "[core]",
    ".github/workflows/ci.yml": "on: push\n",
    ".env": "A=1",
    "Content/notes.md": "notes",
    "__pycache__/a.pyc": b"\x00",
    "build/out.exe": b"MZ\x00",
    "image.jpg": b"\xff\xd8\xff",
}
_DIGESTS = {
    "vproject/DuckyTestIsle.vproject": "{}",
    "Digests/BuiltIn/Verse/Verse.digest.verse": "# verse\n",
    "Digests/BuiltIn/UnrealEngine/UnrealEngine.digest.verse": "# ue\n",
    "Digests/BuiltIn/Fortnite/Fortnite.digest.verse": "# fn\n",
    "Digests/Assets.digest.verse": "# assets\n",
}


def _differs(ours: Any, theirs: Any, at: str = "") -> str:
    """Where two answers first differ, in a few words (an answer can be thousands of rows)."""
    if isinstance(ours, dict) and isinstance(theirs, dict) and list(ours) == list(theirs):
        return next((_differs(ours[key], theirs[key], f"{at}/{key}") for key in ours if ours[key] != theirs[key]), "")
    if isinstance(ours, dict) and isinstance(theirs, dict):
        return f"at {at}: keys only in rust {[k for k in ours if k not in theirs][:9]}, only in python {[k for k in theirs if k not in ours][:9]}"
    if isinstance(ours, list) and isinstance(theirs, list) and len(ours) == len(theirs):
        return next((_differs(a, b, f"{at}[{i}]") for i, (a, b) in enumerate(zip(ours, theirs)) if a != b), "")
    return f"at {at or 'the top'}:\n rust   {str(ours)[:600]}\n python {str(theirs)[:600]}"


@pytest.fixture
def places(tmp_path, monkeypatch):
    """The port both backends build media URLs with, set before the Rust one starts (it
    inherits it). ``rust_store`` points both at ``Documents`` in the same temp folder."""
    from frontend import settings as app_settings

    monkeypatch.setenv("UEFN_DUCKY_PANEL_PORT", str(app_settings.PANEL_LISTENER_PORT))
    return tmp_path


def _forget() -> None:
    """What Python remembers between calls; Rust keeps only Quick Open's index."""
    from frontend import project_kind
    from frontend.ui_web import known_projects
    from frontend.ui_web import project_files as pf

    known_projects.forget_cache()
    project_kind.forget_cached_kind()
    project_kind.forget_cached_identity()
    pf._workspace_folders_cache.clear()
    pf._file_paths_cache.clear()
    pf._last_fingerprints.clear()


@pytest.fixture
def site(places, rust_store, python):
    """An island with UEFN's Verse folders, a folder project, an island found in
    Documents and one the person removed. ``site.open(root, hidden)`` selects one."""
    work = places / "work"
    island, folder = work / "DuckyTestIsle", work / "Plain Folder"
    found = places / "Documents" / "Fortnite Projects" / "FoundIsle"
    removed = places / "Documents" / "Fortnite Projects" / "RemovedIsle"
    _put(island, _ISLAND)
    _put(folder, _FOLDER)
    _put(found, {"FoundIsle.uefnproject": "{}", "Content/found.verse": "f := 1\n"})
    _put(removed, {"RemovedIsle.uefnproject": "{}", "Content/gone.verse": "g := 1\n"})
    _put(places / "UnrealEditorFortnite" / "Saved" / "VerseProject" / "DuckyTestIsle", _DIGESTS)
    _put(places / "outside", {"alone.txt": "not in any project\n"})
    projects.touch(str(island), name="DuckyTestIsle", when=3.0, kind="uefn")
    projects.touch(str(folder), name="Plain Folder", when=2.0, kind="folder")
    projects.touch(str(removed), name="RemovedIsle", when=1.0, kind="uefn")
    projects.forget(str(removed))

    class Site:
        pass

    def changed() -> None:
        """Both forget what they keep. Rust keeps Quick Open's answer for a second, as
        Python's panel route does, until an event like this one."""
        _forget()
        assert rust_store.request("/__panel_event", {"type": "project_changed"})[0] == 204

    def open_(root: Path | str, hidden: bool = False) -> None:
        settings.replace_all({"uefn_project_root": str(root), "show_hidden_project_files": hidden}, {})
        changed()

    def both(method: str, *args: Any) -> Any:
        theirs, ours = python.call(method, *args), rust_store.call(method, *args)
        root = settings.load_fields().get("uefn_project_root")
        assert plain(ours) == plain(theirs), f"{method}{str(args)[:300]} in {root!r} {_differs(plain(ours), plain(theirs))}"
        return plain(theirs)

    def same(method: str, *args: Any) -> Any:
        status, reply = both(method, *args)
        assert status == 200 and reply["ok"] is True, f"{method}{args}: {reply}"
        return reply["result"]

    Site.island, Site.folder, Site.found, Site.outside = island, folder, found, places / "outside"
    Site.open, Site.both, Site.same = staticmethod(open_), staticmethod(both), staticmethod(same)
    Site.changed = staticmethod(changed)
    _forget()
    yield Site
    _forget()


def _walk(site: Any, path: str = "", depth: int = 0) -> list[str]:
    """Every path under ``path`` (the tree's roots by default), each folder listed by both
    backends."""
    seen: list[str] = []
    for row in site.same("list_project_files", path)["entries"]:
        seen.append(row["path"])
        if row.get("is_dir") and depth < 7:
            seen += _walk(site, row["path"], depth + 1)
    return seen


_PROJECTS = [("island", False), ("island", True), ("folder", False), ("folder", True)]


@pytest.mark.parametrize(("which", "hidden"), _PROJECTS)
def test_the_whole_tree_lists_the_same(site, which, hidden):
    site.open(getattr(site, which), hidden)
    roots = site.same("list_workspace_roots")
    assert roots, roots
    assert site.same("list_project_files") == site.same("list_project_files", "__roots__")
    seen = _walk(site)
    if which == "island":
        assert any(path.endswith("game.verse") for path in seen), seen
        assert any("Verse.digest.verse" in path for path in seen), seen  # UEFN's Verse folders
    else:
        assert "src/main.py" in seen and not any(".git/" in path for path in seen), seen
        assert any("node_modules" in path for path in seen) is hidden, seen
    for path in ("Content", ".", "", "Content/Sub", "Content\\Sub\\", "/Content/Sub/", "ws:0", "ws:1", "ws:99", "ws:x"):
        site.both("list_project_files", path)


@pytest.mark.parametrize(("which", "hidden"), _PROJECTS)
def test_every_file_reads_stats_and_classifies_the_same(site, which, hidden):
    site.open(getattr(site, which), hidden)
    seen = _walk(site)
    assert seen
    # What the tree hides can still be asked for by path.
    extra = ["Content/.secret", "Content/Map_Default.uasset", "Saved/log.txt", "Plugins/readme.md", ".git/config", ".env"]
    for path in seen + extra:
        for method in ("read_project_file", "stat_project_file", "project_file_media_url", "classify_project_file"):
            site.both(method, path)
    site.both("fingerprint_project_dirs", seen + extra + ["", ".", "Content", "missing", "ws:0", None])
    site.both("fingerprint_project_dirs", [])


@pytest.mark.parametrize("which", ["island", "folder"])
def test_paths_outside_the_project_get_the_same_answer(site, which):
    site.open(getattr(site, which))
    alone = str(site.outside / "alone.txt").replace("\\", "/")
    other = str(site.found / "Content" / "found.verse").replace("\\", "/")
    digest = [row["path"] for row in site.same("list_project_files")["entries"]]
    digest += [row["path"] for row in site.same("list_workspace_roots")]
    paths = [
        "", " ", "missing.txt", "Content/missing.verse", "../outside/alone.txt", "Content/../../outside/alone.txt",
        "Content/..", "..", "C:/Windows/win.ini", "/etc/passwd", alone, "abs:" + alone, "ext:" + alone,
        "abs:" + other, "ext:" + other, "abs:", "ext:", "abs:relative/path", "ext:relative.txt", "ws:0/nope", "ws:-1",
        "ext:" + str(site.outside).replace("\\", "/"), "abs:" + str(site.island / "Content" / "game.verse"),
        "ext:" + str(site.island / "Content" / "game.verse"), "Content/Sub", "CONTENT/GAME.VERSE", *digest,
    ]
    for path in paths:
        for method in ("list_project_files", "read_project_file", "stat_project_file", "project_file_media_url", "classify_project_file"):
            site.both(method, path)
    site.both("fingerprint_project_dirs", paths)


def test_no_project_and_a_missing_project_answer_the_same(site):
    for root in ("", "  ", str(site.outside / "nowhere"), str(site.island / "DuckyTestIsle.uefnproject"), str(site.outside)):
        site.open(root)
        site.both("list_workspace_roots")
        site.both("list_project_file_paths")
        site.both("list_project_file_paths", "a")
        for path in ("", "Content", ".", "alone.txt", "Content/game.verse"):
            for method in ("list_project_files", "read_project_file", "stat_project_file", "project_file_media_url"):
                site.both(method, path)
        site.both("fingerprint_project_dirs", ["Content", "."])


@pytest.mark.parametrize(("which", "hidden"), _PROJECTS)
def test_quick_open_finds_the_same_paths(site, which, hidden):
    site.open(getattr(site, which), hidden)
    everything = site.same("list_project_file_paths")
    assert everything["paths"] and everything["truncated"] is False
    for query in ("verse", "VERSE", "sub/deep", "main", "md", " note ", "zzz-no-such", "é", ".", "/", "digest", "src pkg"):
        for limit in (50, 1, 0, 3, "2", None):
            site.both("list_project_file_paths", query, limit)
    site.both("list_project_file_paths", "", 1)
    site.both("list_project_file_paths", None)


def test_a_change_on_disk_shows_in_both(site):
    site.open(site.island)
    before = site.same("list_project_file_paths")
    site.same("fingerprint_project_dirs", ["Content", "Content/Sub"])
    _put(site.island, {"Content/Sub/added.verse": "x := 1\n"})
    (site.island / "Content" / "noext").unlink()
    # NTFS brings a folder's time in its parent's listing up to date when the folder is
    # next opened: open it here, so the first backend asked does not see the old time.
    assert len(list((site.island / "Content" / "Sub").iterdir())) == 3
    site.same("fingerprint_project_dirs", ["Content", "Content/Sub"])
    site.changed()
    after = site.same("list_project_file_paths")
    assert after != before and "Content/Sub/added.verse" in after["paths"] and "Content/noext" not in after["paths"]
    assert site.same("stat_project_file", "Content/noext")["exists"] is False
    assert site.same("read_project_file", "Content/Sub/added.verse")["content"] == "x := 1\n"


def test_a_large_folder_lists_the_same_and_fast(site, places, rust_store):
    big = places / "work" / "Big"
    for folder in range(200):
        directory = big / f"dir{folder:03d}"
        directory.mkdir(parents=True)
        for index in range(_BIG // 200):
            (directory / f"file{index:03d}.txt").write_bytes(b"")
    _put(big, {"all/" + f"n{index:05d}.md": "" for index in range(3000)})
    projects.touch(str(big), name="Big", when=4.0, kind="folder")
    site.open(big)
    everything = site.same("list_project_file_paths")
    assert everything["total"] == _BIG + 3000 and everything["truncated"] is True and len(everything["paths"]) == 2000
    assert len(site.same("list_project_files", "all")["entries"]) == 3000
    site.same("list_project_files", ".")
    site.same("list_project_file_paths", "file199", 50)
    # ponytail: a loose bound over HTTP on a shared test machine; the 200 ms target is
    # for the listing itself, which the cached index answers in a few ms.
    started = time.perf_counter()
    rust_store.call("list_project_files", "all")
    rust_store.call("list_project_file_paths", "n02999", 50)
    assert time.perf_counter() - started < 2.0
