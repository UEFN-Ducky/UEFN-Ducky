"""A sealing pass that fails at sign-in leaves a warning, never the rows or keys."""

from __future__ import annotations

import logging

from backend.store import account_rows
from backend.uefn_plugins import data_crypto, scopes


class _NowThread:
    def __init__(self, target, **_kw) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def test_a_failed_seal_pass_is_logged_without_data(monkeypatch, caplog) -> None:
    fake = "row-secret-ADK-0123456789abcdef"

    def _boom(_account: str) -> None:
        raise RuntimeError(f"DPAPI unavailable: {fake}")

    monkeypatch.setattr(data_crypto.threading, "Thread", _NowThread)
    monkeypatch.setattr(scopes, "account_id", lambda: data_crypto._LOCAL)
    monkeypatch.setattr(data_crypto, "ensure_migrated", lambda _account: None)
    monkeypatch.setattr(account_rows, "seal_pending", _boom)
    with caplog.at_level(logging.WARNING, logger=data_crypto.__name__):
        data_crypto.prefetch()
    rows = [r for r in caplog.records if r.name == data_crypto.__name__]
    assert [r.getMessage() for r in rows] == ["sealing account rows failed"]
    assert fake not in rows[0].getMessage()
