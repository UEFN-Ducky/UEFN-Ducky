"""Authoritative tab ownership survives delayed reports and window reuse."""
from frontend.ui_web import tab_registry as registry


def test_claim_overrides_stale_reports_and_exclusion_does_not_find_old_copy(monkeypatch):
    monkeypatch.setattr(registry, "_window_tabs", {})
    monkeypatch.setattr(registry, "_tab_claims", {})
    registry.report_open_tabs("main", ["chat:a", "chat:b"])
    registry.claim_tab("chat:a", "focus-new")
    registry.report_open_tabs("main", ["chat:a", "chat:b"])
    assert registry.find_tab_owner("chat:a") == "focus-new"
    assert registry.find_tab_owner("chat:a", exclude_window="focus-new") == ""
    assert registry.find_tab_owner("chat:b") == "main"
    registry.drop_window("focus-new")
    assert registry.find_tab_owner("chat:a") == "main"


def test_closed_claim_does_not_block_reopen_and_same_names_have_distinct_ids(monkeypatch):
    monkeypatch.setattr(registry, "_window_tabs", {})
    monkeypatch.setattr(registry, "_tab_claims", {})
    registry.claim_tab("chat:a", "focus-old")
    registry.claim_tab("chat:b", "focus-other")
    registry.drop_window("focus-old")
    registry.claim_tab("chat:a", "focus-new")
    assert registry.find_tab_owner("chat:a") == "focus-new"
    assert registry.find_tab_owner("chat:b") == "focus-other"
