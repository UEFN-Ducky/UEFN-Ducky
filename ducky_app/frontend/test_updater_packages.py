"""deb / rpm installs download, check and install the new package through PackageKit;
Flatpak installs hand the update to flatpak."""

from __future__ import annotations

import hashlib
from pathlib import Path

import frontend.updater as updater

PACKAGE = b"new uefn-ducky package"


class FakeManager:
    def __init__(self, code: int = 0, output: str = "") -> None:
        self.code, self.output, self.calls = code, output, []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        return self.code, self.output


def _status(kind: str, asset: dict | None = None) -> dict:
    return {"update_available": True, "install_kind": kind, "remote_version": "9.9.9",
            "download_url": "https://uefnducky.org/download", "asset": asset}


def _asset(sha256: str = hashlib.sha256(PACKAGE).hexdigest()) -> dict:
    return {"url": "https://uefnducky.org/api/v1/files/pkg/content", "sha256": sha256, "sig_url": None}


def _serve(body: bytes = PACKAGE):
    def download(url: str, dest: Path) -> None:
        dest.write_bytes(body)
    return download


class SeesFile(FakeManager):
    """Records what the package file held while pkcon ran (it is deleted afterwards)."""

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.seen = Path(cmd[-1]).read_bytes()
        return super().__call__(cmd)


def test_deb_and_rpm_install_the_downloaded_package_through_packagekit(monkeypatch) -> None:
    # install.sh copies have no apt/dnf repo: `pkcon update` said "No packages were found".
    monkeypatch.setattr(updater, "MINISIGN_PUBLIC_KEY", "")
    for kind in ("deb", "rpm"):
        fake = SeesFile()
        assert updater.install_package_update(kind, _asset(), run=fake, download=_serve()) == {
            "ok": True, "error": None, "stage": "installed"}
        [cmd] = fake.calls
        assert cmd[:4] == ["pkcon", "-y", "--allow-untrusted", "install-local"]
        assert cmd[4].endswith(f"uefn-ducky.{kind}") and fake.seen == PACKAGE
        assert not Path(cmd[4]).exists()


def test_apply_linux_update_sends_deb_and_rpm_to_the_package_install() -> None:
    for kind in ("deb", "rpm"):
        fake = FakeManager()
        out = updater.apply_linux_update(status=_status(kind), run=fake)
        assert not out["ok"] and "install.sh" in out["error"] and fake.calls == []


def test_a_package_that_fails_its_checks_never_reaches_packagekit(monkeypatch) -> None:
    monkeypatch.setattr(updater, "MINISIGN_PUBLIC_KEY", "")
    fake = FakeManager()
    bad_sum = updater.install_package_update("deb", _asset("0" * 64), run=fake, download=_serve())
    assert not bad_sum["ok"] and bad_sum["stage"] == "verify"
    plain = updater.install_package_update("deb", {**_asset(), "url": "http://evil.example/x.deb"},
                                           run=fake, download=_serve())
    assert not plain["ok"] and plain["stage"] == "check"
    monkeypatch.setattr(updater, "MINISIGN_PUBLIC_KEY", "RWSWMIRwnnKIy4h1XdDWMtouy9laP5uw5K1gGe3HVNh5DFIWT7woHBvM")
    unsigned = updater.install_package_update("rpm", _asset(), run=fake, download=_serve())
    assert not unsigned["ok"] and "signature" in unsigned["error"]
    assert fake.calls == []


def test_flatpak_updates_its_own_app_on_the_host(monkeypatch) -> None:
    monkeypatch.setenv("FLATPAK_ID", "org.uefnducky.Ducky")
    fake = FakeManager()
    assert updater.apply_linux_update(status=_status("flatpak"), run=fake)["ok"]
    assert fake.calls == [["flatpak-spawn", "--host", "flatpak", "update", "-y", "--noninteractive",
                           "org.uefnducky.Ducky"]]


def test_a_missing_or_failing_package_manager_says_so(monkeypatch) -> None:
    monkeypatch.setattr(updater, "MINISIGN_PUBLIC_KEY", "")
    missing = updater.install_package_update("deb", _asset(), run=FakeManager(code=127), download=_serve())
    assert not missing["ok"] and "PackageKit" in missing["error"] and "install.sh" in missing["error"]
    no_daemon = updater.install_package_update(
        "deb", _asset(), run=FakeManager(output="Failed to contact PackageKit: Could not connect"),
        download=_serve())
    assert not no_daemon["ok"] and "install.sh" in no_daemon["error"]
    failed = updater.install_package_update("rpm", _asset(), run=FakeManager(code=4, output="x\nAuth failed"),
                                            download=_serve())
    assert not failed["ok"] and failed["error"].endswith("Auth failed")
    no_id = updater.hand_off_to_package_manager("flatpak", run=FakeManager(), env={})
    assert not no_id["ok"]


def test_aur_and_source_never_touch_files() -> None:
    fake = FakeManager()
    assert "yay" in updater.apply_linux_update(status=_status("aur"), run=fake)["error"]
    assert not updater.apply_linux_update(status=_status("source"), run=fake)["ok"]
    assert fake.calls == []
