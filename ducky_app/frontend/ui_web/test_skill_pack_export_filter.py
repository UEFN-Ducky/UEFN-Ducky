"""pywebview file-type filters used by skill-pack export/import.

pywebview only allows \\w in extensions — hyphens in *.ducky-skill-pack raise
ValueError before the save dialog opens (looked like an instant cancel).
"""

from __future__ import annotations



def _parse_like_pywebview(file_type: str) -> tuple[str, str]:
    # Keep in sync with webview.util.parse_file_type (pywebview 6.x).
    from webview.util import parse_file_type

    return parse_file_type(file_type)


def test_hyphenated_skill_pack_filter_rejected():
    bad = "Skill packs (*.ducky-skill-pack;*.zip)"
    try:
        _parse_like_pywebview(bad)
        raised = False
    except ValueError:
        raised = True
    assert raised, "hyphenated extension must be rejected by pywebview"


def test_zip_filter_accepted():
    name, pattern = _parse_like_pywebview("Skill packs (*.zip)")
    assert name == "Skill packs"
    assert pattern == "*.zip"


def test_export_filters_used_by_panel_api():
    # Guard against regressing to the broken hyphenated filter (lives on the store mixin).
    from pathlib import Path

    here = Path(__file__).parent
    src = "".join(p.read_text(encoding="utf-8") for p in sorted(here.glob("panel_api*.py")))
    assert "Skill packs (*.zip)" in src
    assert "*.ducky-skill-pack;*.zip" not in src
