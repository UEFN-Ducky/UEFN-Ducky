"""Every settings field and its default is a checked-in contract.

The Rust backend (native_launcher/crates/backend/src/api/settings.rs) reads settings rows
with these defaults, so both backends answer the same for a field that has no row. After
an intended change, regenerate settings_defaults.json from ducky_app/:
python -m frontend.test_settings_defaults
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from frontend.settings import PanelSettings

CONTRACT = Path(__file__).with_name("settings_defaults.json")


def defaults() -> dict[str, Any]:
    """{field: default} as it is stored; ``port`` is never a row (the environment sets it)."""
    return {k: v for k, v in PanelSettings().to_json_dict().items() if k != "port"}


def test_the_defaults_match_the_contract() -> None:
    assert json.loads(CONTRACT.read_text(encoding="utf-8")) == defaults(), (
        "PanelSettings changed. Regenerate: python -m frontend.test_settings_defaults"
    )


if __name__ == "__main__":
    CONTRACT.write_text(json.dumps(defaults(), indent=1) + "\n", encoding="utf-8")
    print(f"{CONTRACT.name}: {len(defaults())} fields")
