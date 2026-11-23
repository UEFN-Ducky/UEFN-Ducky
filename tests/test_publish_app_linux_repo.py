"""The release uploads every apt/dnf repo file under a name unique to that release, then
swaps the live repo with one uds_linux_repo call, before uds_app_release."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

RELEASE = Path(__file__).resolve().parents[1] / "release"
VERSION = "1.2.601"
REPO = (
    "uefn-ducky.gpg",
    "apt/dists/stable/Release",
    "apt/dists/stable/InRelease",
    "apt/dists/stable/Release.gpg",
    "apt/dists/stable/main/binary-amd64/Packages.gz",
    "apt/dists/stable/main/binary-arm64/Packages.gz",
    f"apt/pool/main/uefn-ducky_{VERSION}_amd64.deb",
    "rpm/repodata/repomd.xml",
    "rpm/repodata/repomd.xml.asc",
    "rpm/uefn-ducky.repo",
    f"rpm/uefn-ducky-{VERSION}-1.x86_64.rpm",
)


def _load():
    spec = importlib.util.spec_from_file_location("publish_app_repo_test", RELEASE / "publish_app.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path, files=REPO) -> Path:
    root = tmp_path / "linux-repo"
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(rel.encode())
    return root


class FakeStore:
    def __init__(self, fail_on: str = "", reply: dict | None = None) -> None:
        self.fail_on, self.reply, self.uploaded, self.calls = fail_on, reply, [], []

    def upload(self, base, key, path: Path) -> dict:
        if path.name.endswith(self.fail_on) and self.fail_on:
            raise SystemExit(f"Presigned PUT HTTP 500: {path.name}")
        self.uploaded.append(path.name)
        return {"fileId": f"id{len(self.uploaded)}", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def mcp(self, base, key, name, arguments) -> dict:
        self.calls.append((name, arguments))
        return self.reply if self.reply is not None else {"ok": True, "files": len(arguments.get("files", {})),
                                                          "version": arguments.get("version")}


def _wire(pub, store):
    pub.upload_app_release, pub.mcp_call = store.upload, store.mcp


def test_one_swap_with_every_path_and_unique_names(tmp_path):
    pub, store = _load(), FakeStore()
    _wire(pub, store)
    pub.publish_linux_repo("https://uefnducky.org", "k", VERSION, _repo(tmp_path))
    [(name, args)] = store.calls
    assert name == "uds_linux_repo" and args["version"] == VERSION
    assert set(args["files"]) == set(REPO)
    assert len(set(args["files"].values())) == len(REPO)
    assert len(set(store.uploaded)) == len(REPO)
    assert "linux-repo-1.2.601-apt_dists_stable_Release" in store.uploaded
    assert all(n.startswith(f"linux-repo-{VERSION}-") and "/" not in n for n in store.uploaded)


def test_a_failed_upload_swaps_nothing(tmp_path):
    pub, store = _load(), FakeStore(fail_on="rpm_repodata_repomd.xml")
    _wire(pub, store)
    with pytest.raises(SystemExit):
        pub.publish_linux_repo("https://uefnducky.org", "k", VERSION, _repo(tmp_path))
    assert store.calls == []


def test_a_refused_swap_stops_the_release(tmp_path):
    pub, store = _load(), FakeStore(reply={"ok": False, "error": "missing uefn-ducky.gpg"})
    _wire(pub, store)
    with pytest.raises(SystemExit, match="uds_linux_repo"):
        pub.publish_linux_repo("https://uefnducky.org", "k", VERSION, _repo(tmp_path))


def test_a_windows_only_release_skips_it(tmp_path):
    pub, store = _load(), FakeStore()
    _wire(pub, store)
    assert pub.publish_linux_repo("https://uefnducky.org", "k", VERSION, tmp_path / "missing") is None
    assert store.calls == [] and store.uploaded == []


def test_bad_paths_and_a_missing_key_are_refused(tmp_path):
    pub = _load()
    with pytest.raises(SystemExit, match="uefn-ducky.gpg"):
        pub.linux_repo_files(_repo(tmp_path / "a", REPO[1:]))
    with pytest.raises(SystemExit, match="segment"):
        pub.linux_repo_files(_repo(tmp_path / "b", REPO + ("apt/pool/main/bad name.deb",)))


def test_next_version():
    pub = _load()
    assert pub.next_version("1.2.600", None) == "1.2.601"
    assert pub.next_version("1.2.600", "1.2.610") == "1.2.611"
    assert pub.next_version("1.2.600", "1.2.590") == "1.2.601"
    assert pub.next_version("1.2.600", "1.2.610", "1.3.0") == "1.3.0"


def test_minisig_is_picked_up(tmp_path):
    pub = _load()
    deb = tmp_path / f"uefn-ducky_{VERSION}_amd64.deb"
    deb.write_bytes(b"x")
    (tmp_path / f"{deb.name}.minisig").write_text("sig")
    [art] = pub.linux_artifacts(VERSION, tmp_path)
    assert art["sig"].name.endswith(".minisig")
