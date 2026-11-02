"""check_findability: what makes a plugin findable by skill and tool search."""

from __future__ import annotations

from pathlib import Path

from backend.uefn_plugins.plugin_lint import check_findability


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


GOOD_SKILL = "---\nname: demo\ndescription: Demo plugin records: list and update them with the demo tools.\n---\n\n# Demo\n"


def test_missing_and_multiline_descriptions_are_errors(tmp_path: Path) -> None:
    root = _write(tmp_path / "demo", {
        "skills/demo/SKILL.md": GOOD_SKILL,
        "skills/demo/references/none.md": "# No front matter\n",
        "skills/demo/references/block.md": "---\ndescription: >-\n  folded text\n---\n\n# Block\n",
    })
    errors, warnings = check_findability(root, "demo")
    assert len(errors) == 2
    assert any("references/none.md" in e for e in errors) and any("references/block.md" in e for e in errors)
    assert warnings == []


def test_short_descriptions_and_weak_tool_docstrings_are_warnings(tmp_path: Path) -> None:
    root = _write(tmp_path / "demo", {
        "skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo\n---\n\n# Demo\n",
        "backend/__init__.py": (
            "def register(api):\n"
            "    @api.tool(listener=False)\n"
            "    def demo_list() -> dict:\n"
            "        return {}\n"
            "\n"
            "    @api.tool(listener=False)\n"
            "    def demo_get(item_id: str) -> dict:\n"
            '        """Get one demo record by id, for showing its fields."""\n'
            "        return {}\n"
            "\n"
            "    def helper_named(x: str = '') -> dict:\n"
            '        """Short."""\n'
            "        return {}\n"
            "\n"
            "    api.tool(helper_named, name='demo_named')\n"
        ),
    })
    errors, warnings = check_findability(root, "demo")
    assert errors == []
    text = "\n".join(warnings)
    assert "SKILL.md: description 'Demo' is short" in text
    assert "tool demo_list has no docstring" in text
    assert "tool demo_named starts 'Short.'" in text  # call-form registration reads the function's docstring
    assert "demo_get" not in text


def test_tools_registered_in_a_loop_are_checked_by_their_own_names(tmp_path: Path) -> None:
    root = _write(tmp_path / "demo", {
        "skills/demo/SKILL.md": GOOD_SKILL,
        "backend/__init__.py": (
            "def demo_list() -> dict:\n"
            '    """List every demo record, newest first, for picking one to open."""\n'
            "    return {}\n"
            "\n"
            "def demo_bare() -> dict:\n"
            "    return {}\n"
            "\n"
            "TOOLS = (demo_list, demo_bare)\n"
            "\n"
            "def register(api):\n"
            "    for fn in TOOLS:\n"
            "        api.tool(fn, name=fn.__name__, listener=False)\n"
        ),
    })
    _, warnings = check_findability(root, "demo")
    text = "\n".join(warnings)
    assert "tool fn " not in text  # never the loop variable
    assert "tool demo_bare has no docstring" in text and "demo_list" not in text


def test_a_plugin_without_skills_or_tools_is_clean(tmp_path: Path) -> None:
    assert check_findability(tmp_path, "empty") == ([], [])
