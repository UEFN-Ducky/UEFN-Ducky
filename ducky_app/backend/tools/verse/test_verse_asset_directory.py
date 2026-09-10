"""Compiled Verse devices are under /<Project>/_Verse, not bare /_Verse."""

from backend.tools.verse.verse_diagnostics import verse_asset_directory


def test_project_mount() -> None:
    assert verse_asset_directory("ExampleProject1") == "/ExampleProject1/_Verse"
    assert verse_asset_directory("/ExampleProject1/") == "/ExampleProject1/_Verse"


def test_empty_stays_legacy() -> None:
    assert verse_asset_directory("") == "/_Verse"


if __name__ == "__main__":
    test_project_mount()
    test_empty_stays_legacy()
    print("ok")
