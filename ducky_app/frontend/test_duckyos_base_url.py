"""DUCKYOS_BASE_URL beats the saved setting, so a CI run that points at staging never reaches production."""

from __future__ import annotations

from types import SimpleNamespace

from frontend import duckyos_account as acct
from frontend import settings


def test_env_wins_over_the_setting_and_unset_falls_back(monkeypatch):
    monkeypatch.setattr(settings.PanelSettings, "load", classmethod(lambda cls: SimpleNamespace(duckyos_base_url="https://saved.example")))
    monkeypatch.setenv("DUCKYOS_BASE_URL", "https://duckyos-staging.example/")
    assert acct.resolve_base_url() == "https://duckyos-staging.example"
    assert acct.resolve_base_url("https://given.example") == "https://given.example"  # an explicit override still wins
    monkeypatch.setenv("DUCKYOS_BASE_URL", "  ")
    assert acct.resolve_base_url() == "https://saved.example"
    monkeypatch.delenv("DUCKYOS_BASE_URL")
    assert acct.resolve_base_url() == "https://saved.example"
