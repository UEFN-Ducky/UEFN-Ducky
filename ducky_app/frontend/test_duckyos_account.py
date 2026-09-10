"""PKCE helpers and Store zip hash gate for DuckyOS desktop login."""

from __future__ import annotations

import base64
import hashlib

from frontend.duckyos_account import pkce_pair


def test_pkce_pair_s256() -> None:
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected
    other, _ = pkce_pair()
    assert other != verifier


def test_auto_apply_store_updates_skips_local_and_unpaid() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    catalog = {
        "ok": True,
        "items": [
            {"slug": "openai", "kind": "plugin", "state": "update", "source": "store"},
            {"slug": "mine", "kind": "plugin", "state": "update", "source": "local"},
            {
                "slug": "paid-pack",
                "kind": "plugin",
                "state": "update",
                "source": "store",
                "paid": True,
                "owned": False,
            },
            {"slug": "fresh", "kind": "plugin", "state": "available", "source": "store"},
        ],
    }
    calls: list[str] = []

    def _install(slug: str, **_kw: object) -> dict:
        calls.append(slug)
        return {"ok": True, "slug": slug}

    with (
        patch.object(acc, "store_catalog", return_value=catalog),
        patch.object(acc, "store_download_and_install", side_effect=_install),
    ):
        out = acc.auto_apply_store_updates(force=True)
    assert out["ok"] is True
    assert out["updated"] == ["openai"]
    assert calls == ["openai"]


def test_store_item_versions_strips_empty_and_keeps_changelog() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    with patch.object(
        acc,
        "_store_collect",
        return_value={
            "versions": [
                {"version": "1.1.14", "changelog": "Bot tools.", "created_at": "2026-09-01T00:00:00Z"},
                {"version": "", "changelog": "skip me"},
                "nope",
            ]
        },
    ):
        out = acc.store_item_versions("discord")
    assert out["ok"] is True
    assert out["slug"] == "discord"
    assert out["versions"] == [
        {"version": "1.1.14", "changelog": "Bot tools.", "created_at": "2026-09-01T00:00:00Z"},
    ]


def test_store_item_versions_needs_slug() -> None:
    from frontend.duckyos_account import store_item_versions

    out = store_item_versions("  ")
    assert out["ok"] is False
    assert out["versions"] == []


if __name__ == "__main__":
    test_pkce_pair_s256()
    test_auto_apply_store_updates_skips_local_and_unpaid()
    test_store_item_versions_strips_empty_and_keeps_changelog()
    test_store_item_versions_needs_slug()
    print("ok")
