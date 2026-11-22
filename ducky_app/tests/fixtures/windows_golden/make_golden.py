"""Rebuild the Windows golden files: what today's Windows release (1.2.516) saves.

    python ducky_app/tests/fixtures/windows_golden/make_golden.py

Writes, next to this script, through the product's own code (never hand-made rows):

- ``ducky.db``: 3 chats (two signed out, one sealed for an account with its data key),
  a plan, a workflow, a saved API key, plugin data sealed by ``data_crypto`` (signed out
  and for the account), settings rows;
- ``panel_settings.json``: the settings file (files backend, the pre-database format);
- ``update_feed.json``: the app-version feed record as the Store serves it today.

Fake values only, sealed with the throwaway key in ``fake_dpapi.py``. The values live
below; ``test_windows_golden.py`` reads them from here. Only rebuild these on purpose
(a format that changed on purpose): the point is that old files keep reading.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parents[2]  # ducky_app

GOLDEN_VERSION = "1.2.516"
PROJECT = "golden_project"
ACCOUNT_KEY = "https://uefnducky.org|golden@example.com"
ADK = hashlib.sha256(b"windows-golden-throwaway-adk").digest()

SECRET_NAME = "golden_provider"
SECRET_VALUE = "sk-golden-0000-not-a-real-key"


def _messages(word: str, *, big: bool = False) -> list[dict]:
    result = (f"{word} notes\n" * 1500) if big else f"{word} notes"
    return [
        {"role": "user", "text": f"tell me about the {word}", "content": f"tell me about the {word}", "ts": 1.0},
        {"role": "assistant", "content": f"here is the {word}", "ts": 2.0, "blocks": [
            {"type": "tool_call", "id": "t1", "name": "workspace_read_file", "arguments": {"path": "notes.md"},
             "result": {"ok": True, "text": result}},
        ]},
    ]


# conv id -> (title, messages); LOCAL_CHATS are saved signed out, SEALED_CHAT for ACCOUNT_KEY.
LOCAL_CHATS = {
    "golden-chat-1": ("Golden chat one", _messages("lighthouse")),
    "golden-chat-2": ("Golden chat two", _messages("harbour", big=True)),
}
SEALED_CHAT = ("golden-chat-sealed", "Golden sealed chat", _messages("pelican", big=True))

PLAN_KEY = "golden-chat-1"
PLAN = {
    "id": "golden-plan", "title": "Golden plan", "status": "open", "kind": "project",
    "created_at": 1.0, "updated_at": 2.0, "overview": "a plan saved by 1.2.516",
    "nodes": [
        {"id": "n1", "content": "Diagnose", "status": "completed", "children": []},
        {"id": "n2", "content": "Fix", "status": "pending", "children": []},
    ],
}

WORKFLOW = {
    "id": "golden-workflow", "name": "Golden workflow", "enabled": True, "updated": 3.0, "last_run": 0.0,
    "description": "a workflow saved by 1.2.516",
    "graph": {"nodes": [{"id": "a", "type": "chat_input"}, {"id": "b", "type": "return_to_user"}],
              "edges": [{"from": "a", "to": "b"}]},
    "runs": [],
}

PLUGIN_ID = "golden-plugin"
PLUGIN_LOCAL = ("ui", {"tab": "cards", "count": 3})
PLUGIN_ACCOUNT = ("deck", {"cards": ["ace", "king"], "owner": "account"})

DB_SETTINGS = {"agent_model": "golden-db-model", "write_lanes_mode": "shadow", "verse_editor_enabled": False}
FILE_SETTINGS = {"agent_model": "golden-file-model", "ai_ignore_patterns": ["*.golden"], "terminals_enabled": False}

FEED = {
    "currentVersion": "1.2.517",
    "installerUrl": "/api/v1/files/golden-setup/content",
    "installerSha256": "0" * 64,
    "releaseNotes": "golden release notes",
    "versions": [{"version": "1.2.517", "changelog": "golden release notes", "created_at": None}],
    "update": {
        "version": "1.2.517",
        "packUrl": "/api/v1/files/golden-pack/content",
        "manifestUrl": "/api/v1/files/golden-manifest/content",
        "manifestSha256": "1" * 64,
    },
}

OUTPUTS = ("ducky.db", "panel_settings.json", "update_feed.json")


def _isolate(root: Path) -> None:
    """A throwaway AppData, and ports nobody listens on (never the running Ducky)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    (root / "Roaming").mkdir(parents=True)
    os.environ.update({
        "LOCALAPPDATA": str(root), "APPDATA": str(root / "Roaming"),
        "UEFN_DUCKY_DOCUMENTS_DIR": str(root / "Documents"),
        "UEFN_DUCKY_PANEL_PORT": str(port + 1), "UEFN_DUCKY_PORT": str(port),
        "DUCKY_SEARCH_AUTOINDEX": "0", "DUCKY_UPDATE_BASE_URL": "https://uefnducky.org",
    })
    for key in ("DUCKY_STORE_BACKEND", "DUCKY_STORE_BACKEND_SETTINGS"):
        os.environ.pop(key, None)
    sys.path.insert(0, str(APP))
    sys.path.insert(0, str(HERE))


def _sign_in(key: str) -> None:
    from backend.uefn_plugins import data_crypto
    from frontend import duckyos_account

    real = getattr(duckyos_account, "_golden_real_account_key", duckyos_account.account_key)
    duckyos_account._golden_real_account_key = real
    duckyos_account.account_key = lambda blob=None: key if blob is None else real(blob)
    data_crypto._fetch_adk = lambda: ADK


def _settings(values: dict) -> None:
    from dataclasses import replace

    from frontend.settings import PanelSettings

    replace(PanelSettings.load(), **values).save()


def build(out: Path) -> None:
    import fake_dpapi

    fake_dpapi.install(setattr)
    from backend.agent import secrets
    from backend.store import db
    from backend.store.repos import automations, chats, plans
    from frontend.ui_web import plugin_host_api as pha

    _sign_in("")
    _settings(DB_SETTINGS)
    secrets.set_key(SECRET_NAME, SECRET_VALUE)
    for conv_id, (title, messages) in LOCAL_CHATS.items():
        chats.conv_save(PROJECT, {"id": conv_id, "title": title, "created": 1.0, "updated": 2.0}, messages=messages)
    plans.plan_put(PROJECT, PLAN_KEY, "project", PLAN)
    automations.put(WORKFLOW)
    pha.cache_set(PLUGIN_ID, *PLUGIN_LOCAL)

    _sign_in(ACCOUNT_KEY)
    conv_id, title, messages = SEALED_CHAT
    chats.conv_save(PROJECT, {"id": conv_id, "title": title, "created": 1.0, "updated": 2.0}, messages=messages)
    pha.cache_set(PLUGIN_ID, *PLUGIN_ACCOUNT)
    _sign_in("")

    from backend.store.repos import settings as settings_repo
    from frontend.app_paths import resolve_app_data_dir

    conn = db.connect()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # The importers' markers name where they looked (this run's temp AppData, under the
    # builder's own user folder): a neutral path instead, then VACUUM drops the old pages.
    root = json.dumps(os.environ["LOCALAPPDATA"])[1:-1]
    with db.write_txn(conn):
        conn.execute("UPDATE meta SET value=replace(value, ?, ?)", (root, json.dumps(r"C:\GoldenPC\AppData\Local")[1:-1]))
    conn.execute("VACUUM")
    db.close_thread_connections()
    shutil.copy2(db.db_path(), out / "ducky.db")

    # The settings file, as the files backend writes it (what every PC had before ducky.db).
    os.environ["DUCKY_STORE_BACKEND_SETTINGS"] = "files"
    settings_repo.reset_for_tests()
    _settings(FILE_SETTINGS)
    shutil.copy2(resolve_app_data_dir() / "panel_settings.json", out / "panel_settings.json")

    (out / "update_feed.json").write_text(json.dumps(FEED, indent=2) + "\n", encoding="utf-8")


def personal_hits(path: Path) -> list[str]:
    """Rows of the golden DB that name this PC's user or home folder (must be none)."""
    import sqlite3

    words = {w for w in (Path.home().name, os.environ.get("USERNAME", ""), str(Path.home())) if len(w) > 2}
    conn = sqlite3.connect(path)
    try:
        hits = []
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for row in conn.execute(f'SELECT * FROM "{table}"').fetchall():
                text = repr(row).replace("\\\\", "\\")
                hits += [f"{table}: {text[:200]}" for w in words if w.lower() in text.lower()]
        return hits
    finally:
        conn.close()


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ducky-golden-"))
    try:
        _isolate(root)
        build(HERE)
    finally:
        try:
            from backend.store import db

            db.close_thread_connections()
        except Exception:
            pass
        shutil.rmtree(root, ignore_errors=True)
    hits = personal_hits(HERE / "ducky.db")
    if hits:
        print("personal data in ducky.db:\n" + "\n".join(hits))
        return 1
    for name in OUTPUTS:
        print(f"{name}: {(HERE / name).stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
