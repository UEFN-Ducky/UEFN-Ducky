"""The updater picks the feed asset for this OS, CPU and install kind."""

from __future__ import annotations

import frontend.updater as updater

SHA = "a" * 64


def _row(os_name: str, arch: str, package: str) -> dict:
    return {"os": os_name, "arch": arch, "package": package,
            "url": f"/api/v1/files/{os_name}-{arch}-{package}", "sha256": SHA, "sig_url": None}


FEED = {
    "currentVersion": "9.9.9",
    "installerUrl": "https://example.org/Setup-old.exe",
    "installerSha256": "b" * 64,
    "assets": [
        _row("windows", "x64", "setup"),
        _row("linux", "x64", "appimage"),
        _row("linux", "arm64", "appimage"),
        _row("linux", "x64", "deb"),
        _row("linux", "x64", "rpm"),
        _row("linux", "x64", "flatpak"),
        _row("linux", "x64", "aur"),
    ],
}


def _pick(machine: str, kind: str, plat: str = "linux", feed: dict = FEED) -> dict | None:
    return updater.pick_asset(feed, plat=plat, machine=machine, kind=kind)


def test_each_linux_kind_gets_its_own_package() -> None:
    for kind in ("appimage", "deb", "rpm", "flatpak", "aur"):
        row = _pick("x86_64", kind)
        assert row is not None and (row["os"], row["arch"], row["package"]) == ("linux", "x64", kind)


def test_appimage_on_arm64_picks_linux_arm64_appimage() -> None:
    assert _pick("aarch64", "appimage")["url"].endswith("linux-arm64-appimage")


def test_no_row_for_this_cpu_or_a_source_checkout_gets_nothing() -> None:
    assert _pick("aarch64", "deb") is None
    assert _pick("x86_64", "source") is None


def test_windows_installer_uses_the_setup_asset() -> None:
    row = _pick("AMD64", "installer", plat="win32")
    assert row["url"].endswith("windows-x64-setup")


def test_an_old_record_without_assets_still_gives_windows_its_setup() -> None:
    old = {k: v for k, v in FEED.items() if k != "assets"}
    row = _pick("AMD64", "installer", plat="win32", feed=old)
    assert row == {"os": "windows", "arch": "x64", "package": "setup",
                   "url": "https://example.org/Setup-old.exe", "sha256": "b" * 64, "sig_url": None}
    # The old fields are always the Windows Setup: Linux never takes them.
    assert _pick("x86_64", "appimage", feed=old) is None


def test_linux_status_offers_the_picked_asset(monkeypatch) -> None:
    import frontend.version_check as vc

    import platform

    monkeypatch.setattr(vc, "update_base_url", lambda: "https://store.example")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(updater, "_os_name", lambda plat=None: "linux")
    status = updater.linux_update_status(payload=FEED, kind="appimage")
    assert status["update_available"] and status["update_action"] == "restart"
    assert status["installer_url"] == "https://store.example/api/v1/files/linux-x64-appimage"
    assert status["installer_sha256"] == SHA
    deb = updater.linux_update_status(payload=FEED, kind="deb")
    assert deb["update_action"] == "install" and deb["channel"] == "installed"
    src = updater.linux_update_status(payload=FEED, kind="source")
    assert not src["update_available"] and src["update_action"] == "none"


ARM_FEED = {**FEED, "assets": [*FEED["assets"], _row("windows", "arm64", "setup")]}


def test_an_arm64_pc_takes_the_arm64_setup_even_from_an_emulated_x64_copy() -> None:
    assert updater.windows_setup(ARM_FEED, machine="ARM64")["url"].endswith("windows-arm64-setup")
    assert updater.windows_setup(ARM_FEED, machine="AMD64")["url"].endswith("windows-x64-setup")
    # a release without an arm64 Setup: the x64 one, which runs emulated on arm64
    assert updater.windows_setup(FEED, machine="ARM64")["url"].endswith("windows-x64-setup")
    old = {k: v for k, v in FEED.items() if k != "assets"}
    assert updater.windows_setup(old, machine="ARM64")["url"] == "https://example.org/Setup-old.exe"


def test_native_machine_is_a_cpu_name() -> None:
    assert updater.native_machine().upper() in ("AMD64", "X86_64", "ARM64", "AARCH64")


def test_the_x64_update_pack_never_reaches_an_arm64_pc(monkeypatch) -> None:
    import platform
    import sys

    import frontend.install_info as install_info
    import frontend.patch_notes_cache as notes
    import frontend.version_check as vc

    feed = {**ARM_FEED, "update": {"version": "9.9.9", "packUrl": "p", "manifestUrl": "m", "manifestSha256": "s"}}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(install_info, "get_install_info",
                        lambda: {"installed": True, "install_location": "C:/x", "install_scope": "user"})
    monkeypatch.setattr(vc, "update_channel", lambda installed: "installed")
    monkeypatch.setattr(vc, "is_packaged_runtime", lambda: True)
    monkeypatch.setattr(vc, "fetch_remote_payload", lambda *a, **k: (feed, None))
    monkeypatch.setattr(vc, "update_base_url", lambda: "https://store.example")
    monkeypatch.setattr(vc, "_background_update_stuck", lambda: False)
    monkeypatch.setattr(notes, "put", lambda *a, **k: None)
    for machine, native, pack, setup in (("AMD64", "AMD64", True, "x64"), ("AMD64", "ARM64", False, "arm64"),
                                         ("ARM64", "ARM64", False, "arm64")):
        monkeypatch.setattr(platform, "machine", lambda m=machine: m)
        monkeypatch.setattr(updater, "native_machine", lambda n=native: n)
        status = vc._app_update_status()
        assert status["update_available"], status
        assert status["update_pack"] is pack and status["installer_url"].endswith(f"windows-{setup}-setup")
