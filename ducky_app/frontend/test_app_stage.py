"""A new version gets ready from only the files that changed, and only if it checks out."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
from pathlib import Path

import pytest

from frontend import app_stage, app_versions

REPO = Path(__file__).resolve().parents[2]
BASE = "https://uefnducky.org"


def _update_pack():
    spec = importlib.util.spec_from_file_location("update_pack_for_stage", REPO / "release" / "update_pack.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _app(root: Path, *, exe: bytes) -> Path:
    (root / "_internal" / "web").mkdir(parents=True)
    (root / "UEFN-Ducky.exe").write_bytes(exe)
    (root / "UEFN-Ducky-Bridge.exe").write_bytes(exe)
    (root / "_internal" / "python313.dll").write_bytes(b"dll" * 40_000)
    (root / "_internal" / "web" / "index.html").write_text("<html>same</html>" * 100, encoding="utf-8")
    return root


class _Server:
    """The Store's two files, with a count of what was read."""

    def __init__(self, pack: Path, manifest_zip: Path, *, ranges: bool = True):
        self.files = {"/pack": pack.read_bytes(), "/manifest": manifest_zip.read_bytes()}
        self.ranges = ranges
        self.range_bytes = 0
        self.whole = []

    @staticmethod
    def _path(url: str) -> str:
        return "/" + url.split("://", 1)[-1].split("/", 1)[-1]

    def fetch(self, url: str) -> bytes:
        path = self._path(url)
        self.whole.append(path)
        return self.files[path]

    def fetch_range(self, url: str, start: int, end: int) -> bytes | None:
        if not self.ranges:
            return None
        self.range_bytes += end - start
        return self.files[self._path(url)][start:end]


@pytest.fixture()
def release(tmp_path, monkeypatch):
    up = _update_pack()
    old = _app(tmp_path / "installed", exe=b"MZ old " + os.urandom(3000))
    new = _app(tmp_path / "build" / "UEFN-Ducky-1.2.412", exe=b"MZ new " + os.urandom(3000))
    (new / "_internal" / "web" / "added.js").write_text("new()", encoding="utf-8")
    pack, manifest_zip, manifest = up.build_update_pack(new, "1.2.412", tmp_path / "build")
    update = {
        "version": "1.2.412",
        "packUrl": "/pack",
        "manifestUrl": "/manifest",
        "manifestSha256": hashlib.sha256(manifest_zip.read_bytes()).hexdigest(),
        "signature": "c2ln",
        "issuedAt": 1_800_000_000,
    }
    monkeypatch.setattr(app_stage, "local_sources", lambda: [old])
    monkeypatch.setattr(app_stage, "signature_ok", lambda u, base_url: bool(u.get("signature")))
    monkeypatch.setattr(app_stage, "authenticode_ok", lambda exe: True)
    return {"old": old, "new": new, "pack": pack, "manifest_zip": manifest_zip, "manifest": manifest, "update": update}


def _serve(monkeypatch, server: _Server) -> None:
    monkeypatch.setattr(app_stage, "fetch", server.fetch)
    monkeypatch.setattr(app_stage, "fetch_range", server.fetch_range)


def test_only_changed_files_are_downloaded_and_the_version_turns_ready(release, monkeypatch) -> None:
    server = _Server(release["pack"], release["manifest_zip"])
    _serve(monkeypatch, server)
    monkeypatch.setattr(app_stage, "MERGE_GAP", 0)
    seen: list[tuple[int, int]] = []

    folder = app_stage.stage(release["update"], base_url=BASE, progress=lambda d, t: seen.append((d, t)))

    assert folder == app_versions.version_dir("1.2.412")
    assert app_versions.is_ready(folder)
    for entry in release["manifest"]["files"]:
        assert (folder / entry["path"]).read_bytes() == (release["new"] / entry["path"]).read_bytes()
    changed = [e for e in release["manifest"]["files"] if e["path"] in ("UEFN-Ducky.exe", "UEFN-Ducky-Bridge.exe", "_internal/web/added.js")]
    assert server.whole == ["/manifest"]
    # Exactly the changed files' bytes: the DLL and the page came from the installed copy.
    assert server.range_bytes == sum(e["csize"] for e in changed)
    assert seen[-1][0] == seen[-1][1]
    assert not app_versions.version_dir("1.2.412").with_name("app-1.2.412.partial").exists()
    # Already ready: nothing is fetched again.
    server.whole.clear()
    app_stage.stage(release["update"], base_url=BASE)
    assert server.whole == []


def test_a_server_without_ranges_still_works(release, monkeypatch) -> None:
    server = _Server(release["pack"], release["manifest_zip"], ranges=False)
    _serve(monkeypatch, server)
    folder = app_stage.stage(release["update"], base_url=BASE)
    assert app_versions.is_ready(folder)
    assert server.whole == ["/manifest", "/pack"]


def test_tampered_or_unsigned_updates_never_turn_ready(release, monkeypatch) -> None:
    server = _Server(release["pack"], release["manifest_zip"])
    _serve(monkeypatch, server)
    with pytest.raises(app_stage.StageError, match="signature"):
        app_stage.stage({**release["update"], "signature": ""}, base_url=BASE)
    with pytest.raises(app_stage.StageError, match="file list"):
        app_stage.stage({**release["update"], "manifestSha256": "0" * 64}, base_url=BASE)

    data = bytearray(server.files["/pack"])
    exe = next(e for e in release["manifest"]["files"] if e["path"] == "UEFN-Ducky.exe")
    data[exe["offset"] + 5] ^= 0xFF
    server.files["/pack"] = bytes(data)
    with pytest.raises(app_stage.StageError):
        app_stage.stage(release["update"], base_url=BASE)
    assert not app_versions.is_ready(app_versions.version_dir("1.2.412"))


def test_unsigned_exes_are_refused_except_from_a_local_rehearsal(release, monkeypatch) -> None:
    server = _Server(release["pack"], release["manifest_zip"])
    _serve(monkeypatch, server)
    monkeypatch.setattr(app_stage, "authenticode_ok", lambda exe: False)
    with pytest.raises(app_stage.StageError, match="not signed"):
        app_stage.stage(release["update"], base_url=BASE)
    folder = app_stage.stage(release["update"], base_url="http://127.0.0.1:8765")
    assert app_versions.is_ready(folder)


def test_feed_offers_only_newer_complete_packs() -> None:
    update = {"version": "1.2.412", "packUrl": "/p", "manifestUrl": "/m", "manifestSha256": "a" * 64}
    assert app_stage.feed_update({"update": update}, "1.2.410") == update
    assert app_stage.feed_update({"update": update}, "1.2.412") is None
    assert app_stage.feed_update({"update": {**update, "packUrl": ""}}, "1.2.410") is None
    assert app_stage.feed_update({"update": None}, "1.2.410") is None
    assert app_stage.feed_update(None, "1.2.410") is None


def test_the_signed_record_matches_the_stores_format() -> None:
    from backend.uefn_plugins.signing import canonical_message

    record = app_stage.signed_record({"version": "1.2.410", "manifestSha256": "B" * 64, "issuedAt": 1_800_000_000})
    assert canonical_message(record).decode() == (
        "compiled=false\nissued_at=1800000000\nsha256=" + "b" * 64 + "\nslug=uefn-ducky-app\nteam_id=\nversion=1.2.410\nvisibility=public"
    )


def test_manifest_paths_must_stay_inside_the_app_folder() -> None:
    for bad in ("../evil.exe", "C:/x.exe", "a\\b.exe", "/abs.exe", ""):
        assert not app_stage.safe_relative(bad)
    assert app_stage.safe_relative("_internal/web/index.html")


def test_prune_drops_the_old_per_version_folders(monkeypatch) -> None:
    """1.2.529 and older kept app-<v> per version; only a newer one (an update still to move
    into versions/app) and one a program runs from stay."""
    for version in ("1.2.405", "1.2.408", "1.2.410", "1.2.412"):
        folder = app_versions.versions_dir() / f"app-{version}"
        folder.mkdir(parents=True)
        (folder / "UEFN-Ducky.exe").write_bytes(b"MZ")
        (folder / app_versions.READY_MARKER).write_text("{}", encoding="utf-8")
    (app_versions.versions_dir() / "app-1.2.409.partial").mkdir()
    (app_versions.versions_dir() / "app-1.2.413.partial").mkdir()
    monkeypatch.setattr(app_stage, "in_use", lambda f: f.name == "app-1.2.410")
    removed = app_stage.prune(running="1.2.410")
    left = sorted(p.name for p in app_versions.versions_dir().iterdir())
    assert left == ["app-1.2.410", "app-1.2.412"]
    assert sorted(removed) == ["app-1.2.405", "app-1.2.408", "app-1.2.409.partial", "app-1.2.413.partial"]


def test_spans_merge_neighbours_and_split_far_ones() -> None:
    entries = [
        {"offset": 0, "csize": 100},
        {"offset": 150, "csize": 100},
        {"offset": 10_000_000, "csize": 50},
    ]
    groups = app_stage.spans(entries)
    assert [[e["offset"] for e in g] for g in groups] == [[0, 150], [10_000_000]]


@pytest.fixture(autouse=True)
def _clean_versions():
    yield
    shutil.rmtree(app_versions.versions_dir(), ignore_errors=True)
