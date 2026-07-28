"""Project content-mount path normalization (no Unreal)."""

from __future__ import annotations

import sys
from pathlib import Path

_LISTENER_ROOT = Path(__file__).resolve().parents[1]
if str(_LISTENER_ROOT) not in sys.path:
    sys.path.insert(0, str(_LISTENER_ROOT))

import listener.project_paths as pp  # noqa: E402


def test_rewrites_game_materials_to_project_mount(monkeypatch) -> None:
    monkeypatch.setattr(pp, "content_root", lambda: "/catland")
    assert pp.normalize_project_folder("/Game/Materials") == "/catland/Materials"
    assert pp.normalize_project_folder("/Game/BlockoutCity/Materials") == "/catland/BlockoutCity/Materials"
    assert pp.normalize_project_folder("") == "/catland/Materials"
    assert pp.normalize_project_folder("/Game") == "/catland"


def test_preserves_creative_catalog(monkeypatch) -> None:
    monkeypatch.setattr(pp, "content_root", lambda: "/catland")
    assert pp.normalize_project_folder("/Game/Creative/Devices") == "/Game/Creative/Devices"
    assert pp.normalize_project_asset_path("/Game/Creative/Foo") == "/Game/Creative/Foo"


def test_preserves_already_correct_mount(monkeypatch) -> None:
    monkeypatch.setattr(pp, "content_root", lambda: "/catland")
    assert (
        pp.normalize_project_folder("/catland/BlockoutCity/Materials")
        == "/catland/BlockoutCity/Materials"
    )


def test_game_root_project_keeps_game(monkeypatch) -> None:
    monkeypatch.setattr(pp, "content_root", lambda: "/Game")
    assert pp.normalize_project_folder("/Game/Materials") == "/Game/Materials"
