"""Tests for Store-hosted app version-check payload parsing."""

from __future__ import annotations

from frontend.version_check import (
    absolute_installer_url,
    is_remote_newer,
    parse_version_tuple,
    unwrap_collect_payload,
)


def test_parse_version_tuple() -> None:
    assert parse_version_tuple("1.0.441") == (1, 0, 441)
    assert parse_version_tuple("bad") is None


def test_is_remote_newer() -> None:
    assert is_remote_newer("1.0.440", "1.0.441") is True
    assert is_remote_newer("1.0.441", "1.0.441") is False
    assert is_remote_newer("1.0.442", "1.0.441") is False


def test_unwrap_collect_payload_nested() -> None:
    envelope = {
        "ok": True,
        "payload": {
            "handled": True,
            "payload": {
                "currentVersion": "1.0.500",
                "installerUrl": "/api/files/x/content",
                "installerSha256": "abc",
                "releaseNotes": "notes",
            },
        },
    }
    # Host wraps plugin {handled,payload}; client may see either depth.
    # Our unwrap prefers the innermost object that has currentVersion.
    outer = envelope["payload"]
    assert unwrap_collect_payload({"payload": outer})["currentVersion"] == "1.0.500"
    assert unwrap_collect_payload(outer)["currentVersion"] == "1.0.500"
    assert unwrap_collect_payload(outer["payload"])["currentVersion"] == "1.0.500"


def test_unwrap_flat_legacy() -> None:
    flat = {"currentVersion": "1.0.1", "installerUrl": "https://example/setup.exe"}
    assert unwrap_collect_payload(flat)["currentVersion"] == "1.0.1"


def test_absolute_installer_url() -> None:
    base = "https://uefnducky.org"
    assert absolute_installer_url("/api/files/abc/content", base_url=base) == (
        "https://uefnducky.org/api/files/abc/content"
    )
    assert (
        absolute_installer_url("https://cdn.example/setup.exe", base_url=base)
        == "https://cdn.example/setup.exe"
    )
    assert absolute_installer_url(None, base_url=base) is None


def test_update_channel_dev_blocks_store() -> None:
    from frontend.version_check import update_channel

    # Portable / installed classification without needing a frozen Dev EXE.
    assert update_channel(installed=True) in ("installed", "dev")
    assert update_channel(installed=False) in ("portable", "dev")


if __name__ == "__main__":
    test_parse_version_tuple()
    test_is_remote_newer()
    test_unwrap_collect_payload_nested()
    test_unwrap_flat_legacy()
    test_absolute_installer_url()
    test_update_channel_dev_blocks_store()
    print("ok")
