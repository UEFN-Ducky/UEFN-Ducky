"""is_tester_conv identifies testing duckies by details text (not name)."""

from types import SimpleNamespace

from backend.agent.toolsets.categories.testing import is_tester_conv


def test_tester_by_tool_names_in_personality() -> None:
    assert is_tester_conv(
        SimpleNamespace(
            ducky_name="Anything",
            title="x",
            ducky_personality="Run verse_test_run then tester_get_results every turn.",
        )
    )


def test_tester_by_prefix_in_when_to_use() -> None:
    assert is_tester_conv(
        SimpleNamespace(
            ducky_name="QA Bot",
            title="",
            ducky_personality="",
            when_to_use="Uses tester_list_devices for coverage.",
        )
    )


def test_non_tester() -> None:
    assert not is_tester_conv(
        SimpleNamespace(
            ducky_name="Tester",
            title="Tester",
            ducky_personality="Write Verse gameplay.",
            when_to_use="Gameplay code",
        )
    )
