"""Asserts for plugin_version helpers."""

from backend.uefn_plugins.plugin_version import (
    format_plugin_version,
    parse_plugin_version,
    plugin_version_newer,
    plugin_version_rank,
)


def test_legacy_int_and_semver() -> None:
    assert parse_plugin_version(7) == (0, 0, 7)
    assert parse_plugin_version("7") == (0, 0, 7)
    assert parse_plugin_version("1.0.8") == (1, 0, 8)
    assert format_plugin_version(7) == "7"
    assert format_plugin_version("1.0.8") == "1.0.8"
    assert plugin_version_newer("1.0.8", 7)
    assert plugin_version_newer("1.0.46", 45)
    assert not plugin_version_newer(7, "1.0.8")
    assert plugin_version_rank("1.0.8") > plugin_version_rank(45)


if __name__ == "__main__":
    test_legacy_int_and_semver()
    print("test_plugin_version ok")
