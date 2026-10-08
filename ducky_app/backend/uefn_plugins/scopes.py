"""Plugin data scopes + the host data service (team plans P3/P4).

A scope is ``(account, personal | team_id)``:

- the account is the signed-in DuckyOS user (a stable hash of site + email), or
  ``_local`` when signed out;
- where a plugin's data lives is picked per plugin (``plugin_scopes``, plan §15.1):
  Local (scope ``personal``) or exactly one team. No pick, or team storage
  unavailable for this account → Local. Teams never share rows: switching shows
  that scope's own copy, and the only way data moves is an explicit copy of the
  Local copy into one team (:func:`copy_local_into`).

Plugins never pick their own folder or rows: docs (one JSON doc per entity) and
files go through :class:`PluginData`, which writes the active scope only. Rows
and folders are keyed by account and sealed for it (:mod:`data_crypto`), so
another account on this PC can't read them. Team scopes sync through
:mod:`backend.uefn_plugins.team_sync`; Personal and ``sensitive`` data never leave
the PC.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

LOCAL = "_local"
PERSONAL = "personal"
DOC_MAX_BYTES = 1024 * 1024
ASSET_MAX_BYTES = 100 * 1024 * 1024
# A team copy whose access was lost stays locked on this PC this long, then goes.
LOST_KEEP_S = 7 * 24 * 3600

_PLUGIN_RE = re.compile(r"^[a-z0-9._-]{1,64}$")
_DOC_RE = re.compile(r"^[a-z0-9._-]{1,128}$")
_ASSET_RE = re.compile(r"^[a-z0-9._/-]{1,256}$")
_TEAM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ReadOnlyScope(PermissionError):
    """Team Private is paused: the team's copy is read-only until the owner renews."""


# --------------------------------------------------------------------------- ids


def valid_plugin_id(pid: str) -> bool:
    return bool(_PLUGIN_RE.match(pid or "")) and not pid.startswith(".") and ".." not in pid


def valid_doc_key(key: str) -> bool:
    return bool(_DOC_RE.match(key or "")) and not key.startswith(".") and ".." not in key


def valid_asset_path(path: str) -> bool:
    """``[a-z0-9._/-]`` ≤256, every segment non-empty, none starting with a dot, never ``..``."""
    return (
        bool(_ASSET_RE.match(path or ""))
        and ".." not in path
        and all(seg and not seg.startswith(".") for seg in path.split("/"))
    )


def valid_team_id(team: str) -> bool:
    return bool(_TEAM_RE.match(team or "")) and team not in (PERSONAL, LOCAL)


def _need(ok: bool, what: str) -> None:
    if not ok:
        raise ValueError(f"invalid {what}")


# --------------------------------------------------------------------------- scope


def account_id_for(key: str) -> str:
    """Row/folder id of an account key (``site|email``); ``_local`` when signed out."""
    return LOCAL if not key else "a_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def account_id() -> str:
    """Stable per-account id for rows and folders; ``_local`` when signed out.
    Read fresh per call (the login row, not a process cache), so the MCP bridge
    follows an account switch made in the app."""
    from frontend.duckyos_account import account_key

    aid = account_id_for(account_key())
    _claim_legacy(aid)
    return aid


def _claim_legacy(aid: str) -> None:
    """Rows from before scopes (migration 0009) join the first account that opens them."""
    from backend.store.repos import plugin_kv

    if plugin_kv.has_unclaimed():
        plugin_kv.claim_unclaimed(aid)


def _locked(account: str) -> bool:
    """Signed in, but this account's data key isn't on the PC yet (offline first sign-in)."""
    from backend.uefn_plugins.data_crypto import available

    return not available(account)


def personal_scope(account: str | None = None) -> dict[str, Any]:
    account = account or account_id()
    locked = _locked(account)
    # Shown as "Local": it never leaves this PC (the id stays "personal").
    return {"account": account, "id": PERSONAL, "kind": "personal", "label": "Local",
            "teamId": "", "readOnly": locked, "state": "locked" if locked else "ok"}


def team_scope(account: str, team: str) -> dict[str, Any]:
    """A team scope. ``waiting``: never pulled on this PC for this account, so it is
    read-only until the first pull lands — a plugin must not seed its empty-state
    defaults over the team's data. ``locked``: the account's data key is missing.
    ``lost``: the account lost access to the team; its copy here is unreadable and
    read-only until access comes back, and goes after :data:`LOST_KEEP_S`."""
    from backend.store.repos import plugin_data as repo

    sync = repo.sync_get(account, team)
    state = sync["state"]
    if state == "lost":
        if lost_expired(account, team):
            # A week without access: the copy on this PC goes (the server's stays).
            purge_team(account, team)
            state = "unavailable"
    elif state != "unavailable" and _locked(account):
        state = "locked"
    elif not sync["synced_at"] and state not in ("paused", "unavailable"):
        state = "waiting"
    return {"account": account, "id": team, "kind": "team", "label": sync["label"] or "Team", "teamId": team,
            "readOnly": state in ("paused", "waiting", "locked", "lost"), "state": state}


def _lost_key(account: str, team: str) -> str:
    return f"team_access_lost.{account}.{team}"


def lost_since(account: str, team: str) -> float:
    """When this PC learned the account lost access to ``team`` (0: it hasn't)."""
    from backend.store.repos import kv

    try:
        return float(kv.meta_get(_lost_key(account, team)) or 0)
    except ValueError:
        return 0.0


def mark_lost(account: str, team: str, since: float | None = None) -> None:
    """Access to ``team`` is gone: keep the first time this PC saw it (the clock of the purge)."""
    from backend.store.repos import kv

    if not lost_since(account, team):
        kv.meta_set(_lost_key(account, team), repr(time.time() if since is None else since))


def clear_lost(account: str, team: str) -> None:
    from backend.store.repos import kv

    kv.meta_set(_lost_key(account, team), "")


def lost_expired(account: str, team: str) -> bool:
    since = lost_since(account, team)
    return bool(since) and time.time() - since >= LOST_KEEP_S


def active_scope(plugin: str) -> dict[str, Any]:
    """The scope ``plugin``'s data reads and writes right now."""
    from backend.store.repos import plugin_data as repo

    account = account_id()
    if account == LOCAL:
        return personal_scope(account)
    team = repo.link_get(account, plugin)
    if team == PERSONAL or not valid_team_id(team):
        return personal_scope(account)
    scope = team_scope(account, team)
    # Teams beta or team storage gone for this account: Local only, no teaser.
    return personal_scope(account) if scope["state"] == "unavailable" else scope


def scope_view(scope: dict[str, Any]) -> dict[str, Any]:
    """What panels and tool results see: label, kind, read-only (no account id)."""
    return {"kind": scope["kind"], "label": scope["label"], "teamId": scope["teamId"], "readOnly": scope["readOnly"]}


def scope_name(scope: dict[str, Any]) -> str:
    """``team Alpha Studio`` / ``local`` — how tool results name the copy they changed."""
    return f"team {scope['label']}" if scope["kind"] == "team" else "local"


def link_plugin(plugin: str, scope_id: str, *, label: str = "", members: int = 0) -> dict[str, Any]:
    """Keep ``plugin``'s data in Local or in one of the account's teams. Nothing moves:
    the plugin shows that scope's own copy from now on."""
    from backend.store.repos import plugin_data as repo

    _need(valid_plugin_id(plugin), "plugin id")
    account = account_id()
    if account == LOCAL:
        raise ValueError("Sign in to share a plugin's data with a team")
    if scope_id != PERSONAL:
        _need(valid_team_id(scope_id), "team id")
        old = repo.sync_get(account, scope_id)
        # A fresh link tries again even if an earlier round found the team unavailable.
        repo.sync_put(account, scope_id, label=label or old["label"], members=int(members or old["members"]),
                      state="ok" if old["state"] == "unavailable" else old["state"], error="")
    repo.link_set(account, plugin, scope_id)
    return active_scope(plugin)


def copy_local_into(plugin: str, team: str) -> dict[str, Any]:
    """One way, on request: the plugin's Local docs and files go into ``team``'s copy
    (queued to sync). A team item with the same key is kept as it is. Sensitive docs
    stay Local. Team → team never happens."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins.data_crypto import open_bytes, open_text, seal_bytes, seal_text

    _need(valid_plugin_id(plugin), "plugin id")
    _need(valid_team_id(team), "team id")
    account = account_id()
    if account == LOCAL:
        raise ValueError("Sign in to share a plugin's data with a team")
    src = personal_scope(account)
    target = team_scope(account, team)
    if target["state"] == "waiting":
        # Never pulled on this PC: get the team's copy first, so "keep theirs" is true.
        from backend.uefn_plugins.team_sync import first_pull

        first_pull(account, team)
        target = team_scope(account, team)
    dst = PluginData(plugin)._writable(target)
    copied = kept = 0
    for kind in ("doc", "asset"):
        for row in repo.rows(account, PERSONAL, plugin, kind):
            if row["sensitive"]:
                continue
            old = repo.get(account, team, plugin, kind, row["key"])
            if old and not old["deleted"]:
                kept += 1
                continue
            if kind == "doc":
                if row["value"] is None:
                    continue
                value = seal_text(open_text(row["value"], account), account)
            else:
                source = asset_file(src, plugin, row["key"])
                if not source.is_file():
                    continue
                write_asset_bytes(asset_file(dst, plugin, row["key"]), seal_bytes(open_bytes(source.read_bytes(), account), account))
                value = None
            repo.put(account, team, plugin, kind, row["key"], value=value, size=int(row["size"]), sha256=row["sha256"],
                     dirty=True)
            copied += 1
    return {"ok": True, "copied": copied, "kept": kept}


def scopes_root(account: str) -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir() / "scopes" / account


def plugin_dir(scope: dict[str, Any], plugin: str) -> Path:
    """``%LOCALAPPDATA%/UEFN-Ducky/scopes/{account}/{personal|team_id}/{plugin_id}/``."""
    _need(valid_plugin_id(plugin), "plugin id")
    return scopes_root(scope["account"]) / scope["id"] / plugin


def purge_team(account: str, team: str) -> None:
    """The team's local copy goes, rows and files: a week after access was lost (or
    at once when this PC held nothing of it). Never the server's copy."""
    from backend.store.repos import plugin_data as repo
    from backend.store.repos import plugin_kv

    if not valid_team_id(team):
        return
    from backend.store.repos import workflows

    team_workflows = [r["key"] for r in repo.rows(account, team, "ducky.automations", "doc")]
    workflows.runtime_delete(account, team_workflows)
    repo.delete_scope(account, team)
    plugin_kv.delete_scope(account, team)
    shutil.rmtree(scopes_root(account) / team, ignore_errors=True)
    if lost_since(account, team):
        clear_lost(account, team)


def erase_plugin(plugin: str) -> None:
    """Uninstall + erase data: this account's docs and files of ``plugin`` in every scope."""
    from backend.store.repos import plugin_data as repo

    account = account_id()
    repo.delete_plugin(account, plugin)
    root = scopes_root(account)
    if root.is_dir():
        for scope_dir in root.iterdir():
            shutil.rmtree(scope_dir / plugin, ignore_errors=True)


# --------------------------------------------------------------------------- host data API


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encode_doc(value: Any) -> bytes:
    """Canonical doc bytes: what is hashed, pushed and stored."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def asset_file(scope: dict[str, Any], plugin: str, path: str) -> Path:
    _need(valid_asset_path(path), "file path")
    return plugin_dir(scope, plugin) / "files" / Path(*path.split("/"))


def write_asset_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(target)


def _closed(scope: dict[str, Any]) -> bool:
    """Access to the team is lost: its copy here reads as empty until access comes back."""
    return scope["state"] == "lost"


class PluginData:
    """One plugin's docs and files in the active scope (``api.data`` for backends,
    ``data.*`` / ``files.*`` for panels). Each call resolves the scope afresh, so a
    project or account switch is picked up without a plugin reload."""

    def __init__(self, plugin_id: str, *, personal: bool = False) -> None:
        _need(valid_plugin_id(plugin_id), "plugin id")
        self.plugin_id = plugin_id
        self._personal = personal

    # -- scope

    def personal(self) -> "PluginData":
        """The same plugin pinned to the account's Personal scope (one-time imports of
        old local data land here, whatever the open project is linked to)."""
        return PluginData(self.plugin_id, personal=True)

    def _scope(self) -> dict[str, Any]:
        if self._personal:
            return personal_scope()
        s = active_scope(self.plugin_id)
        if s["state"] == "waiting":
            # First use of a team scope on this PC: pull before the plugin sees it (bounded).
            from backend.uefn_plugins.team_sync import first_pull

            if first_pull(s["account"], s["id"]):
                s = active_scope(self.plugin_id)
        return s

    def scope(self) -> dict[str, Any]:
        return scope_view(self._scope())

    def _writable(self, scope: dict[str, Any] | None = None) -> dict[str, Any]:
        s = scope or self._scope()
        if s["state"] == "locked":
            raise ReadOnlyScope("Waiting for this account's data key. Plugin data opens once you're online.")
        if s["state"] == "waiting":
            raise ReadOnlyScope(f"Waiting for team data from {s['label']}. Read-only until it arrives.")
        if s["state"] == "lost":
            raise ReadOnlyScope(f"You no longer have access to {s['label']}. Its data on this PC is locked.")
        if s["readOnly"]:
            raise ReadOnlyScope(f"Team Private is paused for {s['label']}. Read-only.")
        return s

    # -- docs (sealed for the account at rest, plan §13; sha256/size are of the plaintext)

    def get(self, key: str, default: Any = None, *, sensitive: bool = False) -> Any:
        from backend.store.repos import plugin_data as repo
        from backend.uefn_plugins.data_crypto import Locked, open_text

        _need(valid_doc_key(key), "doc key")
        s = personal_scope() if sensitive else self._scope()
        if _closed(s):
            return default
        row = repo.get(s["account"], s["id"], self.plugin_id, "doc", key)
        if not row or row["deleted"] or row["value"] is None:
            return default
        try:
            return json.loads(open_text(row["value"], s["account"]))
        except Locked:
            return default

    def put(self, key: str, value: Any, *, sensitive: bool = False) -> dict[str, Any]:
        """Write one doc. ``sensitive=True`` keeps it in Personal, never synced."""
        from backend.store.repos import plugin_data as repo
        from backend.uefn_plugins.data_crypto import seal_text

        _need(valid_doc_key(key), "doc key")
        s = self._writable(personal_scope() if sensitive else None)
        raw = encode_doc(value)
        if len(raw) > DOC_MAX_BYTES:
            raise ValueError(f"doc {key!r} is over 1 MB; split it into one doc per entity")
        sha = _sha(raw)
        old = repo.get(s["account"], s["id"], self.plugin_id, "doc", key)
        if old and not old["deleted"] and old["sha256"] == sha:
            return {"ok": True, "key": key, "changed": False}
        repo.put(s["account"], s["id"], self.plugin_id, "doc", key, value=seal_text(raw.decode("utf-8"), s["account"]),
                 size=len(raw), sha256=sha, dirty=s["kind"] == "team" and not sensitive, sensitive=sensitive)
        return {"ok": True, "key": key, "changed": True}

    def keys(self, prefix: str = "") -> list[str]:
        from backend.store.repos import plugin_data as repo

        s = self._scope()
        if _closed(s):
            return []
        return [r["key"] for r in repo.rows(s["account"], s["id"], self.plugin_id, "doc", prefix) if not r["sensitive"]]

    def items(self, prefix: str = "") -> dict[str, Any]:
        """All docs under ``prefix`` in one read: ``{key: value}``."""
        from backend.store.repos import plugin_data as repo

        from backend.uefn_plugins.data_crypto import Locked, open_text

        s = self._scope()
        out: dict[str, Any] = {}
        if _closed(s):
            return out
        try:
            for r in repo.rows(s["account"], s["id"], self.plugin_id, "doc", prefix):
                if not r["sensitive"] and r["value"] is not None:
                    out[r["key"]] = json.loads(open_text(r["value"], s["account"]))
        except Locked:
            return {}
        return out

    def delete(self, key: str) -> bool:
        from backend.store.repos import plugin_data as repo

        _need(valid_doc_key(key), "doc key")
        s = self._writable()
        old = repo.get(s["account"], s["id"], self.plugin_id, "doc", key)
        if not old or old["deleted"]:
            return False
        repo.remove(s["account"], s["id"], self.plugin_id, "doc", key, tombstone=s["kind"] == "team" and old["rev"] > 0)
        return True

    # -- files (assets)

    def put_file(self, path: str, data: bytes) -> dict[str, Any]:
        from backend.store.repos import plugin_data as repo
        from backend.uefn_plugins.data_crypto import seal_bytes

        s = self._writable()
        target = asset_file(s, self.plugin_id, path)
        if len(data) > ASSET_MAX_BYTES:
            raise ValueError(f"file {path!r} is over 100 MB")
        sha = _sha(data)
        old = repo.get(s["account"], s["id"], self.plugin_id, "asset", path)
        if old and not old["deleted"] and old["sha256"] == sha and target.is_file():
            return {"ok": True, "path": path, "changed": False}
        write_asset_bytes(target, seal_bytes(data, s["account"]))
        repo.put(s["account"], s["id"], self.plugin_id, "asset", path, value=None, size=len(data), sha256=sha,
                 dirty=s["kind"] == "team")
        return {"ok": True, "path": path, "changed": True, "size": len(data)}

    def has_file(self, path: str) -> bool:
        from backend.store.repos import plugin_data as repo

        s = self._scope()
        if _closed(s):
            return False
        row = repo.get(s["account"], s["id"], self.plugin_id, "asset", path)
        return bool(row and not row["deleted"] and asset_file(s, self.plugin_id, path).is_file())

    def get_file(self, path: str) -> bytes | None:
        """A stored file's bytes (sealed on disk, so there is no path to hand out)."""
        from backend.uefn_plugins.data_crypto import Locked, open_bytes

        s = self._scope()
        if not self.has_file(path):
            return None
        try:
            return open_bytes(asset_file(s, self.plugin_id, path).read_bytes(), s["account"])
        except Locked:
            return None

    def files(self, prefix: str = "") -> list[dict[str, Any]]:
        from backend.store.repos import plugin_data as repo

        s = self._scope()
        if _closed(s):
            return []
        return [{"path": r["key"], "size": r["size"], "sha256": r["sha256"]}
                for r in repo.rows(s["account"], s["id"], self.plugin_id, "asset", prefix)]

    def delete_file(self, path: str) -> bool:
        from backend.store.repos import plugin_data as repo

        s = self._writable()
        target = asset_file(s, self.plugin_id, path)
        old = repo.get(s["account"], s["id"], self.plugin_id, "asset", path)
        if not old or old["deleted"]:
            return False
        target.unlink(missing_ok=True)
        repo.remove(s["account"], s["id"], self.plugin_id, "asset", path,
                    tombstone=s["kind"] == "team" and old["rev"] > 0)
        return True


def panel_call(plugin_id: str, op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Panel bridge entry (``data.*`` / ``files.*`` / ``scope.get``): one dispatcher, JSON in and out."""
    p = params or {}
    data = PluginData(plugin_id)
    if op == "scope.get":
        return {"ok": True, "scope": data.scope()}
    if op == "data.get":
        return {"ok": True, "key": p.get("key"), "value": data.get(str(p.get("key") or ""))}
    if op == "data.put":
        return data.put(str(p.get("key") or ""), p.get("value"))
    if op == "data.list":
        prefix = str(p.get("prefix") or "")
        return {"ok": True, "items": data.items(prefix)} if p.get("values") else {"ok": True, "keys": data.keys(prefix)}
    if op == "data.delete":
        return {"ok": True, "deleted": data.delete(str(p.get("key") or ""))}
    if op == "files.put":
        raw = base64.b64decode(str(p.get("b64") or ""), validate=True)
        return data.put_file(str(p.get("path") or ""), raw)
    if op == "files.get":
        blob = data.get_file(str(p.get("path") or ""))
        return {"ok": blob is not None, "b64": base64.b64encode(blob).decode("ascii") if blob is not None else None}
    if op == "files.list":
        return {"ok": True, "files": data.files(str(p.get("prefix") or ""))}
    if op == "files.delete":
        return {"ok": True, "deleted": data.delete_file(str(p.get("path") or ""))}
    raise ValueError(f"unknown data op: {op}")
