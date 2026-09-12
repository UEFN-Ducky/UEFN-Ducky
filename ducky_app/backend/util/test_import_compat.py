"""Legacy import compatibility for reorganized backend modules."""
from __future__ import annotations

import importlib
import sys


def test_legacy_tools_actors_resolves_to_uefn_package():
    # Ensure finder is installed
    import backend  # noqa: F401
    from backend.util import import_compat  # noqa: F401

    # Drop any prior binding so we exercise the finder
    sys.modules.pop("backend.tools.actors", None)
    mod = importlib.import_module("backend.tools.actors")
    assert mod.__name__ == "backend.tools.uefn.actors"


def test_legacy_skill_resolves():
    import backend  # noqa: F401
    from backend.util import import_compat  # noqa: F401

    sys.modules.pop("backend.skill", None)
    mod = importlib.import_module("backend.skill")
    assert mod.__name__ == "backend.skills.store"


def test_legacy_json_util_resolves():
    import backend  # noqa: F401
    from backend.util import import_compat  # noqa: F401

    sys.modules.pop("backend.json_util", None)
    mod = importlib.import_module("backend.json_util")
    assert mod.__name__ == "backend.util.json_util"
    assert callable(mod.tool_json)


# Host-disk Verse registers without the Store plugin or Epic MCP (f8c866e), so the
# always-on bootstrap imports these three by design. Everything else under
# backend.tools.verse is still Store-gated.
ALWAYS_ON_VERSE_MODULES = frozenset({"skill_tool", "verse", "verse_diagnostics"})


def _tools_init_source() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "tools" / "__init__.py").read_text(encoding="utf-8")


def test_tools_init_does_not_import_domain_uefn():
    """Always-on tools bootstrap must not load Store-gated domains."""
    text = _tools_init_source()
    for banned in (
        "backend.tools.uefn",
        "backend.tools.world",
        "backend.tools.animation",
        "backend.tools.vfx",
        "backend.tools.scene",
        "backend.tools.modeling",
        "backend.tools.tester",
        "backend.tools.integrations",
    ):
        assert banned not in text, banned


def test_tools_init_imports_only_the_host_disk_verse_modules():
    """The verse exemption is three named modules, not the whole domain."""
    import re

    imported = set(re.findall(r"^from backend\.tools\.verse import (\w+)", _tools_init_source(), re.M))
    assert imported == ALWAYS_ON_VERSE_MODULES, (
        f"unexpected always-on verse imports: {imported ^ ALWAYS_ON_VERSE_MODULES}"
    )


def test_tools_bootstrap_does_not_shadow_the_verse_subpackage():
    """An always-on `from backend.tools.verse import verse` would rebind the name
    `verse` inside backend.tools, so backend.tools.verse stops naming the package.
    Every `import backend.tools.verse.<mod> as x` then dies on the attribute walk.
    """
    import backend.tools

    assert backend.tools.verse.__name__ == "backend.tools.verse"
    mod = importlib.import_module("backend.tools.verse.verse_editable")
    assert mod.__name__ == "backend.tools.verse.verse_editable"
