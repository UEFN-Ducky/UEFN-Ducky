"""Checks for the per-conversation verify-evidence ledger."""

from __future__ import annotations

from backend.agent import verify_evidence as ve


def test_record_ok_only_check_tools():
    tok = ve.bind_conversation("conv-ev")
    try:
        ve.record_ok("spawn_actor")
        assert ve.evidence_names() == []
        ve.record_ok("workspace_compile_verse")
        ve.record_ok("ducky_call_tool", {"name": "get_verse_editables"})
        ve.record_ok("unreal__call_tool", {"tool_name": "GetDeviceProperties"})
        assert ve.evidence_names() == [
            "workspace_compile_verse",
            "get_verse_editables",
            "GetDeviceProperties",
        ]
        assert ve.has_evidence()
    finally:
        ve.reset_conversation(tok)


def test_bind_clears_prior_evidence():
    tok = ve.bind_conversation("conv-reset")
    try:
        ve.record_ok("changeset_list")
        assert ve.has_evidence()
    finally:
        ve.reset_conversation(tok)
    tok = ve.bind_conversation("conv-reset")
    try:
        assert ve.evidence_names() == []
    finally:
        ve.reset_conversation(tok)


def test_fake_verify_warning_requires_check_tool():
    tok = ve.bind_conversation("conv-claim")
    try:
        msg = {"content": "The Verse compiles and the device is wired."}
        assert ve.fake_verify_warning(msg)
        ve.record_ok("workspace_compile_verse")
        assert ve.fake_verify_warning(msg) is None
        assert ve.fake_verify_warning({"content": "still looking"}) is None
    finally:
        ve.reset_conversation(tok)


def test_handoff_warning():
    assert ve.handoff_warning({"content": "Build Verse then drag in Details."})
    assert ve.handoff_warning({"content": "tell me to continue when ready"})
    assert ve.handoff_warning({"content": "All good, no next steps."}) is None
