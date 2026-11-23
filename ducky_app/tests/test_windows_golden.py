"""Windows data saved by 1.2.516 still reads the same (``fixtures/windows_golden``).

Each test copies the golden files into its own AppData and opens them through the
product's code: chat store, account sealing, API keys, plugin data, settings, update
feed. A failure here means a change altered how Windows reads what users already
have on disk. Fix the change, never the golden files.

The golden blobs are sealed with the throwaway DPAPI stand-in (``fake_dpapi.py``):
real DPAPI only opens on the PC and user that sealed it. ``test_real_dpapi_round_trip``
covers the OS call itself.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "windows_golden"
sys.path.insert(0, str(GOLDEN))

import fake_dpapi  # noqa: E402
import make_golden as g  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows data on a Windows PC")


def _canon(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class _Login:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from frontend import duckyos_account

        real = duckyos_account.account_key
        self.key = ""
        monkeypatch.setattr(duckyos_account, "account_key", lambda blob=None: self.key if blob is None else real(blob))

    def be(self, key: str) -> None:
        self.key = key


@pytest.fixture
def golden(monkeypatch: pytest.MonkeyPatch) -> _Login:
    """Today's ducky.db in this test's AppData, signed out, golden DPAPI in place."""
    from backend.agent import secrets
    from backend.store.repos import chats
    from backend.uefn_plugins import data_crypto
    from frontend.app_paths import resolve_app_data_dir

    chats.forget_parts()  # payloads another test opened are memoized per process
    shutil.copy2(GOLDEN / "ducky.db", resolve_app_data_dir(for_write=True) / "ducky.db")
    fake_dpapi.install(monkeypatch.setattr)
    data_crypto.reset_for_tests()
    secrets.clear_memory_cache()
    monkeypatch.setattr(data_crypto, "_fetch_adk", lambda: g.ADK)
    yield _Login(monkeypatch)
    data_crypto.reset_for_tests()
    secrets.clear_memory_cache()


def test_signed_out_chats_read(golden: _Login) -> None:
    from backend.store.repos import chats

    assert {c["id"] for c in chats.conv_list(g.PROJECT)} == set(g.LOCAL_CHATS)
    for conv_id, (title, messages) in g.LOCAL_CHATS.items():
        doc = chats.conv_get(conv_id, project_id=g.PROJECT, with_messages=False)
        assert doc is not None and doc["title"] == title
        assert _canon(chats.messages_get(conv_id)) == _canon(messages)


def test_sealed_chat_reads(golden: _Login) -> None:
    from backend.store.repos import chats

    conv_id, title, messages = g.SEALED_CHAT
    assert chats.conv_get(conv_id) is None  # signed out: the account's chat stays hidden
    golden.be(g.ACCOUNT_KEY)
    doc = chats.conv_get(conv_id, project_id=g.PROJECT, with_messages=False)
    assert doc is not None and doc["title"] == title
    assert _canon(chats.messages_get(conv_id)) == _canon(messages)
    assert conv_id in {hit["conv_id"] for hit in chats.search_messages(g.PROJECT, "pelican")}


def test_sealed_chat_needs_its_account_key(golden: _Login, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.store import db
    from backend.uefn_plugins import data_crypto, scopes

    golden.be(g.ACCOUNT_KEY)
    account = scopes.account_id()
    (body,) = db.connect().execute(
        "SELECT body FROM messages WHERE conv_id=? ORDER BY seq LIMIT 1", (g.SEALED_CHAT[0],)
    ).fetchone()
    assert data_crypto.is_sealed(body) and "pelican" not in body
    assert "pelican" in data_crypto.open_text(body, account)
    data_crypto.reset_for_tests()
    monkeypatch.setattr(data_crypto, "_fetch_adk", lambda: b"\x00" * 32)
    with pytest.raises(OSError):
        data_crypto.open_text(body, account)


def test_api_key_reads(golden: _Login) -> None:
    from backend.agent import secrets

    assert secrets.get_key(g.SECRET_NAME) == g.SECRET_VALUE
    assert secrets.get_key_fresh(g.SECRET_NAME) == g.SECRET_VALUE


def test_plan_and_workflow_read(golden: _Login) -> None:
    from backend.store.repos import automations, plans

    assert plans.plan_get(g.PROJECT, g.PLAN_KEY) == g.PLAN
    (row,) = [w for w in automations.list_all() if w["id"] == g.WORKFLOW["id"]]
    assert {k: row[k] for k in g.WORKFLOW} == g.WORKFLOW


def test_plugin_data_reads(golden: _Login) -> None:
    from frontend.ui_web import plugin_host_api as pha

    assert pha.cache_get(g.PLUGIN_ID, g.PLUGIN_LOCAL[0]) == g.PLUGIN_LOCAL[1]
    assert pha.cache_get(g.PLUGIN_ID, g.PLUGIN_ACCOUNT[0]) == {}  # not the signed-out user's
    golden.be(g.ACCOUNT_KEY)
    assert pha.cache_get(g.PLUGIN_ID, g.PLUGIN_ACCOUNT[0]) == g.PLUGIN_ACCOUNT[1]


def test_settings_rows_read(golden: _Login) -> None:
    from frontend.settings import PanelSettings

    s = PanelSettings.load()
    assert {k: getattr(s, k) for k in g.DB_SETTINGS} == g.DB_SETTINGS


def test_settings_file_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PC from before ducky.db: only panel_settings.json, imported on first load."""
    from frontend.app_paths import resolve_app_data_dir
    from frontend.settings import PanelSettings

    shutil.copy2(GOLDEN / "panel_settings.json", resolve_app_data_dir(for_write=True) / "panel_settings.json")
    s = PanelSettings.load()
    assert {k: getattr(s, k) for k in g.FILE_SETTINGS} == g.FILE_SETTINGS


def _feed() -> dict:
    return json.loads((GOLDEN / "update_feed.json").read_text(encoding="utf-8"))


def test_old_update_record_reads() -> None:
    from frontend import app_stage, version_check

    payload = version_check.unwrap_collect_payload({"payload": {"handled": True, "payload": _feed()}})
    assert version_check._extract_remote_version(payload) == g.FEED["currentVersion"]
    pack = app_stage.feed_update(payload, current=g.GOLDEN_VERSION)
    assert pack is not None and pack["packUrl"] == g.FEED["update"]["packUrl"]


def test_old_update_record_picks_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Today's record has no per-platform fields: an installed Windows copy still gets
    the record's installer and its hash."""
    from frontend import install_info, version_check

    monkeypatch.setenv(version_check.UPDATE_BASE_URL_ENV, "https://uefnducky.org")
    monkeypatch.setattr(version_check, "__version__", g.GOLDEN_VERSION)
    monkeypatch.setattr(version_check, "fetch_remote_payload", lambda **_: (_feed(), None))
    monkeypatch.setattr(version_check, "update_channel", lambda *, installed: "installed")
    monkeypatch.setattr(version_check, "is_packaged_runtime", lambda: True)
    monkeypatch.setattr(version_check, "_background_update_stuck", lambda: False)
    monkeypatch.setattr(install_info, "get_install_info", lambda: {
        "installed": True, "install_location": r"C:\GoldenPC\UEFN-Ducky", "install_scope": "user",
    })
    status = version_check._app_update_status()
    assert status["feed_status"] == "update_available" and status["update_available"] is True
    assert status["remote_version"] == g.FEED["currentVersion"]
    assert status["installer_url"] == "https://uefnducky.org" + g.FEED["installerUrl"]
    assert status["installer_sha256"] == g.FEED["installerSha256"]


def test_golden_db_names_no_person() -> None:
    assert g.personal_hits(GOLDEN / "ducky.db") == []


def test_real_dpapi_round_trip() -> None:
    """The OS call the stand-in replaces: today's envelope, sealed and opened for real."""
    from backend.agent import secrets

    blob = secrets.protect_text("golden value")
    assert blob[:5] == b"UMCP\x01" and secrets.unprotect_text(blob) == "golden value"
    sealed = secrets.protect_bytes(b"payload", b"adk:" + g.ADK)
    assert secrets.unprotect_bytes(sealed, b"adk:" + g.ADK) == b"payload"
    with pytest.raises(OSError):
        secrets.unprotect_bytes(sealed, b"adk:" + b"\x00" * 32)


# --- the Windows dev workflow (dev_workflow.json) -------------------------------------


@pytest.fixture(scope="module")
def dev_golden() -> dict:
    return json.loads((GOLDEN / "dev_workflow.json").read_text(encoding="utf-8"))


def test_build_exes_runs_with_no_arguments_as_today(dev_golden) -> None:
    import dev_workflow

    now = dev_workflow.build_exes()
    was = dev_golden["build_exes"]
    assert now["required"] == [], "build_exes.py must keep building Windows with no arguments"
    assert now["defaults"] == was["defaults"], "a default changed: Windows builds differently"
    assert set(was["options"]) <= set(now["options"]), "an option the person uses is gone"
    for key in ("stale_patterns", "stale_patterns_dev", "launcher_exe", "output_lines"):
        assert now[key] == was[key], f"Windows build output names changed ({key})"


def test_running_from_source_keeps_its_modes(dev_golden) -> None:
    import dev_workflow

    now = dev_workflow.launcher()
    assert now["from_source"] is True, "python -m frontend is how the person runs from source"
    missing = set(dev_golden["launcher"]["modes"]) - set(now["modes"])
    assert not missing, f"launcher modes gone: {sorted(missing)}"


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
def test_windows_installs_the_same_packages(dev_golden, name) -> None:
    import dev_workflow

    now, was = set(dev_workflow.windows_requirements(name)), set(dev_golden["requirements"][name])
    assert not now - was, (
        f"{name}: Windows now installs {sorted(now - was)}. A Linux-only package needs "
        "'; sys_platform == \"linux\"'; a new Windows one means rebuilding dev_workflow.json on purpose."
    )
    assert not was - now, f"{name}: Windows no longer installs {sorted(was - now)}"


def test_the_same_tests_run_on_windows(dev_golden) -> None:
    import dev_workflow

    assert dev_workflow.pytest_setup()["testpaths"] == dev_golden["pytest"]["testpaths"]
    collected = dev_workflow.collected_tests()
    assert collected >= dev_golden["pytest"]["collected"], (
        f"Windows collects {collected} tests, {dev_golden['pytest']['collected']} on the golden day"
    )
