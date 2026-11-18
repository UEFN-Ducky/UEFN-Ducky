from __future__ import annotations

from backend.agent.coding_agents.base import CodingAgentInfo


def test_to_dict_carries_why_sign_in_is_unavailable() -> None:
    info = CodingAgentInfo(id="gemini_cli", label="Gemini CLI", enabled=True, available=True, status="", cli_path="")
    assert info.to_dict()["sign_in_unavailable"] == ""
    info.sign_in_unavailable = "Google doesn't allow personal accounts here"  # set by the Google gateway
    assert info.to_dict()["sign_in_unavailable"] == "Google doesn't allow personal accounts here"
