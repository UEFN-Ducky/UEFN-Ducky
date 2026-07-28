"""profile_details: user-written text drives which tools are always loaded."""

from types import SimpleNamespace

from backend.agent.profile_details import profile_detail_text, tools_named_in_profile


def test_profile_detail_text_joins_fields():
    conv = SimpleNamespace(ducky_personality="Hello", when_to_use="World")
    assert "Hello" in profile_detail_text(conv)
    assert "World" in profile_detail_text(conv)


def test_tools_named_in_profile_exact_match():
    conv = SimpleNamespace(
        ducky_personality="Call ducky_group_create then verse_test_run.",
        when_to_use="",
    )
    found = tools_named_in_profile(
        conv, {"ducky_group_create", "verse_test_run", "spawn_actor"}
    )
    assert found == {"ducky_group_create", "verse_test_run"}
