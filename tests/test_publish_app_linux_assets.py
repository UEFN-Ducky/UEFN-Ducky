"""A release uploads every Linux package and signature, then registers them all in one
uds_app_release call; a failed upload registers nothing."""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

RELEASE = Path(__file__).resolve().parents[1] / "release"
VERSION = "1.2.600"
LINUX_FILES = (
    f"UEFN-Ducky-{VERSION}-x86_64.AppImage",
    f"UEFN-Ducky-{VERSION}-aarch64.AppImage",
    f"uefn-ducky_{VERSION}_amd64.deb",
    f"uefn-ducky-{VERSION}-1.x86_64.rpm",
    f"UEFN-Ducky-{VERSION}-x86_64.flatpak",
    f"uefn-ducky_{VERSION}_arm64.deb",
)


def _load():
    spec = importlib.util.spec_from_file_location("publish_app_linux_test", RELEASE / "publish_app.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _dist(tmp_path: Path) -> Path:
    folder = tmp_path / "linux"
    folder.mkdir()
    for name in LINUX_FILES:
        (folder / name).write_bytes(name.encode())
        (folder / f"{name}.sig").write_bytes(b"sig " + name.encode())
    (folder / "uefn-ducky_1.2.599_amd64.deb").write_bytes(b"old build")  # another version
    (folder / "notes.txt").write_text("not a package")
    return folder


class FakeStore:
    """Fake S3 + Store: uploads get a file id; tools/call records what was registered."""

    def __init__(self, fail_on: str = "") -> None:
        self.fail_on, self.uploaded, self.calls = fail_on, [], []

    def upload(self, base, key, path: Path) -> dict:
        # core's allowed_app_release_name: a plain .rpm/.AppImage/.sig is HTTP 400; its
        # identity rule on `filename`: no `_` (x86_64, foo_1.2_amd64.deb), at most 120.
        assert path.name.startswith("linux-repo-") or path.suffix in (".exe", ".zip", ".apk"), path.name
        assert re.fullmatch(r"[A-Za-z0-9 .'-]{1,120}", path.name), path.name
        name = path.name.removeprefix(f"linux-repo-{VERSION}-")
        if name == self.fail_on:
            raise SystemExit(f"Presigned PUT HTTP 500: {name}")
        self.uploaded.append(name)
        return {"fileId": f"id-{len(self.uploaded)}", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def mcp(self, base, key, name, arguments) -> dict:
        self.calls.append((name, arguments))
        return {"ok": True}


def _publish(pub, store: FakeStore, folder: Path) -> None:
    """main()'s upload → register order, against the fakes."""
    pub.upload_app_release, pub.mcp_call = store.upload, store.mcp
    assets = pub.release_assets("https://uefnducky.org", "k", VERSION, "/api/v1/files/win/content", "ab" * 32, folder)
    pub.mcp_call("https://uefnducky.org", "k", "uds_app_release", {"version": VERSION, **({"assets": assets} if assets else {})})


def test_one_call_registers_windows_and_every_linux_package(tmp_path):
    pub, store = _load(), FakeStore()
    _publish(pub, store, _dist(tmp_path))
    assert len(store.uploaded) == 12  # 6 packages + 6 signatures, the other version untouched
    assert "uefn-ducky_1.2.599_amd64.deb" not in store.uploaded
    [(name, args)] = store.calls
    assert name == "uds_app_release"
    assets = args["assets"]
    assert len(assets) == 7
    assert assets[0] == {"os": "windows", "arch": "x64", "package": "setup",
                         "url": "/api/v1/files/win/content", "sha256": "ab" * 32, "sig_url": ""}
    keys = {(a["os"], a["arch"], a["package"]) for a in assets}
    assert keys == {("windows", "x64", "setup"), ("linux", "x64", "appimage"), ("linux", "arm64", "appimage"),
                    ("linux", "x64", "deb"), ("linux", "arm64", "deb"), ("linux", "x64", "rpm"),
                    ("linux", "x64", "flatpak")}
    for a in assets[1:]:
        assert a["url"].startswith("/api/v1/files/id-") and a["sig_url"].startswith("/api/v1/files/id-")
        assert len(a["sha256"]) == 64


def test_a_failed_upload_registers_nothing(tmp_path):
    pub, store = _load(), FakeStore(fail_on=f"uefn-ducky-{VERSION}-1.x86-64.rpm")
    with pytest.raises(SystemExit):
        _publish(pub, store, _dist(tmp_path))
    assert store.calls == []


def test_a_failed_signature_upload_registers_nothing(tmp_path):
    pub, store = _load(), FakeStore(fail_on=f"uefn-ducky-{VERSION}-arm64.deb.sig")
    with pytest.raises(SystemExit):
        _publish(pub, store, _dist(tmp_path))
    assert store.calls == []


def test_a_windows_only_release_sends_no_assets(tmp_path):
    pub, store = _load(), FakeStore()
    _publish(pub, store, tmp_path / "missing")
    assert store.uploaded == [] and "assets" not in store.calls[0][1]


def test_a_package_without_an_arch_is_refused(tmp_path):
    pub = _load()
    (tmp_path / f"uefn-ducky_{VERSION}.deb").write_bytes(b"x")
    with pytest.raises(SystemExit, match="x86_64"):
        pub.linux_artifacts(VERSION, tmp_path)


def test_store_names_pass_the_identity_rule_and_stay_unique():
    pub = _load()
    assert pub.store_upload_name(VERSION, f"uefn-ducky_{VERSION}_amd64.deb") == \
        f"linux-repo-{VERSION}-uefn-ducky-{VERSION}-amd64.deb"
    a = pub.store_upload_name(VERSION, "apt/pool/main/" + "x" * 200 + "_amd64.deb")
    b = pub.store_upload_name(VERSION, "apt/pool/main/" + "x" * 200 + "_arm64.deb")
    assert len(a) == 120 and a != b and re.fullmatch(r"[A-Za-z0-9 .'-]+", a)
