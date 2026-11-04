"""panel_chats.groups: seat names, leaders and refusals on chats that are not groups."""

from __future__ import annotations

from types import SimpleNamespace

import frontend.ui_web.panel_api as _pa
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
from frontend.ui_web.panel_chats import groups


def test_a_second_seat_gets_the_next_free_copy_name():
    members = [{"name": "Verse Coder"}, {"ducky_name": "Verse Coder 2"}]
    assert groups._copy_name({"name": "Verse Coder"}, members) == "Verse Coder 3"
    assert groups._copy_name({}, []) == "Ducky 2"


def test_group_rpcs_refuse_a_chat_that_is_not_a_group(monkeypatch):
    monkeypatch.setattr(_pa, "load_conversation", lambda *a, **kw: SimpleNamespace(is_group=False))
    api = PanelApiChatsMixin()
    for call in (lambda: api.group_set_leader("g", "m"), lambda: api.group_remove("g", "m"),
                 lambda: api.group_get_lanes("g"), lambda: api.group_add_member("g", "m")):
        assert call() == {"ok": False, "error": "Not a group chat"}
    assert api.group_check_lane("g", "m", None)["errors"] == ["Not a group chat"]
    assert api.group_lane_authorities("g") == []


def test_a_member_counts_where_it_is_filed(monkeypatch):
    hub = SimpleNamespace(is_group=True)
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: hub if cid == "other-team" else None)
    assert groups._filed_with_group(SimpleNamespace(parent_conv_id="g"), "g")
    assert groups._filed_with_group(SimpleNamespace(parent_conv_id="other-team"), "g")
    assert not groups._filed_with_group(SimpleNamespace(parent_conv_id="gone"), "g")


def test_an_api_key_chat_is_refused_to_agents(monkeypatch):
    from frontend import favorite_models

    conv = SimpleNamespace(title="Paid", coding_agent="ducky", provider="openai", model="gpt")
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: conv)
    monkeypatch.setattr(favorite_models, "billing_kind", lambda *a: "api_key")
    monkeypatch.setattr(_pa.PanelSettings, "load", staticmethod(lambda: SimpleNamespace(agent_api_keys=[])))
    assert "API key" in groups._agent_api_key_refusal("c")
    monkeypatch.setattr(_pa.PanelSettings, "load", staticmethod(lambda: SimpleNamespace(agent_api_keys=["OpenAI"])))
    assert groups._agent_api_key_refusal("c") == ""
