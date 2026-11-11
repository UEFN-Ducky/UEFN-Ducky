"""Files sealed for one Ducky account (``UDF1``): ducky.db backups from before chats were
sealed, and chat attachments while their owner isn't signed in on this PC.

A file is sealed with its own random key (AES-256-GCM, 1 MiB chunks, the last one marked so
a cut file never opens). That key is wrapped for the account with X25519: anyone can seal
for an account (its public key is in ``meta``), only the account can open (its private key
is sealed with :mod:`backend.uefn_plugins.data_crypto`, so it opens only while that account
is signed in here). Sealing and opening write a new file beside the old one and swap it in,
keeping the file's times, so an interrupted run leaves the original whole.

Chat attachments follow who is signed in (:func:`arrange_chat_files`): the signed-in
account's are plain on disk (agents read them by path), every other account's are sealed.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from backend.store import db

MAGIC = b"UDF1"
CHUNK = 1024 * 1024
TMP_SUFFIX = ".udf-tmp"  # a copy being written; swept if a crash leaves it
_PUB_META = "file_key_pub:"
_PRIV_META = "file_key_priv:"
_ARRANGED_META = "chat_files_for"
_INFO = b"ducky-file-key/1"
_LOCAL = "_local"  # backend.uefn_plugins.scopes.LOCAL: this PC signed out

_log = logging.getLogger("uefn_ducky.store.sealed_files")
_lock = threading.Lock()
_private: dict[tuple[str, str], object] = {}  # (db path, account) -> X25519PrivateKey


class WrongAccount(Exception):
    """The file is sealed for another Ducky account."""

    def __init__(self, account: str) -> None:
        super().__init__("This file is locked to another Ducky account. Sign in with that account to open it.")
        self.account = account


# --------------------------------------------------------------------------- keys


def _meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row[0])


# The keypairs live in ducky.db and in this file beside it: restoring an older backup of
# ducky.db (one from before a keypair was made) must not lock that account's files for good.
KEYS_FILE = "account_file_keys.json"


def _keys_path() -> Path:
    return db.app_root() / KEYS_FILE


def _read_keys_file() -> dict[str, dict[str, str]]:
    try:
        doc = json.loads(_keys_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in doc.items() if isinstance(v, dict)} if isinstance(doc, dict) else {}


def _write_keys_file(keys: dict[str, dict[str, str]]) -> None:
    path = _keys_path()
    tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}{TMP_SUFFIX}")
    try:
        tmp.write_text(json.dumps(keys, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _stored_pair(account: str) -> dict[str, str] | None:
    """{"pub", "priv"} of ``account`` (``priv`` sealed for it), from the file, else ducky.db."""
    pair = _read_keys_file().get(account)
    if pair and pair.get("pub") and pair.get("priv"):
        return pair
    conn = db.connect()
    pub, priv = _meta(conn, _PUB_META + account), _meta(conn, _PRIV_META + account)
    return {"pub": pub, "priv": priv} if pub and priv else None


def ensure_keypair(account: str) -> None:
    """The account's file keypair, made the first time it is signed in here (raises
    ``data_crypto.Locked`` while its data key isn't on this PC)."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

    from backend.uefn_plugins import data_crypto

    conn = db.connect()
    keys = _read_keys_file()
    if keys.get(account) and _meta(conn, _PUB_META + account) == keys[account].get("pub"):
        return
    priv = X25519PrivateKey.generate()
    fresh = {
        "pub": base64.b64encode(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii"),
        "priv": data_crypto.seal_text(
            base64.b64encode(priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode("ascii"),
            account,
        ),
    }
    # One writer at a time across processes (BEGIN IMMEDIATE): the file and ducky.db always
    # end up holding the same pair, whichever was there first.
    with db.write_txn(conn):
        keys = _read_keys_file()
        pair = keys.get(account)
        if not pair:
            pub, sealed = _meta(conn, _PUB_META + account), _meta(conn, _PRIV_META + account)
            pair = {"pub": pub, "priv": sealed} if pub and sealed else fresh
            keys[account] = pair
            _write_keys_file(keys)
        now = time.time()
        for name, value in ((_PUB_META + account, pair["pub"]), (_PRIV_META + account, pair["priv"])):
            conn.execute(
                "INSERT INTO meta(key, value, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                (name, value, now),
            )


def public_key(account: str) -> bytes | None:
    pair = _stored_pair(account)
    return None if pair is None else base64.b64decode(pair["pub"])


def _private_key(account: str):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from backend.uefn_plugins import data_crypto

    memo = (str(db.app_root()), account)
    with _lock:
        key = _private.get(memo)
    if key is not None:
        return key
    pair = _stored_pair(account)
    if pair is None:
        raise WrongAccount(account)
    key = X25519PrivateKey.from_private_bytes(base64.b64decode(data_crypto.open_text(pair["priv"], account)))
    with _lock:
        _private[memo] = key
    return key


def forget() -> None:
    """Login funnel: opened private keys go."""
    with _lock:
        _private.clear()


def _kek(shared: bytes, epk: bytes, pub: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO + epk + pub).derive(shared)


def _wrap(file_key: bytes, account: str, pub: bytes) -> dict[str, str]:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    eph = X25519PrivateKey.generate()
    epk = eph.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    kek = _kek(eph.exchange(X25519PublicKey.from_public_bytes(pub)), epk, pub)
    nonce = os.urandom(12)
    wrapped = nonce + AESGCM(kek).encrypt(nonce, file_key, account.encode("utf-8"))
    return {"epk": base64.b64encode(epk).decode("ascii"), "wrapped": base64.b64encode(wrapped).decode("ascii")}


def _unwrap(header: dict, account: str) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    priv = _private_key(account)
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    epk = base64.b64decode(header["epk"])
    wrapped = base64.b64decode(header["wrapped"])
    kek = _kek(priv.exchange(X25519PublicKey.from_public_bytes(epk)), epk, pub)
    return AESGCM(kek).decrypt(wrapped[:12], wrapped[12:], account.encode("utf-8"))


# --------------------------------------------------------------------------- one file


def is_sealed_file(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == MAGIC
    except OSError:
        return False


def _read_header(fh) -> tuple[dict, bytes]:
    if fh.read(4) != MAGIC:
        raise ValueError("file is not sealed")
    (size,) = struct.unpack(">I", fh.read(4))
    raw = fh.read(size)
    return json.loads(raw.decode("utf-8")), raw


def sealed_for(path: Path) -> str | None:
    """The account a sealed file is for, ``None`` for a plain one."""
    try:
        with open(path, "rb") as fh:
            if fh.read(4) != MAGIC:
                return None
            fh.seek(0)
            return str(_read_header(fh)[0].get("account") or "")
    except (OSError, ValueError):
        return None


def _nonce(i: int) -> bytes:
    return b"\0" * 4 + struct.pack(">Q", i)


# Each chunk: last-chunk flag (1 byte), ciphertext length (4), ciphertext. The flag, the
# chunk's number and the header are authenticated, so chunks can't be dropped or moved.
_CHUNK_HEAD = struct.Struct(">?I")


def _aad(header_raw: bytes, i: int, last: bool) -> bytes:
    return header_raw + struct.pack(">Q?", i, last)


def _encrypt_stream(src, dst, file_key: bytes, header_raw: bytes) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    box = AESGCM(file_key)
    i = 0
    chunk = src.read(CHUNK)
    while True:
        following = src.read(CHUNK) if len(chunk) == CHUNK else b""
        last = not following
        sealed = box.encrypt(_nonce(i), chunk, _aad(header_raw, i, last))
        dst.write(_CHUNK_HEAD.pack(last, len(sealed)))
        dst.write(sealed)
        if last:
            return
        chunk, i = following, i + 1


def _decrypt_stream(src, file_key: bytes, header_raw: bytes) -> Iterator[bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    box = AESGCM(file_key)
    i = 0
    while True:
        head = src.read(_CHUNK_HEAD.size)
        if len(head) != _CHUNK_HEAD.size:
            raise ValueError("sealed file is cut short")
        last, size = _CHUNK_HEAD.unpack(head)
        body = src.read(size)
        if len(body) != size:
            raise ValueError("sealed file is cut short")
        yield box.decrypt(_nonce(i), body, _aad(header_raw, i, last))
        if last:
            if src.read(1):
                raise ValueError("sealed file has bytes after its end")
            return
        i += 1


def _swap_in(tmp: Path, path: Path, times: os.stat_result) -> None:
    _replace(tmp, path)
    try:
        os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
    except OSError:
        pass


def _tmp_for(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}{TMP_SUFFIX}")


def seal_file(path: Path, account: str, pub: bytes | None = None) -> bool:
    """Seal ``path`` in place for ``account``. False when it already is sealed (or the
    account has no keypair yet)."""
    path = Path(path)
    pub = pub if pub is not None else public_key(account)
    if pub is None or is_sealed_file(path):
        return False
    times = path.stat()
    file_key = os.urandom(32)
    header_raw = json.dumps({"v": 1, "account": account, **_wrap(file_key, account, pub)}).encode("utf-8")
    tmp = _tmp_for(path)
    try:
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            dst.write(MAGIC + struct.pack(">I", len(header_raw)) + header_raw)
            _encrypt_stream(src, dst, file_key, header_raw)
            dst.flush()
            os.fsync(dst.fileno())
        _swap_in(tmp, path, times)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def _opened_chunks(path: Path, account: str | None) -> tuple[str, Iterator[bytes], object]:
    fh = open(path, "rb")
    try:
        header, raw = _read_header(fh)
        owner = str(header.get("account") or "")
        if account is not None and owner != account:
            raise WrongAccount(owner)
        from backend.uefn_plugins.scopes import account_id

        if owner != account_id():
            raise WrongAccount(owner)  # its key opens only while that account is signed in
        return owner, _decrypt_stream(fh, _unwrap(header, owner), raw), fh
    except BaseException:
        fh.close()
        raise


def open_to(path: Path, dest: Path, account: str | None = None) -> None:
    """Write the plaintext of sealed ``path`` to ``dest`` (a new file swapped in whole)."""
    path, dest = Path(path), Path(dest)
    tmp = _tmp_for(dest)
    try:
        _owner, chunks, fh = _opened_chunks(path, account)
        try:
            with open(tmp, "wb") as out:
                for part in chunks:
                    out.write(part)
                out.flush()
                os.fsync(out.fileno())
        finally:
            fh.close()
        _replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def _replace(tmp: Path, dest: Path, wait_s: float = 2.0) -> None:
    """``os.replace``, waiting a moment while another reader still has ``dest`` open (on
    Windows the swap fails then; two openers of one file finish within milliseconds)."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def open_file(path: Path, account: str | None = None) -> bool:
    """Open sealed ``path`` in place (``account``: the one it must be for). False when it
    was not sealed."""
    path = Path(path)
    if not is_sealed_file(path):
        return False
    times = path.stat()
    open_to(path, path, account)
    try:
        os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
    except OSError:
        pass
    return True


def read_bytes(path: Path, account: str | None = None) -> bytes:
    """A file's contents, opened if it is sealed."""
    path = Path(path)
    if not is_sealed_file(path):
        return path.read_bytes()
    _owner, chunks, fh = _opened_chunks(path, account)
    try:
        return b"".join(chunks)
    finally:
        fh.close()


# --------------------------------------------------------------------------- ducky.db backups


def backup_files(root: Path | None = None) -> list[Path]:
    """Copies of ducky.db kept beside it: snapshots, files a restore replaced, corrupt ones."""
    root = root or db.app_root()
    out = list(db.snapshot_dir(root).glob("ducky-*.db")) if db.snapshot_dir(root).is_dir() else []
    out += [p for p in root.glob(db.DB_NAME + ".replaced-*") if p.is_file()]
    out += [p for p in root.glob(db.DB_NAME + "*.corrupt") if p.is_file()]
    return [p for p in out if not p.name.endswith(TMP_SUFFIX)]


def _made(st: os.stat_result) -> float:
    """When a file was made (a snapshot's copy starts then; sealing it keeps its mtime)."""
    return float(getattr(st, "st_birthtime", 0) or (st.st_ctime if os.name == "nt" else st.st_mtime))


SETTLE_S = 60.0  # a backup this fresh may still be being written


def backups_waiting(*, made_before: float) -> bool:
    """A backup from before ``made_before``, still plain, skipped only for being fresh."""
    now, settle_s = time.time(), SETTLE_S
    for path in backup_files():
        try:
            st = path.stat()
            if _made(st) < made_before and now - st.st_mtime < settle_s and not is_sealed_file(path):
                return True
        except OSError:
            pass
    return False


def seal_backups(account: str, *, made_before: float, settle_s: float | None = None) -> int:
    """Seal for ``account`` every backup of ducky.db started before ``made_before`` (they
    hold chats from before they were sealed); a snapshot keeps only ``account``'s plugin
    search records. One still being written is left for the next run."""
    pub = public_key(account)
    if pub is None:
        return 0
    sealed = 0
    now = time.time()
    settle_s = SETTLE_S if settle_s is None else settle_s
    snapshots = db.snapshot_dir().resolve()
    for path in backup_files():
        try:
            st = path.stat()
            if _made(st) >= made_before or now - st.st_mtime < settle_s or is_sealed_file(path):
                continue
            if path.parent.resolve() == snapshots:
                db.strip_foreign_records(path, account)
            if seal_file(path, account, pub):
                sealed += 1
        except Exception:  # noqa: BLE001 — in use or unreadable: the next pass tries again
            _log.warning("could not seal the ducky.db backup %s yet", path.name, exc_info=True)
    return sealed


# --------------------------------------------------------------------------- leftovers

LEFTOVER_AGE_S = 600.0


def sweep_leftovers(*, older_than_s: float = LEFTOVER_AGE_S) -> int:
    """Plain copies an interrupted open left behind (a crash, or the app closed mid-open):
    ``*.udf-tmp`` beside ducky.db, its snapshots and chat attachments, and integrity-check
    copies of sealed backups. One still being written is newer than ``older_than_s``."""
    root = db.app_root()
    places = [p for p in root.glob("*" + TMP_SUFFIX)]
    snaps = db.snapshot_dir(root)
    if snaps.is_dir():
        places += list(snaps.glob("*" + TMP_SUFFIX)) + list(snaps.glob(".*.check.db*"))
    for _conv, folder in _chat_folders():
        places += [p for p in folder.rglob("*" + TMP_SUFFIX) if p.is_file()]
    removed = 0
    now = time.time()
    for path in places:
        try:
            if now - path.stat().st_mtime >= older_than_s:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


# --------------------------------------------------------------------------- chat attachments


def _chat_folders() -> Iterator[tuple[str, Path]]:
    projects = db.app_root() / "chats" / "projects"
    if not projects.is_dir():
        return
    for project in projects.iterdir():
        convs = project / "conversations"
        if not convs.is_dir():
            continue
        for conv in convs.iterdir():
            att = conv / "attachments"
            if att.is_dir():
                yield conv.name, att


def _files(folder: Path) -> Iterable[Path]:
    return [p for p in folder.rglob("*") if p.is_file() and not p.name.endswith(TMP_SUFFIX)]


def open_chat_folder(folder: Path, account: str) -> int:
    """Open every attachment sealed for ``account`` in one chat's attachments ``folder``
    now (first read after a sign-in, before the background pass got to that chat)."""
    if not folder.is_dir():
        return 0
    n = 0
    for path in _files(folder):
        try:
            if sealed_for(path) == account and open_file(path, account):
                n += 1
        except (OSError, ValueError, WrongAccount):
            _log.warning("could not open a sealed attachment in %s", folder.parent.name, exc_info=True)
    return n


def open_local_folder(folder: Path) -> int:
    """Open every attachment in one chat's ``folder`` that is sealed for this PC's
    signed-out owner (``_local``), whoever is signed in: that key is this Windows user's,
    no account's. For a chat made signed out that now belongs to the signed-in account
    (the caller checked that it does)."""
    if not folder.is_dir():
        return 0
    n = 0
    for path in _files(folder):
        try:
            if sealed_for(path) != _LOCAL:
                continue
            times = path.stat()
            tmp = _tmp_for(path)
            try:
                with open(path, "rb") as src, open(tmp, "wb") as out:
                    header, raw = _read_header(src)
                    for part in _decrypt_stream(src, _unwrap(header, _LOCAL), raw):
                        out.write(part)
                    out.flush()
                    os.fsync(out.fileno())
                _swap_in(tmp, path, times)
            finally:
                tmp.unlink(missing_ok=True)
            n += 1
        except Exception:  # noqa: BLE001 — that file stays sealed; the next read tries again
            _log.warning("could not open an attachment made signed out in %s", folder.parent.name, exc_info=True)
    return n


_arranging = threading.Lock()


def _set_arranged(conn, account: str | None) -> None:
    with db.write_txn(conn):
        if account is None:
            conn.execute("DELETE FROM meta WHERE key=?", (_ARRANGED_META,))
        else:
            conn.execute(
                "INSERT INTO meta(key, value, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                (_ARRANGED_META, account, time.time()),
            )


def arrange_chat_files(account: str) -> dict[str, int]:
    """Chat attachments on disk follow who is signed in: ``account``'s are opened (it is
    signed in, its data key is here), every other account's chats' are sealed for their
    owner. Done once per sign-in, one pass at a time; a pass stops as soon as someone else
    signs in (that sign-in's own pass takes over), and a file in use is tried again by the
    next pass, since only a whole pass marks the account done."""
    from backend.store import account_rows
    from backend.uefn_plugins import data_crypto

    if account_rows.current() != account:
        return {}
    with _arranging:
        conn = db.connect()
        if _meta(conn, _ARRANGED_META) == account:
            return {}
        _set_arranged(conn, None)  # a pass cut short must never look done for anyone
        owners = {str(r[0]): str(r[1]) for r in conn.execute("SELECT id, account_id FROM conversations")}
        pubs: dict[str, bytes | None] = {}
        out = {"attachments_opened": 0, "attachments_sealed": 0, "attachments_left": 0}
        for conv_id, folder in _chat_folders():
            owner = owners.get(conv_id)
            if owner is None:
                continue  # not a saved chat (or one from before accounts that nobody claimed yet)
            if account_rows.current() != account:
                out["attachments_left"] += 1
                break
            if owner != account and owner not in pubs:
                pubs[owner] = public_key(owner)
            for path in _files(folder):
                try:
                    if owner == account:
                        if sealed_for(path) == account and open_file(path, account):
                            out["attachments_opened"] += 1
                    elif pubs[owner] is None:
                        out["attachments_left"] += 1  # no file keys for its owner yet: sealed once they have some
                    elif seal_file(path, owner, pubs[owner]):
                        out["attachments_sealed"] += 1
                except data_crypto.Locked:
                    out["attachments_left"] += 1
                except (OSError, ValueError, WrongAccount):
                    out["attachments_left"] += 1
                    _log.warning("could not arrange a chat attachment of %s yet", conv_id, exc_info=True)
        if not out["attachments_left"] and account_rows.current() == account:
            _set_arranged(conn, account)
        return {k: v for k, v in out.items() if v}


def reset_for_tests() -> None:
    forget()
