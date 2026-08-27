"""execute_python denylist — no Unreal required."""

from __future__ import annotations

import re
from pathlib import Path


def _load_blocked():
    source = (
        Path(__file__).resolve().parents[3]
        / "uefn_listener"
        / "listener"
        / "handlers"
        / "system.py"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"(def _execute_python_blocked\(code: str\) -> str \| None:.*?)(?=\n@register|\ndef cmd_)",
        source,
        flags=re.S,
    )
    assert match, "_execute_python_blocked not found"
    ns: dict = {}
    exec(match.group(1), ns)
    return ns["_execute_python_blocked"]


def test_mangled_property_read_is_allowed():
    blocked = _load_blocked()
    code = (
        'script = actor.get_editor_property("Script")\n'
        'print(script.get_editor_property("__verse_0xDE71A4D4_CatSpawners"))\n'
    )
    assert blocked(code) is None


def test_walk_plus_hash_is_blocked():
    blocked = _load_blocked()
    code = "import os\nfor r, d, f in os.walk(root):\n    if b'__verse_0x' in data: pass\n"
    msg = blocked(code)
    assert msg is not None
    assert "blocked" in msg.lower()
    assert "allowed" in msg.lower() or "get_verse_editables" in msg


def test_os_walk_alone_is_blocked():
    blocked = _load_blocked()
    msg = blocked("import os\nfor r, d, f in os.walk('/tmp'):\n    print(r)\n")
    assert msg is not None
    assert "walk" in msg.lower()


def test_uasset_plus_hash_is_blocked():
    blocked = _load_blocked()
    msg = blocked('open("Foo.uasset", "rb").read(); print("__verse_0xDE71A4D4_Cat")')
    assert msg is not None


def test_execute_python_delete_asset_is_blocked():
    blocked = _load_blocked()
    msg = blocked("unreal.EditorAssetLibrary.delete_asset('/Game/Foo')")
    assert msg is not None
    assert "never delete" in msg.lower()
    assert "delete queue" in msg.lower()


def test_execute_python_delete_directory_is_blocked():
    blocked = _load_blocked()
    msg = blocked("unreal.EditorAssetLibrary.delete_directory('/Game/Foo')")
    assert msg is not None
    assert "never delete" in msg.lower()
