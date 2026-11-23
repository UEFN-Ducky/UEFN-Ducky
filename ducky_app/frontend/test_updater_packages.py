"""deb / rpm / Flatpak installs hand the update to their package manager."""

from __future__ import annotations

import frontend.updater as updater


class FakeManager:
    def __init__(self, code: int = 0, output: str = "") -> None:
        self.code, self.output, self.calls = code, output, []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        return self.code, self.output


def _status(kind: str) -> dict:
    return {"update_available": True, "install_kind": kind, "remote_version": "9.9.9",
            "download_url": "https://uefnducky.org/download", "asset": None}


def test_deb_and_rpm_install_through_packagekit() -> None:
    for kind in ("deb", "rpm"):
        fake = FakeManager()
        assert updater.apply_linux_update(status=_status(kind), run=fake) == {
            "ok": True, "error": None, "stage": "installed"}
        assert fake.calls == [["pkcon", "-y", "update", "uefn-ducky"]]


def test_flatpak_updates_its_own_app_on_the_host(monkeypatch) -> None:
    monkeypatch.setenv("FLATPAK_ID", "org.uefnducky.Ducky")
    fake = FakeManager()
    assert updater.apply_linux_update(status=_status("flatpak"), run=fake)["ok"]
    assert fake.calls == [["flatpak-spawn", "--host", "flatpak", "update", "-y", "--noninteractive",
                           "org.uefnducky.Ducky"]]


def test_a_missing_or_failing_package_manager_says_so() -> None:
    missing = updater.hand_off_to_package_manager("deb", run=FakeManager(code=127))
    assert not missing["ok"] and "PackageKit" in missing["error"]
    failed = updater.hand_off_to_package_manager("rpm", run=FakeManager(code=4, output="x\nAuth failed"))
    assert not failed["ok"] and failed["error"].endswith("Auth failed")
    no_id = updater.hand_off_to_package_manager("flatpak", run=FakeManager(), env={})
    assert not no_id["ok"]


def test_aur_and_source_never_touch_files() -> None:
    fake = FakeManager()
    assert "yay" in updater.apply_linux_update(status=_status("aur"), run=fake)["error"]
    assert not updater.apply_linux_update(status=_status("source"), run=fake)["ok"]
    assert fake.calls == []
