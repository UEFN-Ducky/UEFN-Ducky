"""Lore drop host — pending-add missing files never CheckIn / lstat."""

from __future__ import annotations

from pathlib import Path

from frontend.ui_web.verse_editor.urc import lore_host


class FakeBackend:
    def __init__(self) -> None:
        self.dropped: list[list[str]] = []
        self.status_paths: list[str] = []
        self.untracked: list[str] = []
        self.checkin = 0
        self.lstat = 0

    def drop(self, project_root: str, paths: list[str]) -> dict:
        assert project_root
        self.dropped.append(list(paths))
        return {"ok": True, "dropped": list(paths), "unstage_rc": 0, "reset_rc": 0}

    def revert_local(self, project_root: str) -> dict:
        return {"ok": True, "skipped": "fake"}

    def status(self, project_root: str) -> dict:
        return {
            "ok": True,
            "paths": list(self.status_paths),
            "staged": list(self.status_paths),
            "untracked": list(self.untracked),
        }


def test_no_lore_is_noop(tmp_path: Path) -> None:
    lore_host.set_backend(FakeBackend())
    try:
        out = lore_host.drop_paths(str(tmp_path), ["Content/Foo.uasset"])
        assert out["skipped"] == "no_lore"
        assert out["dropped"] == []
    finally:
        lore_host.set_backend(None)


def test_pending_add_missing_drops_without_checkin(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    fake = FakeBackend()
    lore_host.set_backend(fake)
    try:
        out = lore_host.drop_paths(
            str(tmp_path),
            ["Content/Characters/BallerinaCappuccinaFixed/Animations/A_Ballerina_Idle_Fixed.uasset"],
        )
        assert out["ok"]
        assert "Idle_Fixed" in out["dropped"][0]
        assert fake.checkin == 0
        assert fake.lstat == 0
        assert fake.dropped
    finally:
        lore_host.set_backend(None)


def test_already_gone_asset_path_still_drops(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    fake = FakeBackend()
    fake.untracked = [
        "Content/Characters/BallerinaCappuccinaFixed/Animations/A_Ballerina_Idle_Fixed.uasset",
    ]
    lore_host.set_backend(fake)
    try:
        out = lore_host.after_asset_gone(
            str(tmp_path),
            "/ExampleProject1/Characters/BallerinaCappuccinaFixed/Animations/A_Ballerina_Idle_Fixed",
        )
        assert out["ok"]
        joined = " ".join(out["dropped"])
        assert "A_Ballerina_Idle_Fixed.uasset" in joined
    finally:
        lore_host.set_backend(None)


def test_orphan_scan_drops_missing_prefixed(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    fake = FakeBackend()
    fake.untracked = [
        "Content/Characters/BallerinaCappuccinaFixed/Animations/A_Ballerina_Idle_Fixed.uasset",
        "Content/Keep/Me.uasset",
    ]
    (tmp_path / "Content/Keep").mkdir(parents=True)
    (tmp_path / "Content/Keep/Me.uasset").write_bytes(b"x")
    lore_host.set_backend(fake)
    try:
        out = lore_host.drop_missing_orphans(
            str(tmp_path),
            prefixes=["Content/Characters/BallerinaCappuccinaFixed"],
        )
        assert out["ok"]
        assert any("Idle_Fixed" in p for p in out["dropped"])
        assert not any("Keep/Me" in p for p in out["dropped"])
    finally:
        lore_host.set_backend(None)


def test_orphan_scan_empty_status_does_not_reset_prefix(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    fake = FakeBackend()
    lore_host.set_backend(fake)
    try:
        out = lore_host.drop_missing_orphans(
            str(tmp_path),
            prefixes=["Content/Characters/BallerinaCappuccinaFixed"],
        )
        assert out["ok"]
        assert out["dropped"] == []
        assert fake.dropped == []
    finally:
        lore_host.set_backend(None)


def test_pending_delete_is_not_reset(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    fake = FakeBackend()
    fake.status_paths = [
        "Content/Characters/BallerinaCappuccinaFixed/Animations/A_Ballerina_Idle_Fixed.uasset",
    ]
    lore_host.set_backend(fake)
    try:
        out = lore_host.drop_missing_orphans(
            str(tmp_path),
            prefixes=["Content/Characters/BallerinaCappuccinaFixed"],
        )
        assert out["dropped"] == []
        assert fake.dropped == []
        gone = lore_host.after_asset_gone(
            str(tmp_path),
            "/ExampleProject1/Characters/BallerinaCappuccinaFixed/Animations/A_Ballerina_Idle_Fixed",
        )
        assert gone.get("dropped") == []
        assert gone.get("note") == "no_pending_add"
    finally:
        lore_host.set_backend(None)


def test_content_rel_from_asset_path() -> None:
    assert (
        lore_host.content_rel_from_asset_path(
            "/ExampleProject1/Characters/BallerinaCappuccina/MAT_BallerinaCappuccina"
        )
        == "Content/Characters/BallerinaCappuccina/MAT_BallerinaCappuccina.uasset"
    )


def test_hard_rules_ban_lore_homework() -> None:
    from backend.agent.hard_rules import AGENT_HARD_RULES

    assert "Never write or delete `.lore`" in AGENT_HARD_RULES
    assert "Revision Control" in AGENT_HARD_RULES
    assert "delete_project_folder" in AGENT_HARD_RULES
    assert "disk" in AGENT_HARD_RULES.lower()


def test_asset_delete_refuse_has_no_content_browser_homework() -> None:
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[4]
        / "uefn_listener"
        / "listener"
        / "asset_delete.py"
    ).read_text(encoding="utf-8")
    assert "Content Browser" not in text
    assert "delete_project_folder" in text
