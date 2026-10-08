"""Store version signatures and compiled-plugin licensing (app side).

The UEFN Ducky Store signs each published version once with an Ed25519 key held as a
core plugin secret on the server. The app pins that key's PUBLIC half here and verifies
the signed record before installing any Store zip, and again — from inside the compiled
binary — every time a compiled plugin loads, so a tampered zip is refused and a compiled
copy cannot be sideloaded as a local plugin.

Signed fields (the server builds the identical message): slug, version, sha256 of the
zip, visibility (public|team), team_id, compiled, issued_at. The canonical message is a
newline-joined ``key=value`` list in sorted-key order — language-agnostic, no JSON-encoding
ambiguity — so the Rust signer and this verifier always agree.

Public key setup: core's plugin SDK has no capability that returns a secret's Ed25519
public key, and we do not change core. The owner runs the Store's one-time
``version-signing-pubkey`` setup event, which derives the key from the signing seed, and
pastes the base64 value into :data:`STORE_SIGNING_PUBKEY` below. Until it is set, signing
is treated as not-yet-configured: installs are allowed (with a logged note) and compiled
builds bake an empty key that skips the load-time check, so the pipeline works during
rollout and enforces once the key is pinned.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger("uefn_plugins.signing")

# Base64 of the 32-byte Ed25519 public key for Store version signatures.
# PASTE the value from the Store's `version-signing-pubkey` setup event here.
STORE_SIGNING_PUBKEY = ""

# The record file written next to an installed plugin (plugin root).
SIG_FILENAME = ".ducky-sig.json"

# Signed fields, in the order the canonical message sorts them.
SIGNED_FIELDS = ("compiled", "issued_at", "sha256", "slug", "team_id", "version", "visibility")


class LicenseError(RuntimeError):
    """A compiled plugin's signed record is missing, tampered, or not licensed here."""


def _norm_value(key: str, record: dict[str, Any]) -> str:
    raw = record.get(key)
    if key == "compiled":
        return "true" if bool(raw) else "false"
    if key == "issued_at":
        try:
            return str(int(raw))
        except (TypeError, ValueError):
            return "0"
    if key == "sha256":
        return str(raw or "").strip().lower()
    return str(raw if raw is not None else "")


def canonical_message(record: dict[str, Any]) -> bytes:
    """Deterministic bytes signed/verified for a version record (matches the server)."""
    return "\n".join(f"{k}={_norm_value(k, record)}" for k in SIGNED_FIELDS).encode("utf-8")


def verify_signature(record: dict[str, Any], signature_b64: str, pubkey_b64: str = "") -> bool:
    """True when ``signature_b64`` is a valid Ed25519 signature over the record.

    Returns False on any decode/verify error. An empty pinned key means
    "not configured" — callers decide whether to allow unverified installs.
    """
    key_b64 = (pubkey_b64 or STORE_SIGNING_PUBKEY or "").strip()
    sig = (signature_b64 or "").strip()
    if not key_b64 or not sig:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64))
        try:
            pub.verify(base64.b64decode(sig), canonical_message(record))
            return True
        except InvalidSignature:
            return False
    except Exception:  # noqa: BLE001 — a malformed key/sig never raises to the caller
        _log.debug("signature verify failed", exc_info=True)
        return False


def signing_configured() -> bool:
    return bool(STORE_SIGNING_PUBKEY.strip())


def record_from_download(meta: Any) -> dict[str, Any] | None:
    """Pull the signed record ({…fields, signature}) out of a download response."""
    if not isinstance(meta, dict):
        return None
    rec = meta.get("signature") if isinstance(meta.get("signature"), dict) else None
    if rec is None and meta.get("sig"):
        rec = meta
    if not isinstance(rec, dict) or not rec.get("signature"):
        return None
    return rec


def write_installed_record(plugin_root: Path, record: dict[str, Any]) -> None:
    """Store the signed record next to the installed plugin. Best-effort."""
    try:
        keep = {k: record.get(k) for k in SIGNED_FIELDS}
        keep["signature"] = record.get("signature")
        (Path(plugin_root) / SIG_FILENAME).write_text(
            json.dumps(keep, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        _log.debug("could not write signed record", exc_info=True)


def read_installed_record(plugin_root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(plugin_root) / SIG_FILENAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def verify_download(record: dict[str, Any] | None, zip_sha256: str) -> tuple[bool, str]:
    """Verify a Store download before install. Returns (ok, reason).

    No pinned key yet → allowed with a note (rollout). A present record with a bad
    signature or a sha256 that does not match the zip is refused.
    """
    if not signing_configured():
        return True, "signing not configured (unverified install allowed)"
    if not record or not record.get("signature"):
        # Signing is live but this item is unsigned (e.g. legacy plain-source): allow.
        return True, "unsigned item"
    want = str(record.get("sha256") or "").strip().lower()
    if want and zip_sha256 and want != zip_sha256.strip().lower():
        return False, "This download does not match its signature (checksum mismatch)."
    if not verify_signature(record, str(record.get("signature") or "")):
        return False, "This download's signature is invalid — refusing to install."
    return True, "ok"


def _team_access(team_id: str) -> tuple[bool, str]:
    """(ok, label) for a team the compiled plugin was published to."""
    from backend.uefn_plugins.scopes import account_id, team_scope

    try:
        scope = team_scope(account_id(), team_id)
    except Exception:
        return False, team_id
    label = str(scope.get("label") or team_id)
    # Has the key and not lost: ok to load (paused is read-only data, access intact).
    return scope.get("state") in ("ok", "paused"), label


def verify_installed_record(
    plugin_id: str,
    version: str,
    visibility: str,
    team_id: str,
    pubkey_b64: str,
) -> None:
    """License gate run from inside a compiled plugin at load. Raises LicenseError.

    ``pubkey_b64`` is baked into the compiled binary; an empty value means the plugin
    was built before signing was configured, so the gate is skipped.
    """
    import os

    if os.environ.get("DUCKY_SKIP_LICENSE_GATE") == "1":
        return  # the build's smoke-import only checks that the .pyd loads
    if not (pubkey_b64 or "").strip():
        return  # unsigned dev/rollout build — no enforcement
    from backend.uefn_plugins.store import plugin_dir

    root = plugin_dir(plugin_id)
    record = read_installed_record(root)
    if not record or not record.get("signature"):
        raise LicenseError("This copy isn't licensed for this PC")
    if not verify_signature(record, str(record.get("signature") or ""), pubkey_b64):
        raise LicenseError("This copy isn't licensed for this PC")
    if (
        str(record.get("slug") or "") != plugin_id
        or str(record.get("version") or "") != str(version)
        or not bool(record.get("compiled"))
    ):
        raise LicenseError("This copy isn't licensed for this PC")
    if str(visibility) == "team":
        ok, label = _team_access(str(team_id or record.get("team_id") or ""))
        if not ok:
            raise LicenseError(f"Paused: no access to {label}")
