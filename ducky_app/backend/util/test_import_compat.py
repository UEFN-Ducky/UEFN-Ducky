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


def test_tools_init_does_not_import_domain_uefn():
    """Always-on tools bootstrap must not load Store-gated domains."""
    # Fresh check against source
    from pathlib import Path

    init = Path(__file__).resolve().parents[1] / "tools" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    for banned in (
        "backend.tools.uefn",
        "backend.tools.verse",
        "backend.tools.world",
        "backend.tools.animation",
        "backend.tools.vfx",
        "backend.tools.scene",
        "backend.tools.modeling",
        "backend.tools.tester",
        "backend.tools.integrations",
    ):
        assert banned not in text, banned
