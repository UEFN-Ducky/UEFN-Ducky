"""Move a Ducky from one PC to another (Windows to Linux): one passphrase-locked zip.

``ducky-move-<date>.zip`` holds ``manifest.json`` (format, app version, OS, KDF salt,
nonce, counts; readable without the passphrase) and ``payload.bin``: chats (with their
folders), settings, plans, workflows and API keys as JSON, zlib-compressed and sealed
with AES-GCM under a scrypt key from the passphrase. The manifest bytes are the
associated data, so an edited manifest fails like a wrong passphrase.

Rows are read and written through the repos, so this PC's sealing (DPAPI on Windows,
the Linux seal, the account data key) is opened on export and applied again on import:
nothing in the archive depends on the PC that made it. Import never overwrites: a chat,
plan or key this PC already has is skipped and counted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any

FORMAT = 1
_MANIFEST = "manifest.json"
_PAYLOAD = "payload.bin"
# scrypt cost: ~0.1 s and 32 MB per try, so a guessed passphrase costs the same.
_N, _R, _P = 2**15, 8, 1
MIN_PASSPHRASE = 8


class MoveError(ValueError):
    """The archive can't be used: wrong passphrase, damaged, or from a newer Ducky."""


def _key(passphrase: str, salt: bytes, n: int = _N, r: int = _R, p: int = _P) -> bytes:
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=32)


def _os_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    return "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else sys.platform


def default_name() -> str:
    return f"ducky-move-{time.strftime('%Y-%m-%d')}.zip"


# --------------------------------------------------------------------------- collect


def _collect() -> dict[str, Any]:
    from backend.agent import secrets
    from backend.automations import bundles
    from backend.store import db
    from backend.store.repos import chats, settings

    plans = [
        {"project_id": str(p), "key": str(k), "kind": str(kind), "doc": json.loads(doc)}
        for p, k, kind, doc in db.connect().execute("SELECT project_id, key, kind, doc FROM plans ORDER BY project_id, key")
    ]
    return {
        # ponytail: whole history in memory; stream per chat if exports pass a few hundred MB.
        "chats": [{"project_id": p, "doc": doc} for p, doc in chats.conv_list_all_projects(with_messages=True)],
        "folders": [{"project_id": p, "folders": f} for p, f in chats.folders_list_all_projects()],
        "settings": settings.load_fields(),
        "plans": plans,
        "workflows": bundles.export_folder("", ""),
        "keys": secrets.load_keys(use_cache=False),
    }


def _counts(data: dict[str, Any]) -> dict[str, int]:
    return {
        "chats": len(data["chats"]),
        "settings": len(data["settings"]),
        "plans": len(data["plans"]),
        "workflows": len(data["workflows"].get("workflows") or []),
        "keys": len(data["keys"]),
    }


# --------------------------------------------------------------------------- archive


def export_archive(dest: str | Path, passphrase: str) -> dict[str, Any]:
    """Write the archive to ``dest`` (a folder gets :func:`default_name`). Returns
    ``{path, counts}``."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from frontend import __version__

    if len(passphrase or "") < MIN_PASSPHRASE:
        raise MoveError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    data = _collect()
    path = Path(dest)
    if path.is_dir():
        path = path / default_name()
    salt, nonce = os.urandom(16), os.urandom(12)
    manifest = {
        "format": FORMAT,
        "app_version": __version__,
        "os": _os_name(),
        "created": time.time(),
        "kdf": {"name": "scrypt", "n": _N, "r": _R, "p": _P, "salt": base64.b64encode(salt).decode("ascii")},
        "cipher": {"name": "AES-256-GCM", "nonce": base64.b64encode(nonce).decode("ascii")},
        "counts": _counts(data),
    }
    head = json.dumps(manifest, indent=1).encode("utf-8")
    body = zlib.compress(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"), 6)
    sealed = AESGCM(_key(passphrase, salt)).encrypt(nonce, body, head)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(_MANIFEST, head)
        zf.writestr(_PAYLOAD, sealed)
    os.replace(tmp, path)
    return {"path": str(path), "counts": manifest["counts"]}


def read_manifest(src: str | Path) -> dict[str, Any]:
    """The archive's manifest (no passphrase needed); :class:`MoveError` when it isn't one."""
    try:
        with zipfile.ZipFile(src) as zf:
            manifest = json.loads(zf.read(_MANIFEST))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise MoveError("This file isn't a Ducky move archive.") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("format"), int):
        raise MoveError("This file isn't a Ducky move archive.")
    if manifest["format"] > FORMAT:
        raise MoveError(f"This archive is format {manifest['format']}; update Ducky to import it.")
    return manifest


def open_archive(src: str | Path, passphrase: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(manifest, data)``; :class:`MoveError` on a wrong passphrase or a damaged file."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    manifest = read_manifest(src)
    try:
        with zipfile.ZipFile(src) as zf:
            head, sealed = zf.read(_MANIFEST), zf.read(_PAYLOAD)
        kdf, cipher = manifest["kdf"], manifest["cipher"]
        if kdf.get("name") != "scrypt":
            raise MoveError("Unknown key format in this archive.")
        salt, nonce = base64.b64decode(kdf["salt"]), base64.b64decode(cipher["nonce"])
        key = _key(passphrase or "", salt, int(kdf["n"]), int(kdf["r"]), int(kdf["p"]))
    except (KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        if isinstance(exc, MoveError):
            raise
        raise MoveError("This archive is damaged.") from exc
    try:
        body = AESGCM(key).decrypt(nonce, sealed, head)
    except InvalidTag:
        raise MoveError("Wrong passphrase, or the archive was changed.") from None
    return manifest, json.loads(zlib.decompress(body))


# --------------------------------------------------------------------------- restore


def import_archive(src: str | Path, passphrase: str) -> dict[str, Any]:
    """Restore the archive into this PC's store. Returns ``{imported: {...}, skipped:
    {...}, skipped_chats: [ids], source_os, summary}``. Existing rows are never overwritten."""
    from backend.agent import secrets
    from backend.automations import bundles
    from backend.store.repos import chats, plans, settings

    manifest, data = open_archive(src, passphrase)
    imported = {"chats": 0, "settings": 0, "plans": 0, "workflows": 0, "keys": 0}
    skipped = {"chats": 0, "plans": 0, "keys": 0}
    skipped_chats: list[str] = []

    for entry in data.get("folders") or []:
        pid = str(entry["project_id"])
        have = chats.folders_get(pid)
        known = {str(f.get("id")) for f in have}
        new = [f for f in entry.get("folders") or [] if str(f.get("id")) not in known]
        if new:
            chats.folders_replace(pid, have + new)

    for entry in data.get("chats") or []:
        doc = dict(entry["doc"])
        cid = str(doc.get("id") or "")
        if not cid or chats.conv_account(cid) is not None:
            skipped["chats"] += 1
            skipped_chats.append(cid)
            continue
        messages = doc.pop("messages", None) or []
        chats.conv_save(str(entry["project_id"]), doc, messages=messages)
        imported["chats"] += 1

    values = data.get("settings") or {}
    imported["settings"] = len(settings.save_fields(values, {})) if values else 0

    for row in data.get("plans") or []:
        if plans.plan_get(row["project_id"], row["key"]) is not None:
            skipped["plans"] += 1
            continue
        plans.plan_put(row["project_id"], row["key"], row["kind"], row["doc"])
        imported["plans"] += 1

    bundle = data.get("workflows") or {}
    if bundle.get("workflows"):
        made = bundles.import_bundle(bundle, "", name=f"From {str(manifest.get('os') or 'old PC').title()}")
        imported["workflows"] = len(made["workflows"])

    keys = {str(k): str(v) for k, v in (data.get("keys") or {}).items() if v}
    have_keys = secrets.load_keys(use_cache=False)
    new_keys = {k: v for k, v in keys.items() if k not in have_keys}
    skipped["keys"] = len(keys) - len(new_keys)
    if new_keys:
        secrets.save_keys({**have_keys, **new_keys})  # sealed for this PC (DPAPI / Linux seal)
    imported["keys"] = len(new_keys)

    out = {"imported": imported, "skipped": skipped, "skipped_chats": skipped_chats,
           "source_os": manifest.get("os", "")}
    out["summary"] = summary(out)
    return out


def summary(result: dict[str, Any]) -> str:
    """'300 chats, 12 keys imported, 0 skipped'."""
    i, s = result["imported"], result["skipped"]
    return f"{i['chats']} chats, {i['keys']} keys imported, {sum(s.values())} skipped"
