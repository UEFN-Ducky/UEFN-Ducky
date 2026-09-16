"""Skill metadata accepts legacy integer revisions and semantic versions."""

def skill_version(raw: object) -> int | str:
    """Keep integer revisions compatible while preserving dotted versions for display."""
    # The plugin package imports the skill store during initialization.
    from backend.uefn_plugins.plugin_version import format_plugin_version

    value = format_plugin_version(raw)
    return int(value) if value.isdigit() else value


def skill_version_key(raw: object) -> tuple[int, int, int]:
    from backend.uefn_plugins.plugin_version import parse_plugin_version

    return parse_plugin_version(raw)


def next_skill_version(raw: object) -> int | str:
    value = skill_version(raw)
    if isinstance(value, int):
        return value + 1
    major, minor, patch = skill_version_key(value)
    return f"{major}.{minor}.{patch + 1}"
