"""UEFN popups: buttons found in a capture, Verse-error popups answered, no save after a skip.

The fixtures are the real dialogs UEFN 42.30 showed when Tycoony opened with Verse
errors (Oct 2 2026); an agent and a workflow sat behind them until they timed out.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PIL import Image

from backend.tools.core import uefn_popups as popups

_FIX = Path(__file__).resolve().parent / "testdata" / "popups"


def _img(name: str) -> Image.Image:
    return Image.open(_FIX / f"{name}.png")


@pytest.mark.parametrize(
    ("name", "count", "primary", "press"),
    [
        ("verse_build_errors", 4, [4], 3),  # Copy Errors, Close Project, Skip Rebuild, [Rebuild and Validate]
        ("data_loss_warning", 2, [], 1),  # Skip Rebuild & Continue, Fix Errors First
        ("verse_validation_errors", 4, [2], 4),  # Open VS Code, [Rebuild Verse And Reload Map], Close Map, Continue
    ],
)
def test_buttons_are_found_and_the_known_rule_picks_the_right_one(name, count, primary, press) -> None:
    buttons = popups.find_buttons(_img(name))
    assert len(buttons) == count
    assert [i for i, b in enumerate(buttons, start=1) if b["primary"]] == primary
    assert [b["nx"] for b in buttons] == sorted(b["nx"] for b in buttons)
    rule = popups.known_rule(name.replace("_", " "), buttons)
    assert rule is not None and rule["press"] == press


def test_a_known_title_with_another_layout_is_not_pressed() -> None:
    buttons = popups.find_buttons(_img("verse_validation_errors"))
    assert popups.known_rule("Verse Build Errors", buttons) is None  # blue button in the wrong place
    assert popups.known_rule("Some Other Dialog", buttons) is None


def test_the_capture_numbers_every_button() -> None:
    img = _img("verse_build_errors")
    out = popups.annotate(img, popups.find_buttons(img))
    assert out.size == img.size and out.tobytes() != img.convert("RGB").tobytes()


@pytest.fixture()
def screen(monkeypatch, tmp_path):
    """A fake UEFN: a stack of popups; a click on the rule's button shows the next one."""
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(popups, "_uefn_pids", lambda: {4242})
    stack = [("Verse Build Errors", "verse_build_errors", 3),
             ("Data Loss Warning", "data_loss_warning", 1),
             ("Verse Validation Errors", "verse_validation_errors", 4)]
    state = {"clicks": []}

    def list_popups():
        if not stack:
            return []
        title, name, _ = stack[0]
        w, h = _img(name).size
        return [{"hwnd": 100 + len(stack), "title": title, "rect": {"left": 0, "top": 0, "right": w, "bottom": h, "width": w, "height": h}}]

    def grab(rect, hwnd=0):
        return _img(stack[0][1])

    def inject_pointer(hwnd, kind, nx, ny, **_):
        if kind != "up":
            return
        title, name, want = stack[0]
        hit = [i for i, b in enumerate(popups.find_buttons(_img(name)), start=1) if abs(b["nx"] - nx) < 1e-6]
        state["clicks"].append((title, hit[0]))
        if hit[0] == want:
            stack.pop(0)

    monkeypatch.setattr(popups, "list_popups", list_popups)
    monkeypatch.setattr(popups, "_grab", grab)
    monkeypatch.setattr("frontend.window_view.inject_pointer", inject_pointer)
    return state


def test_the_verse_popups_on_project_open_are_answered_in_order(screen) -> None:
    for _ in range(3):
        popups.answer_known_popups()
    assert screen["clicks"] == [("Verse Build Errors", 3), ("Data Loss Warning", 1), ("Verse Validation Errors", 4)]
    assert popups.list_popups() == []
    assert popups.verse_skip_active() is True


def test_nothing_saves_the_level_after_a_skip(screen, monkeypatch) -> None:
    for _ in range(3):
        popups.answer_known_popups()
    from backend.bridge import client
    from backend.tools.core import uefn_modal

    with pytest.raises(RuntimeError, match="Verse devices are broken"):
        client.send_command("save_all_dirty", {"maps": True})
    assert uefn_modal.auto_dismiss_save_modal() is None

    sent = []
    monkeypatch.setattr("backend.bridge.client.send_command", lambda *a, **k: sent.append(a) or {})
    monkeypatch.setattr("backend.bridge.client.listener_get_health", lambda *a, **k: {"ok": True})
    from frontend.ui_web.verse_editor.workflow.client import _presave_dirty_packages

    _presave_dirty_packages()
    assert sent == []  # no pre-save before the build


def test_a_clean_build_reloads_the_level_and_saving_works_again(screen, monkeypatch) -> None:
    for _ in range(3):
        popups.answer_known_popups()
    calls = []

    def send_command(command, params=None, timeout=0):
        calls.append(command)
        return {"result": {"level": "/Tycoony/Tycoony", "loaded": True}, "stdout": "", "stderr": ""}

    monkeypatch.setattr("backend.bridge.client.send_command", send_command)
    out = popups.reload_level_after_clean_build()
    assert calls == ["execute_python"] and out["loaded"] is True
    assert popups.verse_skip_active() is False
    assert popups.reload_level_after_clean_build() is None  # nothing to do any more


def test_the_block_ends_with_the_uefn_that_skipped(screen, monkeypatch) -> None:
    for _ in range(3):
        popups.answer_known_popups()
    monkeypatch.setattr(popups, "_uefn_pids", lambda: {9999})  # UEFN restarted: level reloads from disk
    assert popups.verse_skip_active() is False


def _guard_pass() -> None:
    """One pass of the background guard's loop."""
    for event in popups.answer_known_popups():
        popups._log_event(event)


def _popup_events() -> list[dict]:
    from backend.store.repos import events

    return events.newest("uefn_popup", limit=50)


@pytest.fixture()
def stuck(monkeypatch):
    """A known popup whose layout never matches its rule (another DPI or theme), on a fake clock."""
    w, h = _img("verse_validation_errors").size
    rect = {"left": 0, "top": 0, "right": w, "bottom": h, "width": w, "height": h}
    grabs: list[int] = []
    clock = {"now": 1000.0}

    def grab(rect, hwnd=0):
        grabs.append(hwnd)
        return _img("verse_validation_errors")  # its blue button is not where Verse Build Errors has it

    class Clock:
        time = staticmethod(lambda: clock["now"])
        monotonic = staticmethod(lambda: clock["now"])
        sleep = staticmethod(lambda _s: None)

    monkeypatch.setattr(popups, "list_popups", lambda: [{"hwnd": 77, "title": "Verse Build Errors", "rect": rect}])
    monkeypatch.setattr(popups, "_grab", grab)
    monkeypatch.setattr(popups, "time", Clock)
    monkeypatch.setattr(popups, "_retry", {})
    return {"grabs": grabs, "clock": clock}


def test_a_popup_that_does_not_match_is_not_captured_again_every_pass(stuck) -> None:
    for _ in range(10):
        _guard_pass()
    assert stuck["grabs"] == [77]
    assert [e["message"] for e in _popup_events()] == ["Verse Build Errors: not pressed (layout did not match)"]

    stuck["clock"]["now"] += 120  # later it is looked at again, in case it was caught mid-draw
    _guard_pass()
    assert stuck["grabs"] == [77, 77]


def test_each_answered_popup_is_logged_once(screen) -> None:
    for _ in range(5):
        _guard_pass()
    assert sorted(e["message"] for e in _popup_events()) == [
        "Data Loss Warning: pressed Skip Rebuild & Continue",
        "Verse Build Errors: pressed Skip Rebuild",
        "Verse Validation Errors: pressed Continue",
    ]


# --------------------------------------------------------------------------- lone OK / Close
#
# Oct 16 2026: UEFN showed "Unable to Refresh — Timed out attempting to stop game." [OK]
# while an agent pushed a level. Its Epic call (SessionToolset.GetSessionStatus) hung the
# full 180 s, the timeout said only "use the Ducky fallback tool", and the agent then sat
# on uefn_logs_wait for 7 minutes with the popup still up. The fixture is that dialog.


def _box(title: str = "Unable to Refresh", **over) -> dict:
    w, h = _img("unable_to_refresh").size
    rect = {"left": 0, "top": 0, "right": w, "bottom": h, "width": w, "height": h}
    return {"hwnd": 515, "title": title, "rect": rect, "blocking": True, "enabled": True, **over}


def test_unable_to_refresh_is_a_lone_ok_message_box() -> None:
    buttons = popups.find_buttons(_img("unable_to_refresh"))
    assert len(buttons) == 1 and buttons[0]["primary"]  # the blue OK; the clipboard icon is no button
    box = _box()
    assert popups.is_lone_button_popup(box, buttons)
    assert not popups.is_lone_button_popup({**box, "blocking": False}, buttons)  # the editor is not held
    assert not popups.is_lone_button_popup({**box, "enabled": False}, buttons)  # a window behind the modal
    assert not popups.is_lone_button_popup(_box("Delete Assets"), buttons)
    assert not popups.is_lone_button_popup(_box("Private version has been created"), buttons)  # copy first
    assert not popups.is_lone_button_popup(_box("Verse Build Errors"), buttons)  # answered by its rule
    big = _box(rect={"left": 0, "top": 0, "right": 1800, "bottom": 1000, "width": 1800, "height": 1000})
    assert not popups.is_lone_button_popup(big, buttons)  # a floating tool window, not a message box
    assert not popups.is_lone_button_popup(box, popups.find_buttons(_img("data_loss_warning")))  # a choice


@pytest.fixture()
def uefn_popup(monkeypatch):
    """A fake UEFN showing one blocking popup until any of its buttons is clicked."""
    state = {"up": None, "clicks": [], "image": "unable_to_refresh"}

    def show(title: str, image: str) -> None:
        state["up"], state["image"] = _box(title), image

    def inject_pointer(hwnd, kind, nx, ny, **_):
        if kind == "up" and state["up"] and hwnd == state["up"]["hwnd"]:
            state["clicks"].append((state["up"]["title"], nx, ny))
            state["up"] = None

    monkeypatch.setattr(popups, "list_popups", lambda: [dict(state["up"])] if state["up"] else [])
    monkeypatch.setattr(popups, "_grab", lambda rect, hwnd=0: _img(state["image"]))
    monkeypatch.setattr(popups, "_save_capture", lambda img, buttons: "C:/chat/attachments/uefn_popup_1.png")
    monkeypatch.setattr(popups, "_retry", {})
    monkeypatch.setattr("frontend.window_view.inject_pointer", inject_pointer)
    state["show"] = show
    return state


def test_the_lone_button_pass_presses_ok_and_keeps_the_capture(uefn_popup) -> None:
    uefn_popup["show"]("Unable to Refresh", "unable_to_refresh")
    done = popups.press_lone_button_popups()
    assert [(d["title"], d["closed"], d["path"]) for d in done] == [
        ("Unable to Refresh", True, "C:/chat/attachments/uefn_popup_1.png")
    ]
    assert len(uefn_popup["clicks"]) == 1
    assert popups.press_lone_button_popups() == []  # nothing left on screen


@pytest.mark.parametrize("title", ["Modifications en conflit", "Conflicting Changes"])
def test_the_conflicting_changes_dialog_is_never_pressed(uefn_popup, title) -> None:
    # Oct 27: after a pull merged a file changed on both PCs. Only the person answers it, even
    # if its capture ever showed a single blue button.
    from backend.tools.core.uefn_modal import is_save_dialog_title

    uefn_popup["show"](title, "unable_to_refresh")
    assert not popups.is_lone_button_popup(uefn_popup["up"], popups.find_buttons(_img("unable_to_refresh")))
    assert popups.press_lone_button_popups() == [] and popups.answer_known_popups() == []
    assert not is_save_dialog_title(title)  # the Save watchdog's Enter never goes to it either
    assert uefn_popup["clicks"] == [] and uefn_popup["up"] is not None
    assert popups.find_conflict_dialog()["title"] == title


def test_a_popup_with_a_choice_is_left_to_the_agent(uefn_popup) -> None:
    uefn_popup["show"]("Unable to Launch", "data_loss_warning")
    assert popups.press_lone_button_popups() == []
    assert uefn_popup["clicks"] == [] and uefn_popup["up"] is not None


def _epic_pool(monkeypatch, popup_state, *, timeout: float = 180.0):
    """The plugin pool wired to a fake UEFN Epic MCP whose call runs on the Slate thread:
    it cannot answer while a popup is up."""
    from types import SimpleNamespace

    from mcp.types import TextContent

    from backend.mcp_plugins import client_pool, epic
    from backend.mcp_plugins.registry import rebuild_plugin_prefix_cache
    from backend.tools.core import uefn_modal

    rebuild_plugin_prefix_cache([{"id": epic.EPIC_MCP_SERVER_ID, "tool_prefix": "unreal"}])
    monkeypatch.setattr(client_pool, "ensure_plugin_prefix_cache", lambda: None)
    monkeypatch.setattr(client_pool, "_TOOL_TIMEOUT_SEC", timeout)
    monkeypatch.setattr(epic, "probe_epic_mcp", lambda **_k: {"epic_mcp_online": True, "epic_mcp_editor": "uefn"})
    monkeypatch.setattr(uefn_modal.sys, "platform", "win32")
    monkeypatch.setattr(uefn_modal, "_LONE_BUTTON_AFTER_SEC", 0.0)
    monkeypatch.setattr(uefn_modal, "auto_dismiss_save_modal", lambda: None)

    class Session:
        async def call_tool(self, name, args):
            while popup_state["up"]:
                await asyncio.sleep(0.02)
            return SimpleNamespace(content=[TextContent(type="text", text='{"sessionStatus": "Edit"}')])

    pool = client_pool.PluginClientPool()
    conn = SimpleNamespace(last_used=0.0)

    async def get_or_create(_pid):
        return conn

    async def ensure_session(_conn):
        return Session()

    monkeypatch.setattr(pool, "_get_or_create", get_or_create)
    monkeypatch.setattr(pool, "_ensure_session", ensure_session)
    return pool


_GET_SESSION_STATUS = {
    "toolset_name": "ValkyrieToolset.SessionToolset",
    "tool_name": "GetSessionStatus",
    "arguments": {},
}


def test_a_hung_epic_call_gets_the_unable_to_refresh_popup_pressed(uefn_popup, monkeypatch) -> None:
    uefn_popup["show"]("Unable to Refresh", "unable_to_refresh")
    pool = _epic_pool(monkeypatch, uefn_popup)
    out = asyncio.run(asyncio.wait_for(pool.call_tool("unreal__call_tool", _GET_SESSION_STATUS), 30))
    assert out.startswith('{"sessionStatus": "Edit"}')
    assert len(uefn_popup["clicks"]) == 1
    assert "pressed UEFN's popup 'Unable to Refresh' → its only button" in out
    assert "uefn_popup_1.png" in out  # the agent can still read what UEFN said


def test_a_timed_out_epic_call_tells_the_agent_to_look_and_press(uefn_popup, monkeypatch) -> None:
    uefn_popup["show"]("Unable to Launch", "data_loss_warning")  # two buttons: not pressed for it
    pool = _epic_pool(monkeypatch, uefn_popup, timeout=0.3)
    with pytest.raises(RuntimeError) as err:
        asyncio.run(pool.call_tool("unreal__call_tool", _GET_SESSION_STATUS))
    text = str(err.value)
    assert "'Unable to Launch'" in text
    assert "`uefn_popups`" in text and "`uefn_popup_press(hwnd, n)`" in text and "uefn_window_capture" in text
    assert text.index("uefn_popups") < text.index("fallback tool")  # look first, fall back after
    # The agent follows it: the capture numbers the buttons, it presses one.
    listed = popups.describe_popup(popups.list_popups()[0], save=False)
    assert len(listed["buttons"]) == 2
    assert popups.press_button(listed["hwnd"], 1)["closed"] is True


def test_the_timeout_hint_without_a_popup_still_says_where_to_look(monkeypatch) -> None:
    monkeypatch.setattr(popups, "list_popups", lambda: [])
    hint = popups.blocked_call_hint()
    assert hint.startswith("UEFN may be behind a popup") and "`uefn_popups`" in hint
