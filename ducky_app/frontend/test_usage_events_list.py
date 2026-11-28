"""usage_events.json is usage_events.EVENTS as data: the Rust backend checks events against it.

After an intended change to the list, regenerate it from ducky_app/:
python -m frontend.test_usage_events_list
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from frontend import usage_events as ev

LIST = Path(__file__).with_name("usage_events.json")


def _rule(rule: Any) -> list[Any]:
    """One prop's rule, read the way ``ev.validate`` reads it."""
    if rule == ev.YES_NO:
        return ["yes_no"]
    if rule[0] == "bucket":
        return ["bucket", rule[2], [list(step) for step in rule[1]]]
    if rule[0] in ("slug", "version"):
        return [rule[0]]
    return ["enum", *rule]


def listed() -> dict[str, Any]:
    events = {name: {prop: _rule(rule) for prop, rule in spec.items()} for name, spec in ev.EVENTS.items()}
    return {"ui": sorted(ev.UI_EVENTS), "events": events}


def test_the_list_file_is_the_list() -> None:
    assert json.loads(LIST.read_text(encoding="utf-8")) == listed(), (
        "usage_events.EVENTS changed. Regenerate: python -m frontend.test_usage_events_list"
    )


if __name__ == "__main__":
    LIST.write_text(json.dumps(listed(), indent=1) + "\n", encoding="utf-8")
    print(f"{LIST.name}: {len(listed()['events'])} events")
