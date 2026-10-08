"""Team data sync (plan §7, P3 docs + P4 assets): one batched Store call per team.

A round pushes the scope's queued changes (≤100), uploads accepted bytes to the
presigned PUT URLs, commits them, and pulls everything since the cursor from
presigned GET URLs (sha256 checked). Last write wins in server order: a stale
push adopts the server's copy.

Everything uploaded is encrypted with the team's own key (:mod:`team_keys`), so the
size and sha256 the server gets describe ciphertext; local rows keep the
plaintext's for change detection. No key, no round: nothing leaves the PC in
plaintext. Objects from before team keys still read, and any item the server
still holds in plaintext is pushed again, encrypted (the first round with a key
pulls the whole team once to find them).

Losing access to a team locks its copy here (unreadable and read-only) instead of
deleting it; it unlocks when the key can be fetched again, and leaves this PC after
a week without access. The team's copy on the server is never touched.

Load (the 2026-09-16 outage was a desktop poller holding every plugin slot): a
round runs only while a team-scoped plugin panel is open (the host scope bar asks
on open, on focus and each minute), at most once a minute per team, and never
per document. Offline writes stay queued (``dirty``) for the next round.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from backend.store.repos import plugin_data as repo
from backend.uefn_plugins import scopes, team_keys

MIN_INTERVAL_S = 60.0
WORKFLOW_DOCS = "ducky.automations"
MAX_PUSHES = 100
# ponytail: pages per round are capped so one round stays bounded; the next round
# continues from the saved cursor.
MAX_PAGES = 20
TRANSFER_TIMEOUT_S = 300.0

_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class SyncError(Exception):
    def __init__(self, message: str, *, offline: bool = False) -> None:
        super().__init__(message)
        self.offline = offline


# --------------------------------------------------------------------------- transport


class Transport:
    """Store collect calls as the signed-in account + presigned URL transfers.
    Tests pass a fake with the same three methods."""

    def collect(self, event: str, body: dict[str, Any]) -> dict[str, Any]:
        from frontend.duckyos_account import DuckyOSAccountError, api_request

        try:
            status, parsed, _raw = api_request(
                "POST", f"/api/v1/plugins/uefn-ducky-store/collect/{event}", body, timeout=60.0
            )
        except DuckyOSAccountError as exc:
            raise SyncError(exc.message, offline=True) from exc
        payload = parsed.get("payload") if isinstance(parsed, dict) else None
        payload = payload if isinstance(payload, dict) else (parsed or {})
        if 200 <= int(status) < 300 and not payload.get("error"):
            return payload
        err = str(payload.get("error") or (parsed or {}).get("error") or f"HTTP {status}")
        raise SyncError(err, offline=int(status) >= 500)

    def put(self, url: str, data: bytes) -> None:
        req = urllib.request.Request(
            url, data=data, method="PUT", headers={"Content-Length": str(len(data))}
        )
        try:
            with urllib.request.urlopen(req, timeout=TRANSFER_TIMEOUT_S) as resp:
                resp.read()
        except (OSError, urllib.error.URLError) as exc:
            raise SyncError(f"upload failed: {exc}", offline=True) from exc

    def get(self, url: str) -> bytes:
        try:
            with urllib.request.urlopen(url, timeout=TRANSFER_TIMEOUT_S) as resp:
                return resp.read()
        except (OSError, urllib.error.URLError) as exc:
            raise SyncError(f"download failed: {exc}", offline=True) from exc


# --------------------------------------------------------------------------- one round


def _item(d: dict[str, Any]) -> tuple[str, str, str] | None:
    kind, pid, key = str(d.get("kind") or "doc"), str(d.get("pluginId") or ""), str(d.get("key") or "")
    ok = scopes.valid_plugin_id(pid) and (
        (kind == "doc" and scopes.valid_doc_key(key)) or (kind == "asset" and scopes.valid_asset_path(key))
    )
    return (kind, pid, key) if ok else None


def _local_bytes(scope: dict[str, Any], row: dict[str, Any]) -> bytes | None:
    """The row's plaintext (sealed on this PC only, plan §13)."""
    from backend.uefn_plugins.data_crypto import open_bytes, open_text

    if row["kind"] == "doc":
        return None if row["value"] is None else open_text(row["value"], scope["account"]).encode("utf-8")
    path = scopes.asset_file(scope, row["plugin_id"], row["key"])
    return open_bytes(path.read_bytes(), scope["account"]) if path.is_file() else None


def _drop_local(scope: dict[str, Any], kind: str, pid: str, key: str) -> None:
    repo.remove(scope["account"], scope["id"], pid, kind, key, tombstone=False)
    if kind == "asset":
        scopes.asset_file(scope, pid, key).unlink(missing_ok=True)


def _store(scope: dict[str, Any], kind: str, pid: str, key: str, data: bytes, rev: int) -> None:
    from backend.uefn_plugins.data_crypto import seal_bytes, seal_text

    sha = hashlib.sha256(data).hexdigest()
    if kind == "asset":
        scopes.write_asset_bytes(scopes.asset_file(scope, pid, key), seal_bytes(data, scope["account"]))
        repo.put(scope["account"], scope["id"], pid, kind, key, value=None, size=len(data), sha256=sha,
                 dirty=False, rev=rev)
    else:
        repo.put(scope["account"], scope["id"], pid, kind, key, value=seal_text(data.decode("utf-8"), scope["account"]),
                 size=len(data), sha256=sha, dirty=False, rev=rev)
    _land_own(scope, kind, pid, key, data)


def _sealed(scope: dict[str, Any], row: dict[str, Any], tk: tuple[int, bytes]) -> bytes | None:
    """What a push of ``row`` uploads: its plaintext as it is now, encrypted with the
    team key. ``None`` when the local copy is gone, unreadable, or changed since
    ``row`` was read."""
    from backend.uefn_plugins.data_crypto import Locked

    try:
        data = _local_bytes(scope, row)
    except (Locked, OSError, ValueError):
        return None
    if data is None or hashlib.sha256(data).hexdigest() != row["sha256"]:
        return None
    return team_keys.seal(tk, scope["id"], (row["kind"], row["plugin_id"], row["key"]), data)


def _download(t: Transport, scope: dict[str, Any], it: tuple[str, str, str], item: dict[str, Any],
              tk: tuple[int, bytes]) -> bytes:
    """Item ``it``'s plaintext: the stored object (sha256 checked), opened with the
    team key as that item of that team."""
    data = t.get(str(item.get("getUrl") or ""))
    if hashlib.sha256(data).hexdigest() != str(item.get("sha256") or ""):
        raise SyncError("downloaded file failed its sha256 check")
    try:
        enc = int(item["enc"]) if item.get("enc") is not None else None
        return team_keys.open_object(tk, scope["id"], it, data, enc)
    except (ValueError, TypeError) as exc:
        raise SyncError(f"downloaded file could not be opened: {exc}") from exc


def _plaintext_on_server(item: dict[str, Any]) -> bool:
    """The server still stores this live item unencrypted (from before team keys)."""
    return item.get("enc") is not None and not item.get("deleted") and str(item.get("enc")) == "0"


def _reseal(scope: dict[str, Any], it: tuple[str, str, str], may_automate: bool) -> None:
    """Queue an item the server holds in plaintext: the next push uploads it encrypted,
    and the server drops its plaintext versions. Team workflows only for members who
    may change them."""
    if it[1].startswith("ducky.") and not may_automate:
        return
    repo.mark_dirty(scope["account"], scope["id"], it[1], it[0], it[2])


def share_plugin_id(account: str, plugin: str) -> str | None:
    """This person's copy of a plugin on a team: ``a_<16 hex>.<plugin>``."""
    pid = f"{account}.{plugin}"
    if re.fullmatch(r"a_[0-9a-f]{16}", account or "") and scopes.valid_plugin_id(plugin) and scopes.valid_plugin_id(pid):
        return pid
    return None


def own_base(account: str, plugin_id: str) -> str | None:
    """The plugin id inside this account's personal share, or None for anyone else's."""
    plugin = plugin_id[len(account) + 1 :] if plugin_id.startswith(account + ".") else ""
    return plugin if plugin and share_plugin_id(account, plugin) == plugin_id else None


def sidecar_root() -> Path:
    """Where plugins keep a ``db.json`` beside the host data service."""
    from backend.skills.store import appdata_dir

    return appdata_dir()


def plugin_label(plugin_id: str) -> str:
    """The plugin's display name, from its manifest. Empty when it has none."""
    base = own_base_any(plugin_id)
    try:
        from backend.uefn_plugins.store import load_plugin_manifest

        manifest = load_plugin_manifest(base) or {}
        label = str(manifest.get("label") or "").strip()
        if label and len(label) <= 80 and all(ord(c) >= 32 for c in label):
            return label
    except Exception:
        return ""
    return ""


def own_base_any(plugin_id: str) -> str:
    """``a_<16 hex>.<plugin>`` → ``<plugin>``. Any other id is unchanged."""
    m = re.fullmatch(r"a_[0-9a-f]{16}\.(.+)", plugin_id or "")
    plugin = m.group(1) if m else plugin_id
    return plugin if scopes.valid_plugin_id(plugin) else plugin_id


def _queue_doc(account: str, team: str, share: str, key: str, raw: bytes) -> None:
    from backend.uefn_plugins.data_crypto import seal_text

    if not scopes.valid_doc_key(key) or not raw or len(raw) > scopes.DOC_MAX_BYTES:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return
    sha = hashlib.sha256(raw).hexdigest()
    old = repo.get(account, team, share, "doc", key)
    if old and not old["deleted"] and old["sha256"] == sha:
        return
    repo.put(account, team, share, "doc", key, value=seal_text(text, account), size=len(raw), sha256=sha, dirty=True)


def queue_personal(account: str, team: str) -> None:
    """Queue this person's Local plugin docs, and any ``<plugin>/db.json``, onto the
    team under ``{account}.{plugin}``. Sensitive docs stay on the PC. A matching
    sha is left alone, so a quiet round does not re-upload."""
    from backend.uefn_plugins.data_crypto import Locked, open_text

    for plugin in repo.plugin_ids(account, scopes.PERSONAL):
        share = share_plugin_id(account, plugin)
        if not share:
            continue
        try:
            saved = repo.rows(account, scopes.PERSONAL, plugin, "doc")
        except Exception:
            continue
        for row in saved:
            if row["sensitive"] or row["value"] is None:
                continue
            try:
                raw = open_text(row["value"], account).encode("utf-8")
            except (Locked, ValueError, UnicodeError):
                continue
            _queue_doc(account, team, share, row["key"], raw)
    root = sidecar_root()
    if not root.is_dir():
        return
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        db = child / "db.json"
        if not child.is_dir() or not db.is_file():
            continue
        plugin = child.name.replace("_", "-").lower()
        share = share_plugin_id(account, plugin)
        if not share:
            continue
        try:
            raw = db.read_bytes()
        except OSError:
            continue
        _queue_doc(account, team, share, "db", raw)


def _write_sidecar(plugin: str, data: bytes) -> None:
    root = sidecar_root()
    underscored = root / plugin.replace("-", "_") / "db.json"
    hyphenated = root / plugin / "db.json"
    path = hyphenated if hyphenated.is_file() and not underscored.is_file() else underscored
    try:
        if path.is_file() and path.read_bytes() == data:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        return


def _land_own(scope: dict[str, Any], kind: str, pid: str, key: str, data: bytes) -> None:
    """A download of this account's personal plugin: write ``db.json`` back, and the
    Local docs the plugin reads. Someone else's rows stay in the team copy only."""
    if kind != "doc" or scope.get("id") == scopes.PERSONAL:
        return
    plugin = own_base(scope["account"], pid)
    if not plugin:
        return
    if key == "db":
        _write_sidecar(plugin, data)
    personal = {"account": scope["account"], "id": scopes.PERSONAL, "kind": "personal"}
    _store(personal, kind, plugin, key, data, 0)


def _apply_change(scope: dict[str, Any], t: Transport, ch: dict[str, Any], changed: set[str],
                  tk: tuple[int, bytes], automate: bool) -> None:
    it = _item(ch)
    if it is None:
        return
    kind, pid, key = it
    rev = int(ch.get("rev") or 0)
    local = repo.get(scope["account"], scope["id"], pid, kind, key)
    # The cursor is inclusive: rows we already hold at this rev come back; skip them.
    if local and (local["dirty"] or (local["rev"] == rev and not local["deleted"])):
        if not local["dirty"] and _plaintext_on_server(ch):
            _reseal(scope, it, automate)
        return  # a queued local change is settled by its push (stale → adopt)
    if ch.get("deleted"):
        if local:
            _drop_local(scope, kind, pid, key)
            changed.add(pid)
        return
    if not ch.get("getUrl"):
        return
    _store(scope, kind, pid, key, _download(t, scope, it, ch, tk), rev)
    changed.add(pid)
    if _plaintext_on_server(ch):
        _reseal(scope, it, automate)


def _adopt(scope: dict[str, Any], t: Transport, it: tuple[str, str, str], server: Any, changed: set[str],
           tk: tuple[int, bytes], automate: bool) -> int | None:
    """Stale push: the server's copy wins. Returns a rev to rewind the cursor to when
    the server row came without content (the next inclusive pull brings it)."""
    kind, pid, key = it
    if not isinstance(server, dict) or not server:
        # The server has no such item any more: push it again as new.
        repo.set_rev(scope["account"], scope["id"], pid, kind, key, 0)
        return None
    changed.add(pid)
    if kind == "doc" and pid == WORKFLOW_DOCS:
        # A team workflow edited here and elsewhere: keep this PC's edit in History.
        from backend.automations.owned import archive_before_adopt

        archive_before_adopt(scope, key)
    if server.get("deleted"):
        _drop_local(scope, kind, pid, key)
        return None
    if server.get("getUrl"):
        _store(scope, kind, pid, key, _download(t, scope, it, server, tk), int(server.get("rev") or 0))
        if _plaintext_on_server(server):
            _reseal(scope, it, automate)
        return None
    _drop_local(scope, kind, pid, key)
    return int(server.get("rev") or 0)


def _lock(account: str, team: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault((account, team), threading.Lock())


def sync_team(account: str, team: str, *, force: bool = False, transport: Transport | None = None,
              now: Callable[[], float] = time.time) -> dict[str, Any]:
    """One sync round for ``(account, team)``. Returns ``{state, changed, error}``."""
    if not scopes.valid_team_id(team):
        return {"state": "unavailable", "changed": [], "error": "invalid team"}
    lock = _lock(account, team)
    # A Save or Update online waits. A background round never piles up behind one.
    if not lock.acquire(blocking=force):
        return {"state": "busy", "changed": [], "error": ""}
    try:
        st = repo.sync_get(account, team)
        if not force and now() - float(st["called_at"]) < MIN_INTERVAL_S:
            return {"state": st["state"], "changed": [], "error": st["error"], "skipped": True}
        repo.sync_put(account, team, called_at=now())
        t = transport or Transport()
        out = _round(account, team, int(st["cursor_rev"]), t, now)
        # A stale push rewinds to rev 0 and stays dirty: one more round sends it as new.
        if force and out.get("state") == "ok" and repo.count_dirty(account, team):
            st = repo.sync_get(account, team)
            out = _round(account, team, int(st["cursor_rev"]), t, now)
        return out
    finally:
        lock.release()


FIRST_PULL_WAIT_S = 10.0
_FIRST_PULL_TRIED: set[tuple[str, str]] = set()


def first_pull(account: str, team: str, *, timeout: float = FIRST_PULL_WAIT_S) -> bool:
    """Pull a team scope this PC never pulled before the plugin gets its data API.

    Waits at most ``timeout`` seconds, once per scope per process: a plugin that
    seeds defaults when empty must never push them over the team's data. Offline or
    slow, the scope stays read-only ("waiting") and the round finishes in the
    background; the scope bar's minute sync keeps retrying. Returns whether the
    scope has been pulled now.
    """
    key = (account, team)
    if key in _FIRST_PULL_TRIED or not scopes.valid_team_id(team):
        return False
    _FIRST_PULL_TRIED.add(key)
    done = threading.Event()

    def _work() -> None:
        try:
            with _lock(account, team):  # a round already running for this scope finishes first
                st = repo.sync_get(account, team)
                if st["synced_at"]:
                    return
                repo.sync_put(account, team, called_at=time.time())
                out = _round(account, team, int(st["cursor_rev"]), Transport(), time.time)
            try:
                from frontend.ui_web.agent_modes import push_ui_event

                push_ui_event({"type": "plugin_scope_changed", "plugins": out.get("changed") or [], "synced": True})
            except Exception:
                pass
        finally:
            done.set()

    threading.Thread(target=_work, name="team-data-first-pull", daemon=True).start()
    done.wait(timeout)
    return bool(repo.sync_get(account, team)["synced_at"])


def _round(account: str, team: str, cursor: int, t: Transport, now: Callable[[], float]) -> dict[str, Any]:
    from backend.uefn_plugins.data_crypto import available

    if not available(account):
        # Without the account's data key nothing can be sealed or opened: wait, touch nothing.
        return {"state": "locked", "changed": [], "error": ""}
    scope = {"account": account, "id": team, "kind": "team"}
    try:
        tk = _team_key(account, team, t)
    except SyncError as exc:
        return _failed(account, team, exc, set())
    from backend.store.repos import kv, workflows

    automate = workflows.perms_get(account, team)
    sealed_mark = f"team_data_sealed.{account}.{team}"
    if kv.meta_get(sealed_mark) is None:
        # First round with the team's key on this PC: pull the whole team once, so
        # every item the server still holds in plaintext is found and pushed again.
        cursor = 0
    queue_personal(account, team)
    changed: set[str] = set()
    errors: list[str] = []
    pushes: list[dict[str, Any]] = []
    # Pushes carry metadata only: each item is encrypted here to name its size and
    # sha256, then read and encrypted again (to the same bytes) at upload.
    sent: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in repo.dirty(account, team, MAX_PUSHES):
        it = (row["kind"], row["plugin_id"], row["key"])
        push = {"kind": it[0], "pluginId": it[1], "key": it[2], "baseRev": int(row["rev"])}
        label = plugin_label(it[1])
        if label:
            push["title"] = label
        if row["deleted"]:
            push.update(size=0, sha256="", delete=True)
        else:
            sealed = _sealed(scope, row, tk)
            if sealed is None:
                continue  # gone or changed while reading: the next round pushes what is there then
            push.update(size=len(sealed), sha256=hashlib.sha256(sealed).hexdigest(), enc=tk[0])
            sent[it] = {"row": row, "sha256": push["sha256"]}
            del sealed
        pushes.append(push)
    try:
        resp = t.collect("team-data-sync", {"teamId": team, "cursorRev": cursor, "pushes": pushes,
                                            "enc": team_keys.FORMAT})
        commits = []
        for a in resp.get("accepted") or []:
            it = _item(a)
            if it is None or it not in sent:
                continue
            data = _sealed(scope, sent[it]["row"], tk)
            if data is None or hashlib.sha256(data).hexdigest() != sent[it]["sha256"]:
                continue  # changed or gone since: its upload expires, the next round pushes the new copy
            t.put(str(a.get("putUrl") or ""), data)
            commits.append({"kind": it[0], "pluginId": it[1], "key": it[2], "rev": int(a.get("rev") or 0),
                            "enc": tk[0]})
        for d in resp.get("deleted") or []:
            it = _item(d)
            if it:
                repo.mark_pushed(account, team, it[1], it[0], it[2], rev=int(d.get("rev") or 0), sha256="")
        rewind: list[int] = []
        for s in resp.get("stale") or []:
            it = _item(s)
            if it:
                back = _adopt(scope, t, it, s.get("server"), changed, tk, automate)
                if back is not None:
                    rewind.append(back)
        for r in resp.get("refused") or []:
            it = _item(r)
            if it:
                # Not shared (too big, storage full): the local copy stays, the bar says why.
                repo.clear_dirty(account, team, it[1], it[0], it[2])
                errors.append(f"{it[2]}: {r.get('error') or 'refused'}")
        usage = resp.get("usage") or {}
        pages = 0
        while True:
            for ch in resp.get("changes") or []:
                _apply_change(scope, t, ch, changed, tk, automate)
            cursor = int(resp.get("cursorRev") or cursor)
            pages += 1
            if not resp.get("more") or pages >= MAX_PAGES:
                break
            resp = t.collect("team-data-sync", {"teamId": team, "cursorRev": cursor, "enc": team_keys.FORMAT})
            usage = resp.get("usage") or usage
        if commits:
            res = t.collect("team-data-commit", {"teamId": team, "commits": commits, "enc": team_keys.FORMAT})
            usage = res.get("usage") or usage
            for r in res.get("results") or []:
                it = _item(r)
                if it is None:
                    continue
                status = r.get("status")
                if status in ("committed", "unchanged"):
                    # The local row's own (plaintext) sha: an edit made meanwhile stays queued.
                    plain_sha = str(((sent.get(it) or {}).get("row") or {}).get("sha256") or "")
                    repo.mark_pushed(account, team, it[1], it[0], it[2], rev=int(r.get("rev") or 0), sha256=plain_sha)
                elif status == "stale":
                    back = _adopt(scope, t, it, r.get("server"), changed, tk, automate)
                    if back is not None:
                        rewind.append(back)
                else:
                    errors.append(f"{it[2]}: upload not accepted, retrying")
        if rewind:
            cursor = min([cursor, *rewind])
    except SyncError as exc:
        return _failed(account, team, exc, changed)
    repo.sync_put(account, team, cursor_rev=cursor, state="ok", error="; ".join(errors)[:500], usage=usage,
                  synced_at=now())
    if kv.meta_get(sealed_mark) is None:
        kv.meta_set(sealed_mark, "1")
    if scopes.lost_since(account, team):
        # Access is back: the copy here unlocks as it was, and sync carries on.
        scopes.clear_lost(account, team)
    return {"state": "ok", "changed": sorted(changed), "error": "; ".join(errors)}


def _team_key(account: str, team: str, t: Transport) -> tuple[int, bytes]:
    """``(version, key)`` of the team's data key: kept on this PC, asked of the Store
    at most once a minute. Without it the round waits (nothing goes up in plaintext)."""
    try:
        got = team_keys.get(account, team, lambda: t.collect("team-data-key", {"teamId": team}))
    except ValueError as exc:
        raise SyncError(f"team data key: {exc}") from exc
    if got is None:
        raise SyncError("Waiting for this team's data key.", offline=True)
    return got


def _lost(account: str, team: str, changed: set[str]) -> dict[str, Any]:
    """Access to the team is gone (removed, or the team was deleted). Its key goes and
    its copy here locks: unreadable and read-only for plugins, kept as it is so it
    unlocks if access comes back (the next round that gets the key). After
    ``scopes.LOST_KEEP_S`` without access the copy on this PC is deleted; the team's
    copy on the server is never touched. A team this PC holds nothing of just goes."""
    team_keys.forget(account, team)
    held = bool(repo.plugin_ids(account, team)) or repo.count_dirty(account, team) > 0
    if not held or scopes.lost_expired(account, team):
        scopes.purge_team(account, team)
        return {"state": "removed", "changed": sorted(changed), "error": ""}
    scopes.mark_lost(account, team)
    repo.sync_put(account, team, state="lost", error="")
    return {"state": "lost", "changed": sorted(changed), "error": ""}


def _failed(account: str, team: str, exc: SyncError, changed: set[str]) -> dict[str, Any]:
    msg = str(exc)
    low = msg.lower()
    if "team not found" in low:
        return _lost(account, team, changed)
    if low.startswith("plan_paused"):
        state = "paused"
    elif "storage isn't available" in low or low.startswith("permission denied"):
        state = "unavailable"  # Personal only, silently
    else:
        state = "offline" if exc.offline else "error"
    repo.sync_put(account, team, state=state, error=msg[:500])
    return {"state": state, "changed": sorted(changed), "error": msg}


# --------------------------------------------------------------------------- host glue


def sync_active(plugin: str, *, force: bool = False,
                on_done: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Start a round for the team ``plugin``'s data lives in, on a worker thread (no-op for Local)."""
    scope = scopes.active_scope(plugin)
    if scope["kind"] != "team":
        return {"ok": True, "started": False}

    def _work() -> None:
        out = sync_team(scope["account"], scope["id"], force=force)
        if on_done and (out.get("changed") or not out.get("skipped")):
            on_done(out)

    threading.Thread(target=_work, name="team-data-sync", daemon=True).start()
    return {"ok": True, "started": True}


def scope_status(plugin: str) -> dict[str, Any]:
    """Where ``plugin``'s data lives: the host scope bar and the Plugins page. ``visible``
    is false for accounts without the Teams beta (rule 13: normal members see no change).
    ``local`` / ``current`` are the Local and the shown copy's counts and bytes."""
    from backend.store.repos import workflows
    from frontend.duckyos_account import get_status

    scope = scopes.active_scope(plugin)
    account = scope["account"]
    out: dict[str, Any] = {
        "ok": True,
        "visible": scope["kind"] == "team" or teams_enabled(),
        "scope": scopes.scope_view(scope),
        "pluginLabel": plugin_label(plugin) or plugin,
        "email": str(get_status().get("email") or ""),
        "canChange": account != scopes.LOCAL,
        "state": scope["state"],
        "local": repo.totals(account, scopes.PERSONAL, plugin),
        "current": repo.totals(account, scope["id"], plugin),
    }
    if scope["kind"] == "team":
        out["teamSlug"] = workflows.team_slug(account, scope["id"])
    if scope["kind"] == "team":
        st = repo.sync_get(account, scope["id"])
        out.update(
            members=int(st["members"]),
            state=scope["state"],
            error=st["error"],
            syncedAt=float(st["synced_at"]) or None,
            pending=repo.count_dirty(account, scope["id"]),
            usage=st["usage"] or {},
        )
        lost = scopes.lost_since(account, scope["id"]) if scope["state"] == "lost" else 0.0
        if lost:
            # When the locked copy leaves this PC unless access comes back.
            out["deleteAt"] = lost + scopes.LOST_KEEP_S
    return out


def teams_enabled() -> bool:
    """The account has the Teams beta: the last Store catalog fetched as this account said so."""
    from backend.store.repos import kv
    from frontend.duckyos_account import account_key

    key = account_key()
    doc = kv.get_doc("cache_docs", "store_catalog")
    cat = doc.get("catalog") if isinstance(doc, dict) else None
    return bool(key) and isinstance(cat, dict) and cat.get("account") == key and cat.get("teams") is True


def _memberships(*, private_only: bool) -> dict[str, Any]:
    """Local, plus teams from the Store hub. ``private_only`` keeps plugin data on
    teams whose Team Private is active. Workflows list every team you belong to."""
    from frontend.duckyos_account import teams_snapshot

    account = scopes.account_id()
    choices = [{"id": scopes.PERSONAL, "kind": "personal", "label": "Local"}]
    if account == scopes.LOCAL or (private_only and not teams_enabled()):
        return {"ok": True, "choices": choices}
    try:
        snap = teams_snapshot()
    except Exception:
        snap = {}
    from backend.store.repos import workflows

    for team in snap.get("teams") or []:
        plan = team.get("private_plan") or {}
        team_id = str(team.get("id") or "")
        if not scopes.valid_team_id(team_id):
            continue
        if private_only and plan.get("status") not in ("active", "comped"):
            continue
        perms = team.get("perms") if isinstance(team.get("perms"), dict) else {}
        slug = str(team.get("slug") or "")
        workflows.perms_put(account, team_id, manage_automations=bool(perms.get("manage_automations")), slug=slug)
        members = len(team.get("members") or [])
        label = str(team.get("name") or "Team")
        repo.sync_put(account, team_id, label=label, members=members)
        if repo.sync_get(account, team_id)["state"] == "lost":
            # The hub lists the team again: get its key back and unlock now, not at
            # the next round a panel happens to ask for.
            threading.Thread(target=sync_team, args=(account, team_id), kwargs={"force": True},
                             name="team-data-unlock", daemon=True).start()
        choices.append({"id": team_id, "kind": "team", "label": label, "members": members, "slug": slug})
    return {"ok": True, "choices": choices}


def scope_choices() -> dict[str, Any]:
    """Local + teams with Team Private active. The plugin data picker. One hub call."""
    return _memberships(private_only=True)


def remember_workflow_teams() -> dict[str, Any]:
    """Remember every team this account belongs to, so Workflows can list them."""
    return _memberships(private_only=False)


def link_scope(plugin: str, scope_id: str) -> dict[str, Any]:
    """Keep ``plugin``'s data in Local or one team (the UI confirmed first)."""
    scopes.link_plugin(plugin, scope_id)
    return scope_status(plugin)
