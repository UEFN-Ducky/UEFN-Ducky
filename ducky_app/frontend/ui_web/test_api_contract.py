"""The panel API surface (method names, parameters, defaults) is a checked-in contract.

The web UI calls these methods by name, and any other backend that serves the window has
to answer the same calls. After an intended change, regenerate api_contract.json from
ducky_app/:  python -m frontend.ui_web.test_api_contract
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from frontend.ui_web.panel_api import PanelApi

CONTRACT = Path(__file__).with_name("api_contract.json")


def surface(cls: type = PanelApi) -> dict[str, list[str]]:
    """{method: ["conv_id", "refresh=False", ...]} for every public method, sorted."""
    out: dict[str, list[str]] = {}
    for name in sorted(dir(cls)):
        fn = getattr(cls, name, None)
        if name.startswith("_") or not callable(fn):
            continue
        params = inspect.signature(fn).parameters.values()
        out[name] = [str(p.replace(annotation=p.empty)) for p in params if p.name != "self"]
    return out


def diff(old: dict, new: dict) -> dict[str, list[str]]:
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
    }


def _contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_surface_matches_the_contract() -> None:
    d = diff(_contract(), surface())
    assert not any(d.values()), f"PanelApi changed: {d}. Regenerate: python -m frontend.ui_web.test_api_contract"


def test_an_added_removed_or_changed_method_is_named() -> None:
    old = {"keep": ["a"], "gone": [], "edit": ["a"]}
    new = {"keep": ["a"], "edit": ["a", "b=1"], "fresh": []}
    assert diff(old, new) == {"added": ["fresh"], "removed": ["gone"], "changed": ["edit"]}


def test_a_new_method_on_the_class_shows_as_added() -> None:
    class Wider(PanelApi):
        def brand_new(self, conv_id: str, refresh: bool = False) -> None: ...

    assert surface(Wider)["brand_new"] == ["conv_id", "refresh=False"]
    assert diff(_contract(), surface(Wider))["added"] == ["brand_new"]


if __name__ == "__main__":
    CONTRACT.write_text(json.dumps(surface(), indent=1) + "\n", encoding="utf-8")
    print(f"{CONTRACT.name}: {len(surface())} methods")
