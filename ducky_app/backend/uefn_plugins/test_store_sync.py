"""Store enable/disable/install/uninstall must not wedge on hung unload / boot load."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


def _zip_plugin(plugin_id: str = "wedge", version: int = 1) -> bytes:
    manifest = {
        "id": plugin_id,
        "kind": "plugin",
        "version": version,
        "label": "Wedge",
        "description": "sync wedge test",
        "default_enabled": False,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("backend/__init__.py", "def register(api):\n    pass\n")
    return buf.getvalue()


def test_invalidate_plugin_runtime_bounds_hanging_unload() -> None:
    from backend.uefn_plugins.plugin_host import boot, lifecycle, state
    from backend.uefn_plugins.plugin_host.testing import host_patched, patch_host

    mod = ModuleType("uefn_plugin_hangunload")
    started = threading.Event()

    def _hang() -> None:
        started.set()
        time.sleep(60)

    mod.unload = _hang  # type: ignore[attr-defined]
    sys.modules["uefn_plugin_hangunload"] = mod
    try:
        t0 = time.perf_counter()
        lifecycle.invalidate_plugin_runtime("hangunload", unload_timeout=0.25)
        elapsed = time.perf_counter() - t0
        assert started.wait(1.0), "unload() never started"
        assert elapsed < 2.0, f"invalidate blocked on unload: {elapsed:.2f}s"
    finally:
        sys.modules.pop("uefn_plugin_hangunload", None)


def test_ensure_plugins_loaded_timeout_returns_false() -> None:
    from backend.uefn_plugins.plugin_host import boot, lifecycle, state
    from backend.uefn_plugins.plugin_host.testing import host_patched, patch_host

    with state._LOAD_START_LOCK:
        was_loaded = boot._LOADED
        was_thread = boot._LOAD_THREAD
        boot._LOADED = False
        state._LOAD_DONE.clear()
        boot._LOAD_THREAD = threading.Thread(
            target=lambda: time.sleep(60),
            daemon=True,
            name="uefn-plugins-load-fake",
        )
        boot._LOAD_THREAD.start()

    try:
        with host_patched("ensure_plugins_loaded_async", lambda **_k: None):
            t0 = time.perf_counter()
            ready = boot.ensure_plugins_loaded(timeout=0.2)
            elapsed = time.perf_counter() - t0
        assert ready is False
        assert elapsed < 1.5, f"ensure_plugins_loaded ignored timeout: {elapsed:.2f}s"
    finally:
        with state._LOAD_START_LOCK:
            boot._LOADED = was_loaded
            boot._LOAD_THREAD = was_thread
            if was_loaded:
                state._LOAD_DONE.set()


def test_uninstall_returns_fast_and_removes_dir(monkeypatch) -> None:
    """Disk + settings clear synchronously; hung reload/skills must not stick the bridge."""

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        monkeypatch.setenv("LOCALAPPDATA", tmp)
        monkeypatch.setenv("USERPROFILE", tmp)
        monkeypatch.setenv("HOME", tmp)

        from backend.uefn_plugins.store import (
            import_plugin_from_bytes,
            is_plugin_installed,
            plugin_dir,
            set_uefn_plugin_enabled,
            uninstall_uefn_plugin,
        )
        from backend.uefn_plugins.plugin_host import boot, lifecycle, state
        from backend.uefn_plugins.plugin_host.testing import host_patched

        result = import_plugin_from_bytes(_zip_plugin("wedge", 1), source="local", replace=True)
        assert result.get("ok"), result
        assert set_uefn_plugin_enabled("wedge", True, trust_local=True).get("ok")
        # Let enable bg finish so modules are importable, then uninstall.
        lifecycle.wait_plugin_toggles(timeout=5.0)
        dest = plugin_dir("wedge")
        assert dest.is_dir()

        hang = threading.Event()

        def _hanging_reload(plugin_id: str) -> None:
            hang.wait(60)

        with host_patched("reload_single_plugin", _hanging_reload):
            t0 = time.perf_counter()
            out = uninstall_uefn_plugin("wedge", erase_data=False)
            elapsed = time.perf_counter() - t0

        assert out.get("ok"), out
        assert elapsed < 3.0, f"uninstall blocked on teardown: {elapsed:.2f}s"
        assert not is_plugin_installed("wedge")
        assert not dest.exists()
        hang.set()  # release daemon so the process can exit cleanly


def test_reload_single_plugin_bounds_boot_wait() -> None:
    from backend.uefn_plugins.plugin_host import boot, lifecycle, state
    from backend.uefn_plugins.plugin_host.testing import host_patched, patch_host

    calls: list[float] = []

    def _slow_wait(timeout: float = 30.0) -> None:
        calls.append(timeout)
        time.sleep(min(0.01, timeout))

    def _ensure(timeout: float | None = None) -> bool:
        calls.append(-1.0 if timeout is None else float(timeout))
        return False

    with (
        host_patched("wait_plugin_toggles", _slow_wait),
        host_patched("ensure_plugins_loaded", _ensure),
        host_patched("invalidate_plugin_runtime", lambda *_a, **_k: None),
        host_patched("get_enabled_plugin_ids", lambda: []),
    ):
        t0 = time.perf_counter()
        lifecycle.reload_single_plugin("missing-plugin-xyz")
        elapsed = time.perf_counter() - t0

    assert calls == [5.0, 10.0], calls
    assert elapsed < 2.0


def _zip_hanging_register(plugin_id: str = "hangreg") -> bytes:
    manifest = {
        "id": plugin_id,
        "kind": "plugin",
        "version": 1,
        "label": "Hang Register",
        "description": "register() blocks forever",
        "default_enabled": False,
        "backend": {"entry": "backend", "register": "register"},
    }
    backend = (
        "import time\n"
        "def register(api):\n"
        "    time.sleep(600)\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("backend/__init__.py", backend)
    return buf.getvalue()


def test_hung_register_does_not_wedge_store_toggles(monkeypatch) -> None:
    """ensure_plugins_loaded / disable / invalidate stay fast while register() hangs."""

    # ignore_cleanup_errors: hung register() keeps the .py module mapped on Windows.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        monkeypatch.setenv("LOCALAPPDATA", tmp)
        monkeypatch.setenv("USERPROFILE", tmp)
        monkeypatch.setenv("HOME", tmp)

        from backend.uefn_plugins.store import (
            import_plugin_from_bytes,
            set_uefn_plugin_enabled,
        )
        from backend.uefn_plugins.plugin_host import boot, lifecycle, state
        from backend.uefn_plugins.plugin_host.testing import host_patched

        # Quiet sibling that must still be unloadable while hangreg is stuck in register().
        other = import_plugin_from_bytes(_zip_plugin("other", 1), source="local", replace=True)
        assert other.get("ok"), other

        hung = import_plugin_from_bytes(_zip_hanging_register("hangreg"), source="local", replace=True)
        assert hung.get("ok"), hung

        # Do not call the real enable path (that would hang a toggle thread on register).
        # Persist enabled=True in settings + mark host as post-boot with hangreg missing.
        from frontend.settings import PanelSettings, replace

        settings = PanelSettings.load()
        settings = replace(
            settings,
            enabled_uefn_plugins=["hangreg"],
            trusted_local_uefn_plugins=["hangreg", "other"],
        )
        settings.save()

        with state._LOCK:
            state._REGISTERED.discard("hangreg")
            state._REGISTERED.discard("other")
        boot._LOADED = True
        boot._UI_READY = True
        state._LOAD_DONE.set()
        state._REPAIR_ATTEMPTS.clear()
        with state._REPAIR_THREAD_LOCK:
            boot._REPAIR_THREAD = None

        t0 = time.perf_counter()
        assert boot.ensure_plugins_loaded() is True
        assert time.perf_counter() - t0 < 1.0, "ensure_plugins_loaded blocked on repair"

        # Give the repair daemon a moment to enter the hung register().
        deadline = time.monotonic() + 15.0
        while "hangreg" not in state._REPAIR_ATTEMPTS and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "hangreg" in state._REPAIR_ATTEMPTS

        # Second ensure within backoff must not re-kick hangreg.
        first_attempt = state._REPAIR_ATTEMPTS["hangreg"]
        assert boot.ensure_plugins_loaded() is True
        time.sleep(0.05)
        assert state._REPAIR_ATTEMPTS.get("hangreg") == first_attempt

        t1 = time.perf_counter()
        disabled = set_uefn_plugin_enabled("hangreg", False)
        assert disabled.get("ok"), disabled
        assert time.perf_counter() - t1 < 2.0, "disable blocked while register hung"

        t2 = time.perf_counter()
        lifecycle.invalidate_plugin_runtime("other", unload_timeout=0.5)
        assert time.perf_counter() - t2 < 2.0, "invalidate blocked while register hung"


def test_reload_single_plugin_register_is_async() -> None:
    """Install-path reload must return before a hung register() finishes."""
    from backend.uefn_plugins.plugin_host import boot, lifecycle, state
    from backend.uefn_plugins.plugin_host.testing import host_patched, patch_host

    started = threading.Event()

    def _hanging_load(pid: str, root: Path, manifest: dict) -> None:
        started.set()
        time.sleep(60)

    fake_root = Path(tempfile.gettempdir()) / "uefn-reloadhang-fake"
    fake_root.mkdir(parents=True, exist_ok=True)
    manifest = {"id": "reloadhang", "kind": "plugin", "version": 1, "label": "Reload Hang"}

    with (
        host_patched("wait_plugin_toggles", lambda timeout=30.0: None),
        host_patched("ensure_plugins_loaded", lambda timeout=None: True),
        host_patched("invalidate_plugin_runtime", lambda *_a, **_k: None),
        patch(
            "backend.uefn_plugins.store.load_plugin_manifest",
            lambda _pid: dict(manifest),
        ),
        patch(
            "backend.uefn_plugins.store.plugin_dir",
            lambda _pid: fake_root,
        ),
        host_patched("get_enabled_plugin_ids", lambda: ["reloadhang"]),
        host_patched("is_plugin_enabled", lambda _pid: True),
        host_patched("_load_enabled_plugin", _hanging_load),
        host_patched("_load_one", lambda *_a, **_k: None),
        host_patched("_notify_uefn_plugins_changed", lambda: None),
        host_patched("_strip_contributions_for", lambda _pid: None),
    ):
        t0 = time.perf_counter()
        lifecycle.reload_single_plugin("reloadhang")
        elapsed = time.perf_counter() - t0

    assert elapsed < 2.0, f"reload_single_plugin blocked on register: {elapsed:.2f}s"
    assert started.wait(2.0), "background register never started"


def test_rmtree_retry_recovers_from_winerror_32() -> None:
    from backend.uefn_plugins import store as st

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        target = Path(tmp) / "verse"
        target.mkdir()
        (target / "modules.md").write_text("x", encoding="utf-8")
        hits = {"n": 0}
        real = shutil.rmtree

        def flaky(path, *a, **k):
            hits["n"] += 1
            if hits["n"] == 1:
                err = OSError(32, "The process cannot access the file")
                err.winerror = 32
                err.filename = str(Path(path) / "modules.md")
                raise err
            return real(path, *a, **k)

        with patch.object(st.shutil, "rmtree", flaky):
            st._rmtree_retry(target, attempts=4)
        assert not target.exists()
        assert hits["n"] >= 2


def test_parallel_plugin_replaces_do_not_overlap_their_swaps(monkeypatch) -> None:
    """Update All used to rmtree plugin B while skill-sync still had B's files open; now each
    install moves its folder aside and the new one in, one install at a time."""
    from backend.uefn_plugins import store as st

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        monkeypatch.setenv("LOCALAPPDATA", tmp)
        monkeypatch.setenv("USERPROFILE", tmp)
        monkeypatch.setenv("HOME", tmp)
        with patch.object(st, "_refresh_plugin_skills_async", lambda: None):
            assert st.import_plugin_from_bytes(
                _zip_plugin("alpha", 1), source="local", replace=True
            ).get("ok")
            assert st.import_plugin_from_bytes(
                _zip_plugin("beta", 1), source="local", replace=True
            ).get("ok")

            order: list[str] = []
            real = st._rename_retry

            def slow_rename(src, dst, **kwargs):
                order.append("start")
                time.sleep(0.12)
                real(src, dst, **kwargs)
                order.append("end")

            results: list[dict] = []

            def _replace(pid: str) -> None:
                results.append(
                    st.import_plugin_from_bytes(_zip_plugin(pid, 2), source="local", replace=True)
                )

            with patch.object(st, "_rename_retry", slow_rename):
                t1 = threading.Thread(target=_replace, args=("alpha",))
                t2 = threading.Thread(target=_replace, args=("beta",))
                t1.start()
                t2.start()
                t1.join(8)
                t2.join(8)

        assert all(r.get("ok") for r in results), results
        assert "".join(order) == "startend" * 4, order  # two renames per install, never interleaved


def test_repair_thread_starts_only_when_a_repair_is_due(monkeypatch, tmp_path) -> None:
    """Oct 10 2026: every get_contributions() (key status, model refresh, tool lists) started
    an 'uefn-plugins-repair' thread with a new database connection even when every plugin
    was registered, ~1.3 threads a second while Ducky sat idle."""
    import types

    from backend.uefn_plugins.plugin_host import boot, lifecycle, state
    from backend.uefn_plugins.plugin_host.testing import host_patched, patch_host

    clock = [1000.0]
    fake_time = types.SimpleNamespace(**{n: getattr(time, n) for n in dir(time) if not n.startswith("_")})
    fake_time.monotonic = lambda: clock[0]
    runs: list[int] = []
    real_worker = boot._repair_missing_backends_worker

    def _counting_worker() -> None:
        runs.append(1)
        real_worker()

    patch_host(monkeypatch, "time", fake_time)
    patch_host(monkeypatch, "_repair_missing_backends_worker", _counting_worker)
    patch_host(monkeypatch, "get_enabled_plugin_ids", lambda: ["alpha", "beta"])
    patch_host(monkeypatch, "appdata_uefn_plugins_dir", lambda: tmp_path)  # no plugin folders
    patch_host(monkeypatch, "_REGISTERED", {"alpha", "beta"})
    patch_host(monkeypatch, "_REPAIR_ATTEMPTS", {})
    patch_host(monkeypatch, "_REPAIR_THREAD", None)
    patch_host(monkeypatch, "_LOADED", True)

    def _ensure(times: int) -> None:
        for _ in range(times):
            assert boot.ensure_plugins_loaded() is True
            thread = boot._REPAIR_THREAD
            if thread is not None:
                thread.join(5)

    _ensure(100)
    assert runs == []

    state._REGISTERED.discard("beta")  # its register() never finished
    _ensure(100)
    assert runs == [1]
    clock[0] += state._REPAIR_BACKOFF_S
    _ensure(100)
    assert runs == [1, 1]


if __name__ == "__main__":
    test_invalidate_plugin_runtime_bounds_hanging_unload()
    test_ensure_plugins_loaded_timeout_returns_false()
    test_uninstall_returns_fast_and_removes_dir()
    test_reload_single_plugin_bounds_boot_wait()
    test_hung_register_does_not_wedge_store_toggles()
    test_reload_single_plugin_register_is_async()
    test_rmtree_retry_recovers_from_winerror_32()
    print("store_sync self-check OK")


def test_a_copy_with_no_recorded_source_takes_store_updates_but_a_local_copy_does_not(monkeypatch) -> None:
    """Oct 11 2026: Anthropic, OpenAI and Cursor were installed before Ducky recorded where a
    plugin came from. The Store showed them an Update that always failed "installed from
    'local'", so they never updated (and Claude Code never got Ask before changes)."""
    from backend.uefn_plugins import store as st

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        monkeypatch.setenv("LOCALAPPDATA", tmp)
        monkeypatch.setenv("USERPROFILE", tmp)
        monkeypatch.setenv("HOME", tmp)
        with patch.object(st, "_refresh_plugin_skills_async", lambda: None), \
                patch.object(st, "_reload_in_app_process", lambda pid: None):
            assert st.import_plugin_from_bytes(_zip_plugin("old", 1), source="local", replace=True).get("ok")
            manifest = st.plugin_dir("old") / "plugin.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data.pop("source", None)
            manifest.write_text(json.dumps(data), encoding="utf-8")
            assert st.import_plugin_from_bytes(_zip_plugin("old", 2), source="store", replace=True).get("ok")
            assert json.loads(manifest.read_text(encoding="utf-8"))["source"] == "store"

            assert st.import_plugin_from_bytes(_zip_plugin("mine", 1), source="local", replace=True).get("ok")
            refused = st.import_plugin_from_bytes(_zip_plugin("mine", 2), source="store", replace=True)
            assert not refused.get("ok") and "installed from 'local'" in refused["error"]


def _signed_in_with_a_space(monkeypatch) -> str:
    """Ana is signed in with her data key, and plugin ``cards`` shows space T (pulled once)."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import scopes, spaces
    from frontend import duckyos_account

    class Spaces(spaces.Provider):
        def enabled(self) -> bool:
            return True

    monkeypatch.setattr(duckyos_account, "account_key", lambda blob=None: "https://uefnducky.org|ana@x.org")
    monkeypatch.setattr(scopes, "_locked", lambda account: False)
    spaces.install_for_tests(Spaces())
    ana = scopes.account_id()
    repo.link_set(ana, "cards", "spaceT")
    repo.sync_put(ana, "spaceT", label="Alpha", members=3, synced_at=time.time())
    return ana


def test_scope_status_reports_each_shared_state(monkeypatch) -> None:
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import spaces

    ana = _signed_in_with_a_space(monkeypatch)
    try:
        for key, dirty in (("a", True), ("b", True), ("c", True), ("d", False)):
            repo.put(ana, "spaceT", "cards", "doc", key, value=None, size=1, sha256=key, dirty=dirty)
        repo.put(ana, "spaceT", "other", "doc", "x", value=None, size=1, sha256="x", dirty=True)  # not this bar's
        repo.set_state(ana, "spaceT", "cards", "doc", "b", "refused", "Too big for this plan")
        for _ in range(repo.BLOCK_AFTER):
            repo.note_attempt(ana, "spaceT", "cards", "doc", "c", "File missing")
        repo.set_state(ana, "spaceT", "cards", "doc", "d", "conflict", "Ana changed it too")

        status = spaces.scope_status("cards")
        assert status["visible"] and (status["state"], status["error"], status["members"]) == ("ok", "", 3)
        # Pending leaves out refused and blocked changes; those are counted and listed with their reasons.
        assert (status["pending"], status["refused"], status["blocked"], status["conflict"]) == (1, 1, 1, 1)
        assert sorted((i["key"], i["state"], i["reason"]) for i in status["items"]) == [
            ("b", "refused", "Too big for this plan"), ("c", "blocked", "File missing"),
            ("d", "conflict", "Ana changed it too")]

        for state, error, read_only in (
            ("paused", "", True),
            ("unavailable", "permission denied: teams", True),
            ("reset", "", True),
            ("error", "Sync failed: KeyError: 'rev'", False),
            ("offline", "offline", False),
            ("lost", "", True),
        ):
            repo.sync_put(ana, "spaceT", state=state, error=error)
            status = spaces.scope_status("cards")
            assert (status["state"], status["error"], status["scope"]["readOnly"], status["visible"]) == (
                state, error, read_only, True), state
        # Reset: the bar names everything Upload my copy / Clear my copy act on (the whole copy).
        repo.sync_put(ana, "spaceT", state="reset", error="")
        assert spaces.scope_status("cards")["resetContents"] == ["cards", "other"]
        repo.sync_put(ana, "spaceT", state="ok", error="", synced_at=0)
        assert (spaces.scope_status("cards")["state"], spaces.scope_status("cards")["scope"]["readOnly"]) == (
            "waiting", True)

        # No provider loaded: the copy stays writable (writes queue) and the bar says why nothing syncs.
        spaces.reset_for_tests()
        repo.sync_put(ana, "spaceT", state="error", error="old error", synced_at=time.time())
        status = spaces.scope_status("cards")
        assert (status["state"], status["error"], status["scope"]["readOnly"], status["visible"]) == (
            "no_provider", "Not connected: Ducky Account isn't running", False, True)
        repo.sync_put(ana, "spaceT", state="unavailable", error="permission denied: teams")
        assert spaces.scope_status("cards")["state"] == "unavailable"  # the provider's last word still shows
    finally:
        spaces.reset_for_tests()


def test_sync_now_without_a_provider_says_why(monkeypatch) -> None:
    from backend.uefn_plugins import spaces

    _signed_in_with_a_space(monkeypatch)
    try:
        spaces.reset_for_tests()
        assert spaces.sync_active("cards", force=True) == {
            "ok": False, "started": False, "error": "Not connected: Ducky Account isn't running"}
        assert spaces.sync_active("local-only") == {"ok": True, "started": False}
    finally:
        spaces.reset_for_tests()


def test_scope_status_says_whether_a_plugin_can_share_at_all(monkeypatch) -> None:
    from backend.uefn_plugins import spaces

    _signed_in_with_a_space(monkeypatch)
    try:
        # A Forge-like plugin keeps its own files: nothing to pick, so no bar at all.
        status = spaces.scope_status("scene-maker")
        assert (status["visible"], status["shareable"], status["scope"]["kind"]) == (False, False, "personal")
        status = spaces.scope_status("cards")
        assert (status["visible"], status["shareable"]) == (True, True)
    finally:
        spaces.reset_for_tests()
