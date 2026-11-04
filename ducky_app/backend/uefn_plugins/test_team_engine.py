"""team_engine: with no provider every call answers its default and nothing raises; with
an engine the calls pass through."""

from __future__ import annotations

import ast
import importlib
import inspect
import types
from pathlib import Path

import pytest

from backend.uefn_plugins import spaces, team_engine
from backend.uefn_plugins.team_engine import AREAS, NO_ENGINE


@pytest.fixture(autouse=True)
def _no_provider():
    spaces.reset_for_tests()
    yield
    spaces.reset_for_tests()


_SPECIAL = {"acquire": ("acct", "sp", "pl", None, [("doc:a", "A")]), "LiveApi": ("pl",)}


def test_no_provider_every_call_answers_its_default():
    for area in AREAS:
        for name in area.names():
            getattr(area, name)(*_SPECIAL.get(name, ("acct", "sp", "pl", "unit")))  # never raises


def test_no_provider_examples():
    assert team_engine.status("acct", "sp", "island-media-kit")["state"] == "no_provider"
    assert team_engine.status("acct", "sp", "x")["error"] == spaces.NO_PROVIDER_ERROR
    assert team_engine.vcs_engine.pull_copy("a", "s", "p", None, label="L") == (False, team_engine.NEEDS_ACCOUNT)
    assert team_engine.vcs_engine.acquire("a", "s", "p", None, [("doc:x", "X")])["doc:x"]["state"] == "no_provider"
    assert team_engine.space_vcs.head_get("a", "s", "p")["engine"] == "v1"
    assert team_engine.team_folders.plugin_folders("p") == []
    with team_engine.team_folders.call_marker("p", actor="user"):
        pass
    assert team_engine.live.status("p") == team_engine.LIVE_OFF
    api = team_engine.live.LiveApi("p")
    assert api.join()["state"] == "no_provider" and api.member_states() == [] and api.on("t", print) is None
    assert team_engine.sync_scheduler.run_round("a", "s", force=True)["changed"] == []
    assert team_engine.folder_files.LOCAL_ACCOUNT == "_pc"
    assert team_engine.sync_scheduler.clamp_team_settings("x", 9999) == (10.0, 600.0)


def test_defaults_are_fresh_copies():
    a = team_engine.space_vcs.head_get("a", "s", "p")
    a["engine"] = "v2"
    assert team_engine.space_vcs.head_get("a", "s", "p")["engine"] == "v1"
    NO_ENGINE_before = dict(NO_ENGINE)
    team_engine.vcs_engine.run("a", "s", "p", None)["state"] = "mutated"
    assert NO_ENGINE == NO_ENGINE_before


def test_unknown_name_is_an_attribute_error():
    with pytest.raises(AttributeError):
        team_engine.vcs_engine.no_such_function  # noqa: B018


class _Engine(spaces.Provider):
    def __init__(self, eng):
        self._eng = eng

    def engine(self):
        return self._eng


def test_engine_calls_pass_through():
    seen = []
    eng = types.SimpleNamespace(
        vcs_engine=types.SimpleNamespace(release=lambda *a, **k: seen.append((a, k)) or {"ok": True}),
        live=types.SimpleNamespace(status=lambda plugin, **k: {"on": True, "plugin": plugin}),
        status=lambda account, space, plugin: {"state": "ok", "plugin": plugin},
    )
    spaces.install_for_tests(_Engine(eng))
    assert team_engine.vcs_engine.release("a", "s", "p", None, ["doc:x"]) == {"ok": True}
    assert seen == [(("a", "s", "p", None, ["doc:x"]), {})]
    assert team_engine.live.status("p") == {"on": True, "plugin": "p"}
    assert team_engine.status("a", "s", "p") == {"state": "ok", "plugin": "p"}
    # a name the engine lacks (an older plugin) falls back to its default
    assert team_engine.space_vcs.locks("a", "s", "p") == []


def test_engine_errors_pass_through_but_lookup_errors_do_not():
    def boom(*a, **k):
        raise ValueError("engine says no")

    spaces.install_for_tests(_Engine(types.SimpleNamespace(vcs_engine=types.SimpleNamespace(release=boom))))
    with pytest.raises(ValueError):
        team_engine.vcs_engine.release("a", "s", "p", None, [])

    class _Broken(spaces.Provider):
        def engine(self):
            raise RuntimeError("provider broke")

    spaces.install_for_tests(_Broken())
    assert team_engine.vcs_engine.release("a", "s", "p", None, [])["state"] == "no_provider"


_MOVED = {
    "backend.uefn_plugins.vcs_engine", "backend.uefn_plugins.team_folders", "backend.uefn_plugins.live",
    "backend.uefn_plugins.sync_scheduler", "backend.store.repos.space_vcs", "backend.store.repos.folder_files",
}
_APP = Path(__file__).resolve().parents[2]  # ducky_app


def _app_uses() -> set[tuple[str, str, str]]:
    """``(module, name, file)`` for every name the app's own code (not tests) takes from the
    six moved module paths: ``from <path> import name`` and ``<alias>.name``."""
    out: set[tuple[str, str, str]] = set()
    own = {str(_APP / "backend" / "uefn_plugins" / "team_engine.py")} | {
        str(_APP.joinpath(*m.split(".")[0:]).with_suffix(".py")) for m in _MOVED}
    for py in _APP.rglob("*.py"):
        if py.name.startswith("test_") or py.name == "conftest.py" or str(py) in own:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        bound: dict[str, set[str]] = {}  # a local name -> every module some import binds it to
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    if node.module in _MOVED:
                        out.add((node.module, a.name, py.name))
                    full = f"{node.module}.{a.name}"
                    bound.setdefault(a.asname or a.name, set()).add(full)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    bound.setdefault(a.asname or a.name.split(".")[0], set()).add(a.name if a.asname else "")
        moved = {name: mods.pop() for name, mods in bound.items() if len(mods) == 1 and next(iter(mods)) in _MOVED}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in moved:
                out.add((moved[node.value.id], node.attr, py.name))
    return out


def test_every_name_the_app_takes_from_a_moved_module_is_there_without_the_engine():
    """With Ducky Account's engine gone, each name app code uses still resolves (the facade's
    default or core's own): a missing one would be an AttributeError in the product."""
    uses = _app_uses()
    assert len(uses) > 50  # the scan found the callers
    missing = sorted(f"{mod}.{name} ({where})" for mod, name, where in uses
                     if not hasattr(importlib.import_module(mod), name))
    assert missing == []


def test_the_old_paths_still_import_for_plugins():
    from backend.uefn_plugins.team_folders import current_project_root, declaration_errors, project_id_for

    assert isinstance(current_project_root(), str)
    assert project_id_for("C:/Islands/My Island!") == "my_island"
    assert declaration_errors({"team_folders": [{"id": "s", "root": "C:/x", "unit": "*"}]})
    for mod in _MOVED:
        importlib.import_module(mod)


def test_the_plugin_live_api_keeps_its_shape():
    """``api.live`` is the plugin API: these names and signatures never change."""
    names = {n for n in dir(team_engine.LiveApi) if not n.startswith("_")}
    assert names == set(team_engine.LIVE_API)
    sig = {n: str(inspect.signature(getattr(team_engine.LiveApi, n))) for n in team_engine.LIVE_API}
    assert sig == {
        "join": "(self, room: 'str' = '') -> 'dict[str, Any]'",
        "leave": "(self) -> 'dict[str, Any]'",
        "send": "(self, topic: 'str', data: 'Any', to: 'str' = '') -> 'dict[str, Any]'",
        "on": "(self, topic: 'str', callback: 'Callable[[dict[str, str], Any], Any]') -> 'None'",
        "state": "(self) -> 'dict[str, Any]'",
        "set_my_state": "(self, state: 'dict[str, Any] | None') -> 'dict[str, Any]'",
        "member_states": "(self) -> 'list[dict[str, Any]]'",
        "on_member_state": "(self, callback: 'Callable[[dict[str, str], Any], Any]') -> 'None'",
        "share": "(self, key: 'str', value: 'Any') -> 'dict[str, Any]'",
        "shared": "(self) -> 'dict[str, dict[str, Any]]'",
        "on_shared": "(self, callback: 'Callable[[str, Any, dict[str, Any]], Any]') -> 'None'",
    }


def test_live_listeners_added_before_the_engine_reach_it_when_it_comes(monkeypatch):
    """A plugin that loads before Ducky Account: what it asked of ``api.live`` happens once the
    engine is here; listeners of a plugin that reloaded meanwhile are dropped."""
    team_engine._PENDING.clear()
    api = team_engine.live.LiveApi("early")
    assert api.join()["state"] == "no_provider" and api.member_states() == [] and api.shared() == {}
    with pytest.raises(TypeError):
        api.on("topic", "not a function")
    got: list[tuple] = []
    api.on("topic", print)
    api.on_shared(print)
    team_engine.live.LiveApi("gone").on_member_state(print)
    team_engine.live.forget("gone")

    class Real:
        def __init__(self, plugin_id):
            self.plugin_id = plugin_id

        def on(self, topic, callback):
            got.append((self.plugin_id, "on", topic))

        def on_shared(self, callback):
            got.append((self.plugin_id, "on_shared"))

        def on_member_state(self, callback):
            got.append((self.plugin_id, "on_member_state"))

        def join(self, room=""):
            return {"ok": True, "room": room}

    eng = types.SimpleNamespace(live=types.SimpleNamespace(LiveApi=Real, forget=lambda *a, **k: None))
    from backend.uefn_plugins.plugin_host.testing import patch_host

    patch_host(monkeypatch, "bind_folders_after_engine", lambda: None)
    spaces.install_for_tests(_Engine(eng))
    team_engine.engine_arrived()
    assert got == [("early", "on", "topic"), ("early", "on_shared")]
    assert api.join("r") == {"ok": True, "room": "r"}  # the same api object now goes live
    api.on("later", print)
    assert got[-1] == ("early", "on", "later")


def test_remove_tree_and_long_path(tmp_path):
    d = tmp_path / "copy"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "f.txt").write_text("x")
    assert team_engine.team_folders.remove_tree(d) is True
    assert not d.exists()
    assert team_engine.team_folders.remove_tree(tmp_path / "missing") is True
    assert team_engine.team_folders._lp(tmp_path).endswith(str(tmp_path.name))
