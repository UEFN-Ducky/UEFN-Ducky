"""Settings and appearance calls: the same answer, rows and window events from both backends."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.store import db
from backend.store.repos import settings
from frontend.duckyos_account import OFF_PC_DENY, REMOTE_DENY

_MARK = {"marker": 1}
_MINE = {"id": "p1", "name": "Mine", "foundation": {"bg": "#000"}, "overrides": {"x": "1"}, "status_overrides": {"error": {"fg": "red"}}}
_LOOK = {
    "appearance_foundation": {"bg": "#111"},
    "appearance_overrides": {"accent": "#0f0"},
    "appearance_status_overrides": {"ok": {"fg": "green"}},
    "appearance_profiles": [_MINE, {"id": "p2", "name": "Other"}],
    "appearance_active_profile_id": "p1",
    "appearance_effect_id": "snow",
    "appearance_skin_id": "glass",
}


class _Both:
    """One call on each backend from the same rows: the answer, the rows it left and the
    events it pushed must match."""

    def __init__(self, python: Any, rust: Any) -> None:
        self.python, self.rust = python, rust
        self.pushed: list[dict] = []
        python.api._push_panel = self.pushed.append
        self.cursor = 0

    def _rust_events(self) -> list[dict]:
        # A marker ends the page, so a call that pushed nothing does not wait out a poll.
        assert self.rust.call("push_appearance_live", _MARK) == (200, {"ok": True, "result": None})
        status, page = self.rust.request(f"/__panel_events?since={self.cursor}")
        assert status == 200 and page["events"][-1] == {"type": "appearance_changed", "appearance": _MARK}
        self.cursor = page["cursor"]
        return page["events"][:-1]

    def call(self, rows: dict, method: str, *args: Any, same_ids: bool = True) -> tuple[Any, dict, list]:
        seen = []
        for backend in (self.python, self.rust):
            settings.replace_all(rows, {})
            self.pushed.clear()
            answer = backend.call(method, *args)
            events = list(self.pushed) if backend is self.python else self._rust_events()
            seen.append(json.loads(json.dumps((answer, settings.load_fields(), events))))
        theirs, ours = seen
        if not same_ids:  # a new profile's id is random: Rust's is read as Python's
            ours = json.loads(json.dumps(ours).replace(ours[0][1]["result"]["id"], theirs[0][1]["result"]["id"]))
        assert ours == theirs, f"{method}{args}: rust {ours} != python {theirs}"
        return tuple(theirs)


@pytest.fixture
def both(python, rust_store) -> _Both:
    return _Both(python, rust_store)


def _ok(result: Any) -> list:
    return [200, {"ok": True, "result": result}]


def _error(text: str) -> list:
    return [200, {"ok": False, "error": text}]


# --------------------------------------------------------------------------- settings


def test_get_settings_is_the_same_from_defaults_and_from_odd_rows(both) -> None:
    answer, _, _ = both.call({}, "get_settings")
    assert answer[1]["result"]["web_access"] == "ask" and answer[1]["result"]["follow_code_enabled"] is False
    rows = {
        "agent_model": "m",
        "default_coding_agent": "",
        "coding_agents": {"Claude-Code": {"cli_path": "x"}, "codex": {"enabled": 0, "default_args": None, "permission_mode": "ask"}, "bad": 1},
        "voice_default_speed": 0,
        "voice_process_talk": 0,
        "voice_stt_provider": " whisper ",
        "tts_volume": 0.4,
        "mic_permission": "nope",
        "web_access": "on",
        "memory_keep_last_messages": 0,
        "memory_compress_tokens": 1234.9,
        "memory_index_max_chars": 2500,
        "team_send_delay_s": 3.7,
        "walkthrough_completed": {"a": True, "b": False, "c": 1},
        "follow_code_enabled": True,
        "follow_code_speed": "warp",
        "write_lanes_mode": "strict",
        "remote_access": 1,
        "default_image_provider": "img",
        "openai_api_key": "sk-never",
        "no_such_field": 1,
        "terminals_enabled": None,
    }
    answer, _, _ = both.call(rows, "get_settings")
    result = answer[1]["result"]
    assert (result["web_access"], result["mic_permission"], result["memory_index_max_chars"]) == ("on", "ask", 8000)
    assert result["coding_agents"]["codex"] == {"enabled": False, "cli_path": "", "default_args": "", "permission_mode": "ask"}
    assert "openai_api_key" not in result and result["terminals_enabled"] is True
    # The switch that keeps follow-code on once it was turned off for everyone.
    answer, _, _ = both.call({"follow_code_enabled": True, "follow_code_off_migrated": True, "follow_code_speed": "fast"}, "get_settings")
    assert (answer[1]["result"]["follow_code_enabled"], answer[1]["result"]["follow_code_speed"]) == (True, "fast")


def test_rust_leaves_settings_python_has_not_imported_yet(python, rust_store) -> None:
    (db.app_root() / "panel_settings.json").write_text(json.dumps({"appearance_skin_id": "glass"}), encoding="utf-8")
    assert rust_store.call("get_appearance") == (200, {"ok": False, "error": "settings are not imported yet"})
    theirs = python.call("get_appearance")  # Python's first load imports the file
    assert theirs[1]["result"]["skin_id"] == "glass" and rust_store.call("get_appearance") == theirs


# --------------------------------------------------------------------------- appearance


def test_get_appearance_is_the_same_and_resets_a_look_that_moved_to_the_store(both) -> None:
    answer, rows, events = both.call({}, "get_appearance")
    assert (answer[1]["result"]["active_profile_id"], rows, events) == ("__default__", {}, [])
    dirty = {**_LOOK, "appearance_profiles": [_MINE, {"id": "__default__", "name": "No"}, {"id": "x"}, 5], "appearance_sounds": {"enabled": True}}
    answer, rows, _ = both.call(dirty, "get_appearance")
    assert answer[1]["result"]["profiles"] == [_MINE] and rows["appearance_profiles"] == dirty["appearance_profiles"]
    for legacy, effect in [("__hacker__", "matrix"), ("__hacker__", "snow"), ("__light__", "matrix")]:
        answer, rows, _ = both.call({**_LOOK, "appearance_active_profile_id": legacy, "appearance_effect_id": effect}, "get_appearance")
        assert answer[1]["result"]["active_profile_id"] == "__default__" and answer[1]["result"]["foundation"] == {}
        assert rows["appearance_active_profile_id"] == "__default__", "saved, not only shown"
        assert rows.get("appearance_effect_id") == (None if (legacy, effect) == ("__hacker__", "matrix") else effect)


def test_save_appearance_cleans_and_saves_the_patch_and_tells_the_windows(both) -> None:
    patch = {
        "foundation": {"bg": "#222", "empty": "", "n": 3},
        "overrides": {},
        "status_overrides": {"error": {"fg": "red", "bg": ""}, "ok": {"x": ""}, "bad": 1},
        "profiles": [_MINE, {"id": " p3 ", "name": " Three ", "status_overrides": {"ok": {}}}, {"id": "__default__", "name": "No"}],
        "active_profile_id": "p3",
        "effect_id": " snow ",
        "effects_enabled": 0,
        "skin_id": None,
        "profile_patches": {"p1": {"a": 1}, "junk": 2},
        "sounds": {"enabled": 1, "volume": "0.25", "mapping": {"send": "pop", "bad": 3}},
    }
    answer, rows, events = both.call(_LOOK, "save_appearance", patch)
    assert answer == _ok("Appearance saved") and rows["appearance_foundation"] == {"bg": "#222", "n": "3"}
    assert rows["appearance_status_overrides"] == {"error": {"fg": "red"}}
    assert rows["appearance_sounds"] == {"enabled": True, "volume": 0.25, "mapping": {"send": "pop"}}
    assert [e["type"] for e in events] == ["appearance_changed"] and events[0]["appearance"]["effect_id"] == "snow"
    # The patch as JSON text, an empty one, and the volumes a page can send.
    both.call(_LOOK, "save_appearance", json.dumps({"skin_id": " paper ", "sounds": {}}))
    for patch in (None, "", "  ", {}):
        assert both.call(_LOOK, "save_appearance", patch)[0] == _ok("Appearance saved")
    for volume in (None, 7, -2, True, "loud", [1], 1):
        _, rows, _ = both.call({}, "save_appearance", {"sounds": {"volume": volume}})
        assert 0.0 <= rows["appearance_sounds"]["volume"] <= 1.0
    assert both.call({}, "save_appearance", "[1]")[0] == _error("appearance patch must be a JSON object")
    assert both.call({}, "save_appearance", 5)[0] == _error("appearance patch must be an object")


def test_push_appearance_live_shows_a_look_without_saving_it(both) -> None:
    _, rows, events = both.call(_LOOK, "push_appearance_live", {"foundation": {"bg": "#333"}})
    assert events == [{"type": "appearance_changed", "appearance": {"foundation": {"bg": "#333"}}}]
    assert rows["appearance_foundation"] == {"bg": "#111"}
    assert both.call(_LOOK, "push_appearance_live", "not a look")[2] == []


def test_appearance_profiles_are_saved_loaded_renamed_and_deleted_the_same(both) -> None:
    for name in (" Night ", "", None):
        answer, rows, events = both.call(_LOOK, "save_appearance_profile", name, same_ids=False)
        made = answer[1]["result"]
        assert made["name"] == (name or "Untitled").strip() and made["foundation"] == {"bg": "#111"}
        assert rows["appearance_active_profile_id"] == made["id"] and rows["appearance_profiles"][-1] == made and events == []

    answer, rows, events = both.call(_LOOK, "load_appearance_profile", "p2")
    assert answer[1]["result"]["active_profile_id"] == "p2" and "appearance_foundation" not in rows and len(events) == 1
    answer, rows, _ = both.call(_LOOK, "load_appearance_profile", " __default__ ")
    assert rows["appearance_active_profile_id"] == "__default__" and rows.get("appearance_effect_id") == "snow"
    assert both.call(_LOOK, "load_appearance_profile", "nope")[0] == _error("Profile not found: nope")
    assert both.call(_LOOK, "load_appearance_profile", None)[0] == _error("Profile not found: None")

    answer, rows, events = both.call(_LOOK, "rename_appearance_profile", " p2 ", " Second ")
    assert answer == _ok({"id": "p2", "name": "Second"}) and rows["appearance_profiles"][1]["name"] == "Second" and events == []
    assert both.call(_LOOK, "rename_appearance_profile", "__default__", "x")[0] == _error("Built-in appearance profiles cannot be renamed")
    assert both.call(_LOOK, "rename_appearance_profile", "p2", "  ")[0] == _error("Profile name is required")
    assert both.call(_LOOK, "rename_appearance_profile", "p9", "x")[0] == _error("Profile not found: p9")

    answer, rows, events = both.call(_LOOK, "delete_appearance_profile", "p2")
    assert answer == _ok("Profile deleted") and rows["appearance_profiles"] == [_MINE] and events == []
    assert rows["appearance_active_profile_id"] == "p1" and rows["appearance_skin_id"] == "glass"
    # The look in use is deleted: back to Default, no effect, no skin, and the windows hear.
    answer, rows, events = both.call(_LOOK, "delete_appearance_profile", "p1")
    assert rows["appearance_active_profile_id"] == "__default__" and len(events) == 1
    assert not {"appearance_foundation", "appearance_skin_id", "appearance_effect_id"} & set(rows)
    assert both.call(_LOOK, "delete_appearance_profile", "__default__")[0] == _error("Built-in appearance profiles cannot be deleted")
    assert both.call(_LOOK, "delete_appearance_profile", "p9")[0] == _error("Profile not found: p9")

    answer, rows, _ = both.call(_LOOK, "update_active_appearance_profile")
    assert answer[1]["result"] == {**_MINE, "foundation": {"bg": "#111"}, "overrides": {"accent": "#0f0"}, "status_overrides": {"ok": {"fg": "green"}}}
    assert rows["appearance_profiles"][0] == answer[1]["result"]
    for active in ("", "__default__", "gone"):
        assert both.call({**_LOOK, "appearance_active_profile_id": active}, "update_active_appearance_profile")[0] == _ok(None)


# --------------------------------------------------------------------------- shared rows


def test_a_save_through_one_backend_is_seen_by_the_other_within_one_call(python, rust_store) -> None:
    python.api._push_panel = lambda event: None
    assert rust_store.call("save_appearance", {"skin_id": "glass", "foundation": {"bg": "#123"}})[1]["ok"] is True
    theirs = python.call("get_appearance")
    assert theirs[1]["result"]["skin_id"] == "glass" and theirs == rust_store.call("get_appearance")
    assert python.call("save_appearance", {"skin_id": "paper"})[1]["ok"] is True
    ours = rust_store.call("get_appearance")
    assert ours[1]["result"]["skin_id"] == "paper" and ours[1]["result"]["foundation"] == {"bg": "#123"}
    # Each backend's own copy is older than the other's last save: a save still writes
    # only what its caller changed.
    made = rust_store.call("save_appearance_profile", "Mine")[1]["result"]
    assert python.call("rename_appearance_profile", made["id"], "Yours")[1]["result"]["name"] == "Yours"
    assert rust_store.call("get_appearance") == python.call("get_appearance")
    assert settings.load_fields()["appearance_skin_id"] == "paper"


def test_each_method_rust_answers_is_reached_as_python_allows(rust_store) -> None:
    status, reaches = rust_store.request("/__rs/api/methods")
    assert status == 200 and "get_appearance" in reaches
    for method, reach in reaches.items():
        allowed = "window" if method in REMOTE_DENY else "this_pc" if method in OFF_PC_DENY else "anyone"
        assert reach == allowed, method


def test_only_a_page_on_this_pc_is_answered(rust_store) -> None:
    import urllib.error
    import urllib.request

    def status(headers: dict) -> int:
        req = urllib.request.Request(rust_store.origin + "/__panel_api/get_appearance", data=b"{}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            return err.code

    host = rust_store.origin.split("://")[1]
    assert status({}) == status({"Origin": f"http://{host}"}) == 200
    assert status({"Host": "pc.uefnducky.org"}) == status({"Origin": "https://evil.org"}) == status({"Origin": "null"}) == 403
