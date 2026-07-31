"""INVTEXT cleanup helpers used by validate_uefn_asset (no unreal import)."""

from __future__ import annotations

import re
from pathlib import Path


def _extract_helper(name: str) -> str:
    source = (
        Path(__file__).resolve().parents[3]
        / "uefn_listener"
        / "listener"
        / "registry"
        / "assets_pipeline.py"
    ).read_text(encoding="utf-8")
    # Grab from "def NAME" until the next top-level def.
    pattern = rf"(def {name}\(.*?(?=\ndef [a-zA-Z_]))"
    match = re.search(pattern, source, flags=re.S)
    assert match, f"helper {name} not found"
    return match.group(1)


def _load_helpers():
    ns: dict = {"re": re}
    exec(_extract_helper("_text_list"), ns)
    exec(_extract_helper("_validation_result_name"), ns)
    return ns["_text_list"], ns["_validation_result_name"]


def test_text_list_extracts_invtext_sentence():
    _text_list, _ = _load_helpers()
    blob = (
        'LOCGEN_FORMAT_ORDERED(INVTEXT("{0} ({1})"), '
        'INVTEXT("Package /Roguelike/VFX/NS_X references disallowed object '
        '/Script/NiagaraEditor.NiagaraNodeCustomHlsl."), '
        'INVTEXT("FortValidator_FortExposedAssets"))'
    )
    out = _text_list([blob])
    assert len(out) == 1
    assert "NiagaraNodeCustomHlsl" in out[0]
    assert "FortValidator_FortExposedAssets" not in out[0]


def test_validation_result_name_prefers_enum_name():
    _, _validation_result_name = _load_helpers()

    class Fake:
        name = "INVALID"

    assert _validation_result_name(Fake()) == "INVALID"
    assert _validation_result_name("VALID") == "VALID"
