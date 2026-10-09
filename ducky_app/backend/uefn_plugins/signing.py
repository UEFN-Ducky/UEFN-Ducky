"""Store version signatures and compiled-plugin licensing (app side).

The UEFN Ducky Store signs each published version once with an Ed25519 key it creates
for itself the first time anything needs it. Every app release builds that key's PUBLIC
half in: the release fetches it from the Store's public ``signing-pubkey`` endpoint and
writes :data:`BUILT_IN_KEY_FILE` next to this module, which the frozen app ships. The app
verifies the signed record before installing any Store zip, and again — from inside the
compiled binary — every time a compiled plugin loads, so a tampered zip is refused and a
compiled copy cannot be sideloaded as a local plugin. Nobody runs a setup step or pastes
a key; a release that cannot reach the Store fails instead of shipping without the key.

Signed fields (the server builds the identical message): slug, version, sha256 of the
zip, visibility (public|team), team_id, compiled, issued_at. The canonical message is a
newline-joined ``key=value`` list in sorted-key order — language-agnostic, no JSON-encoding
ambiguity — so the Rust signer and this verifier always agree.

Dev runs (no built-in key) never fetch and never block: Store installs go unverified and
compiled builds made there bake an empty key that skips the load-time check. This module
imports only the standard library at the top, so release scripts can load it by path.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_log = logging.getLogger("uefn_plugins.signing")

# Test override for the Store's public key (base64, 32 bytes). Releases leave it empty
# and build the fetched key in through BUILT_IN_KEY_FILE.
STORE_SIGNING_PUBKEY = ""

# Written by the release from the Store; shipped inside the frozen app; not in git.
BUILT_IN_KEY_FILE = "store_signing_key.json"
DEFAULT_STORE_BASE = "https://uefnducky.org"
SIGNING_PUBKEY_PATH = "/api/v1/plugins/uefn-ducky-store/collect/signing-pubkey"

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

    Returns False on any decode/verify error. No key (a dev run without the built-in
    one) means "not configured" — callers decide whether to allow unverified installs.
    """
    key_b64 = (pubkey_b64 or pinned_public_key() or "").strip()
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


class SigningKeyError(RuntimeError):
    """The Store's signing key could not be fetched for a release (plain message)."""


def valid_public_key(key: str) -> bool:
    """True for a base64 Ed25519 public key (32 bytes)."""
    try:
        return len(base64.b64decode((key or "").strip(), validate=True)) == 32
    except Exception:  # noqa: BLE001
        return False


def built_in_key_path() -> Path:
    """Where the release writes the Store's public key; the frozen app ships it here."""
    here = Path(__file__).with_name(BUILT_IN_KEY_FILE)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and not here.is_file():
        return Path(meipass) / "backend" / "uefn_plugins" / BUILT_IN_KEY_FILE
    return here


def built_in_public_key() -> str:
    """The public key this build carries ('' in dev runs that were never released)."""
    try:
        data = json.loads(built_in_key_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    key = str(data.get("public_key") or "").strip() if isinstance(data, dict) else ""
    return key if valid_public_key(key) else ""


def pinned_public_key() -> str:
    """The key downloads and compiled plugins are verified with: the test override, else
    the built-in one. '' means signing is not configured (a dev run)."""
    return STORE_SIGNING_PUBKEY.strip() or built_in_public_key()


def signing_configured() -> bool:
    return bool(pinned_public_key())


def store_base_url(base_url: str = "") -> str:
    """The Store to read the key from: given, else DUCKYOS_BASE_URL, else uefnducky.org."""
    base = (base_url or os.environ.get("DUCKYOS_BASE_URL") or "").strip().rstrip("/")
    return base or DEFAULT_STORE_BASE


def _app_user_agent() -> str:
    """The app's own User-Agent (``UEFN-Ducky/<version>``). The Store's CDN refuses
    urllib's default one, so every request here sends this."""
    try:
        from frontend import __version__

        return f"UEFN-Ducky/{__version__}"
    except Exception:  # noqa: BLE001 — loaded by path from a release script
        init_py = Path(__file__).resolve().parents[2] / "frontend" / "__init__.py"
        try:
            m = re.search(r'(?m)^__version__\s*=\s*["\']([^"\']+)["\']', init_py.read_text(encoding="utf-8"))
        except OSError:
            m = None
        return f"UEFN-Ducky/{m.group(1) if m else '0'}"


def fetch_store_public_key(base_url: str = "", *, user_agent: str = "", timeout: float = 20.0) -> str:
    """Read the Store's version-signing public key from its public endpoint.

    Raises :class:`SigningKeyError` with a plain message when the Store can't be
    reached or returns no key — release builds stop on it.
    """
    base = store_base_url(base_url)
    req = urllib.request.Request(
        base + SIGNING_PUBKEY_PATH,
        data=b"{}",
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            # Collect endpoints require Origin to match the site (browser CSRF rule).
            "Origin": base,
            "User-Agent": user_agent or _app_user_agent(),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            envelope = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SigningKeyError(
            f"The Store at {base} refused the signing-key request (HTTP {exc.code}). "
            "Release builds take the key from the Store, so the release stops here."
        ) from exc
    except (OSError, ValueError) as exc:
        raise SigningKeyError(
            f"Couldn't reach the Store at {base} to get its signing key ({exc}). "
            "Release builds take the key from the Store, so try again when it is reachable."
        ) from exc
    # The host wraps the plugin's {handled, payload}; it may nest more than once.
    node: Any = envelope
    for _ in range(4):
        if isinstance(node, dict) and node.get("public_key"):
            break
        node = node.get("payload") if isinstance(node, dict) else None
    key = str(node.get("public_key") or "").strip() if isinstance(node, dict) else ""
    if not valid_public_key(key):
        raise SigningKeyError(f"The Store at {base} returned no signing key, so the release stops here.")
    return key


def write_built_in_key(public_key: str, *, source: str = "") -> Path:
    """Build the Store's public key into this checkout (the release step before freezing)."""
    import time

    if not valid_public_key(public_key):
        raise SigningKeyError("not a base64 Ed25519 public key")
    path = Path(__file__).with_name(BUILT_IN_KEY_FILE)
    payload = {
        "public_key": public_key.strip(),
        "source": source or DEFAULT_STORE_BASE,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def release_public_key(base_url: str = "") -> str:
    """The key a release plugin build bakes in: the one this build carries, else the
    Store's (fetched now). Raises :class:`SigningKeyError` when neither is available."""
    key = pinned_public_key()
    if key:
        return key
    if not base_url and not os.environ.get("DUCKYOS_BASE_URL"):
        try:
            from frontend.duckyos_account import resolve_base_url

            base_url = resolve_base_url()
        except Exception:  # noqa: BLE001 — release scripts have no account
            base_url = ""
    return fetch_store_public_key(base_url)


def release_key_problem() -> str:
    """Why this build must not be shipped: '' when it carries the Store's public key.

    Releases fetch the key from the Store before freezing the app (release/publish_app.py,
    release/build_all.ps1), so this only fails when that step was skipped.
    """
    if pinned_public_key():
        return ""
    return (
        "This build doesn't carry the Store's signing key, so its Store installs would go "
        "unverified. Release builds fetch the key from the Store automatically before "
        "building the app: build the release with release/publish_app.py or "
        "release/build_all.ps1."
    )


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

    A dev run without the built-in key → allowed with a note. An unsigned (legacy) item
    still installs. A present record with a bad signature or a sha256 that does not
    match the zip is refused.
    """
    if not signing_configured():
        return True, "this build has no Store signing key (dev run): unverified install allowed"
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

    ``pubkey_b64`` is baked into the compiled binary; an empty value means a dev build
    made without the Store's key (release builds always bake it), so the gate is skipped.
    """
    if os.environ.get("DUCKY_SKIP_LICENSE_GATE") == "1":
        return  # the build's smoke-import only checks that the .pyd loads
    if not (pubkey_b64 or "").strip():
        return  # dev build without the Store's key — no enforcement
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
