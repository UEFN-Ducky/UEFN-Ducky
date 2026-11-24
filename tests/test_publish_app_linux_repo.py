"""The release uploads every apt/dnf repo file under a name unique to that release, then
swaps the live repo with one uds_linux_repo call, before uds_app_release."""

from __future__ import annotations

import hashlib
import importlib.util
import re
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


ARMORED = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQ\n-----END PGP PUBLIC KEY BLOCK-----\n"
SITE_REPO = ("[uefn-ducky]\nname=UEFN Ducky\nbaseurl=https://uefnducky.org/linux/rpm\nenabled=1\n"
             "gpgcheck=0\nrepo_gpgcheck=1\ngpgkey=https://uefnducky.org/linux/uefn-ducky.gpg\n")


def _repo(tmp_path: Path, files=REPO, content: dict | None = None) -> Path:
    root = tmp_path / "linux-repo"
    content = {"uefn-ducky.gpg": ARMORED, "rpm/uefn-ducky.repo": SITE_REPO.encode(), **(content or {})}
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(content.get(rel, rel.encode()))
    return root


class FakeStore:
    def __init__(self, fail_on: str = "", reply: dict | None = None) -> None:
        self.fail_on, self.reply, self.uploaded, self.calls = fail_on, reply, [], []

    def upload(self, base, key, path: Path) -> dict:
        # core's ticket checks `filename` with its identity rule: no `_`, at most 120.
        assert re.fullmatch(r"[A-Za-z0-9 .'-]{1,120}", path.name), path.name
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
    assert "linux-repo-1.2.601-apt-dists-stable-Release" in store.uploaded
    assert all(n.startswith(f"linux-repo-{VERSION}-") and "/" not in n for n in store.uploaded)


def test_a_failed_upload_swaps_nothing(tmp_path):
    pub, store = _load(), FakeStore(fail_on="rpm-repodata-repomd.xml")
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


def test_only_the_store_allow_list_passes(tmp_path):
    pub = _load()
    assert set(pub.linux_repo_files(_repo(tmp_path / "ok"))) == set(REPO)
    for stray in ("uefn-ducky.asc", "README", "deb/x.deb", "aptx/Release"):
        with pytest.raises(SystemExit, match="the Store takes only"):
            pub.linux_repo_files(_repo(tmp_path / stray.replace("/", "_"), REPO + (stray,)))


def test_the_key_must_be_armored(tmp_path):
    pub = _load()
    with pytest.raises(SystemExit, match="armored"):
        pub.linux_repo_files(_repo(tmp_path, content={"uefn-ducky.gpg": b"\x99\x01\x8d binary key"}))


@pytest.mark.parametrize("field,old", [("baseurl", "https://uefnducky.org/downloads/linux/rpm"),
                                       ("gpgkey", "https://uefnducky.org/downloads/linux/uefn-ducky.asc")])
def test_dnf_urls_must_match_the_site(tmp_path, field, old):
    pub = _load()
    good = dict(line.split("=", 1) for line in SITE_REPO.splitlines()[1:])
    bad = SITE_REPO.replace(f"{field}={good[field]}", f"{field}={old}")
    with pytest.raises(SystemExit, match=field):
        pub.linux_repo_files(_repo(tmp_path, content={"rpm/uefn-ducky.repo": bad.encode()}))


def test_the_shipped_repo_template_passes(tmp_path):
    pub = _load()
    template = (RELEASE.parent / "build" / "linux" / "rpm" / "uefn-ducky.repo").read_bytes()
    pub.linux_repo_files(_repo(tmp_path, content={"rpm/uefn-ducky.repo": template}))


def test_a_bad_repo_stops_the_release_before_any_upload():
    src = (RELEASE / "publish_app.py").read_text(encoding="utf-8")
    main = src[src.index("commit_and_push_before_publish(ROOT"):]
    assert main.index("linux_repo_files()") < main.index("upload_app_release(")


def test_next_version():
    pub = _load()
    assert pub.next_version("1.2.600", None) == "1.2.601"
    assert pub.next_version("1.2.600", "1.2.610") == "1.2.611"
    assert pub.next_version("1.2.600", "1.2.590") == "1.2.601"
    assert pub.next_version("1.2.600", "1.2.610", "1.3.0") == "1.3.0"


def test_print_next_version_is_alone_on_stdout(capsys):
    pub = _load()

    def noisy(base):
        print("  warn: could not read store version (offline)")
        return "1.2.700"

    pub.fetch_store_version, pub.read_version = noisy, lambda: "1.2.600"
    pub.print_next_version("")
    out = capsys.readouterr()
    assert out.out == "1.2.701\n" and "warn" in out.err
    pub.print_next_version("1.3.0")
    assert capsys.readouterr().out == "1.3.0\n"


def test_minisig_is_picked_up(tmp_path):
    pub = _load()
    deb = tmp_path / f"uefn-ducky_{VERSION}_amd64.deb"
    deb.write_bytes(b"x")
    (tmp_path / f"{deb.name}.minisig").write_text("sig")
    [art] = pub.linux_artifacts(VERSION, tmp_path)
    assert art["sig"].name.endswith(".minisig")
