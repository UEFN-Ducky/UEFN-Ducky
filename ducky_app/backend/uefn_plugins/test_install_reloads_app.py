"""A plugin installed from the agents' tool server is loaded in the app process too, and two
processes installing at once take turns."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from backend.uefn_plugins import store
from frontend.ui_web import agent_modes


class _SyncThread:
    def __init__(self, target, args=(), **_kwargs):
        self._target = target
        self._args = args

    def start(self) -> None:
        self._target(*self._args)


def _capture_posts(monkeypatch) -> list[tuple[str, list]]:
    posts: list[tuple[str, list]] = []

    class _Reply:
        def read(self) -> bytes:
            return b'{"ok": true}'

    def _urlopen(req, timeout=0):
        posts.append((req.full_url, json.loads(req.data)["args"]))
        return _Reply()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setattr(threading, "Thread", _SyncThread)
    return posts


def test_tool_server_install_asks_the_app_to_reload(monkeypatch) -> None:
    posts = _capture_posts(monkeypatch)
    monkeypatch.setattr(agent_modes, "_in_bridge_process", lambda: True)
    store._reload_in_app_process("island-media-kit")
    assert len(posts) == 1
    url, args = posts[0]
    assert url.endswith("/__panel_api/reload_uefn_plugin")
    assert args == ["island-media-kit"]


def test_the_app_itself_does_not_call_itself(monkeypatch) -> None:
    posts = _capture_posts(monkeypatch)
    monkeypatch.setattr(agent_modes, "_in_bridge_process", lambda: False)
    store._reload_in_app_process("island-media-kit")
    assert posts == []


APP_DIR = str(Path(__file__).resolve().parents[2])
# A plugin whose declared folders moved reloads before its next call: tested with the
# team-sync engine, in Ducky Account.


# --------------------------------------------------------------------------- an install in the app reaches the tool server


def test_the_tool_server_reloads_a_plugin_the_app_installed_again(monkeypatch) -> None:
    """Oct 23 2026: the app updated Web Browser to 1.0.34 and the agents' tool server kept
    listing 1.0.33's tools, so browser_video_frames never appeared."""
    from backend.uefn_plugins import host
    from backend.util.file_lock import held_file_lock

    pid = "files-probe"
    root = store.plugin_dir(pid)
    root.mkdir(parents=True)
    manifest = root / "plugin.json"
    manifest.write_text("{}", encoding="utf-8")
    reloaded: list[str] = []
    monkeypatch.setattr(host, "reload_single_plugin", reloaded.append)
    monkeypatch.setitem(host._LOADED_SIGNATURE, pid, host._manifest_signature(root))
    assert host.reload_plugins_changed_on_disk() == []  # nothing installed since
    os.utime(manifest, ns=(1, 1))  # installed again by another process
    writing, done = threading.Event(), threading.Event()

    def installing() -> None:
        with held_file_lock(store.PLUGIN_INSTALL_LOCK, 5.0):
            writing.set()
            done.wait(5)

    other = threading.Thread(target=installing)
    other.start()
    assert writing.wait(5)
    try:
        assert host.reload_plugins_changed_on_disk() == []  # never mid-install
    finally:
        done.set()
        other.join(5)
    assert host.reload_plugins_changed_on_disk() == [pid]
    assert reloaded == [pid]
    assert host.reload_plugins_changed_on_disk() == []  # once


# --------------------------------------------------------------------------- two processes install at once


def _zip(pid: str, version: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps({
            "id": pid, "kind": "plugin", "version": version, "label": pid, "default_enabled": False,
            "backend": {"entry": "backend", "register": "register"},
        }))
        zf.writestr("backend/__init__.py", "def register(api):\n    pass\n")
        zf.writestr(f"skills/{pid}/SKILL.md", f"---\nname: {pid}\ndescription: tips\n---\n\n# Tips\n")
    return buf.getvalue()


# The other process (the app while an agent's tool server installs, or the other way round):
# the same UEFN install, whose first file write takes two seconds.
_OTHER_INSTALL = r'''
import json, sys, time
sys.path.insert(0, {app!r})
from pathlib import Path
from backend.uefn_plugins import store

real = store._write_bytes_retry
seen = {{}}

def slow(path, payload, **kw):
    if not seen:
        seen["in"] = time.time()
        Path({marker!r}).write_text("in")
        time.sleep(2.0)
        seen["out"] = time.time()
    return real(path, payload, **kw)

store._write_bytes_retry = slow
out = store.import_plugin_from_bytes(bytes.fromhex(sys.stdin.read()), source="local", replace=True, replace_old=True)
print(json.dumps({{"ok": out.get("ok"), "error": out.get("error"), **seen}}), flush=True)
'''


def test_two_installs_started_at_once_from_two_processes_take_turns(monkeypatch, tmp_path: Path) -> None:
    """Oct 20 2026: an agent's update of UEFN and the Store page's ran at once in two processes,
    each removed the old parts the other was replacing, and the PC was left with no UEFN."""
    for part in ("animation", "modeling"):
        assert store.import_plugin_from_bytes(_zip(part, "1.0.0"), source="local", replace=True)["ok"]
    marker = tmp_path / "other-in"
    payload = _zip("uefn", "1.1.0")
    other = subprocess.Popen(
        [sys.executable, "-c", _OTHER_INSTALL.format(app=APP_DIR, marker=str(marker))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env={**os.environ},
    )
    try:
        other.stdin.write(payload.hex())
        other.stdin.close()
        deadline = time.monotonic() + 60
        while not marker.exists():
            assert other.poll() is None and time.monotonic() < deadline, "the other install never started"
            time.sleep(0.02)
        real = store._write_bytes_retry
        mine: dict[str, float] = {}

        def timed(path, payload, **kw):
            mine.setdefault("in", time.time())
            return real(path, payload, **kw)

        monkeypatch.setattr(store, "_write_bytes_retry", timed)
        here = store.import_plugin_from_bytes(payload, source="local", replace=True, replace_old=True)
        theirs = json.loads(other.stdout.readline())
    finally:
        other.kill()
        other.wait(10)
    assert theirs["ok"] and here["ok"], (theirs, here)
    # This install started writing only once the other had finished writing, never in the middle.
    assert mine["in"] > theirs["out"], (mine, theirs)
    assert store.load_plugin_manifest("uefn")["version"] == "1.1.0"
    assert not store.plugin_dir("animation").exists() and not store.plugin_dir("modeling").exists()
