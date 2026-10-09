"""Download a pinned npm package into a plugin's ``ui/vendor/``.

Plugin panels never load code from the internet (their security policy blocks it), so a
library a plugin uses ships inside the plugin. This fetches an exact version from the npm
registry, checks it against the registry's integrity hash, and copies only the files asked
for (plus the package's license) — the tarball is never unpacked as a whole.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REGISTRY = "https://registry.npmjs.org"
_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_LICENSES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md")
_LISTABLE = (".js", ".mjs", ".cjs", ".css", ".map", ".json", ".woff", ".woff2", ".ttf", ".svg", ".png", ".wasm")
MAX_META_BYTES = 2 * 1024 * 1024
MAX_TARBALL_BYTES = 60 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024


class VendorError(ValueError):
    pass


def _user_agent() -> str:
    try:
        from frontend import __version__

        return f"UEFN-Ducky/{__version__}"
    except Exception:  # noqa: BLE001
        return "UEFN-Ducky"


def _get(url: str, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent(), "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310 — https registry only
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise VendorError("not on npm: check the package name and exact version") from exc
        raise VendorError(f"npm answered HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise VendorError(f"couldn't reach npm: {exc}") from exc
    if len(data) > limit:
        raise VendorError(f"too big (over {limit // (1024 * 1024)} MB)")
    return data


def _check_integrity(data: bytes, dist: dict[str, Any]) -> None:
    integrity = str(dist.get("integrity") or "")
    if integrity.startswith("sha512-"):
        want = base64.b64decode(integrity[len("sha512-"):])
        if hashlib.sha512(data).digest() != want:
            raise VendorError("download doesn't match npm's sha512 integrity — not saved")
        return
    shasum = str(dist.get("shasum") or "")
    if shasum and hashlib.sha1(data).hexdigest() != shasum:  # noqa: S324 — npm's legacy checksum
        raise VendorError("download doesn't match npm's checksum — not saved")
    if not shasum:
        raise VendorError("npm gave no integrity hash for this version — not saved")


def _members(tar: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    """Regular files by their path inside the package (npm's top folder dropped)."""
    out: dict[str, tarfile.TarInfo] = {}
    for member in tar.getmembers():
        if not member.isfile():
            continue
        parts = member.name.replace("\\", "/").split("/", 1)
        if len(parts) == 2 and parts[1] and ".." not in parts[1].split("/"):
            out[parts[1]] = member
    return out


def vendor_npm(plugin_root: Path, package: str, version: str, files: list[str] | None = None) -> dict[str, Any]:
    """Copy ``files`` of ``package@version`` into ``<plugin>/ui/vendor/<package>@<version>/``.

    With no ``files``, lists the package's files so the right build can be picked.
    """
    name = (package or "").strip().lower()
    ver = (version or "").strip().lstrip("v")
    if not _NAME.match(name):
        return {"ok": False, "error": f"not an npm package name: {package!r}"}
    if not _VERSION.match(ver):
        return {"ok": False, "error": "give an exact version like 0.160.0 (no latest, ^ or ~): the plugin must not change later"}
    try:
        meta = json.loads(_get(f"{REGISTRY}/{urllib.parse.quote(name, safe='@')}/{ver}", MAX_META_BYTES))
        dist = meta.get("dist") if isinstance(meta, dict) else None
        tarball = str((dist or {}).get("tarball") or "")
        if not tarball.startswith(REGISTRY + "/"):
            return {"ok": False, "error": "npm gave no download for that version"}
        data = _get(tarball, MAX_TARBALL_BYTES)
        _check_integrity(data, dist)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = _members(tar)
            if not files:
                # Built browser files first (a big package lists thousands of sources).
                def rank(path: str) -> tuple[int, int, str]:
                    low = path.lower()
                    if low.endswith(".map"):
                        tier = 4
                    elif ".min." in low or "/umd/" in low or low.startswith("umd/"):
                        tier = 0
                    elif low.endswith(".css"):
                        tier = 1
                    elif low.startswith(("dist/", "build/")):
                        tier = 2
                    else:
                        tier = 3
                    return (tier, low.count("/"), low)

                listing = sorted((p for p in members if p.lower().endswith(_LISTABLE)), key=rank)
                return {
                    "ok": True,
                    "package": name,
                    "version": ver,
                    "files": listing[:300],
                    "more": max(0, len(listing) - 300),
                    "hint": "Call again with files=[...] naming the built file(s) the panel loads (dist/…min.js, …css).",
                }
            wanted = [str(f or "").replace("\\", "/").strip().lstrip("/") for f in files]
            missing = [w for w in wanted if w not in members]
            if missing:
                return {"ok": False, "error": f"not in {name}@{ver}: {', '.join(missing)} (call with no files to list them)"}
            license_file = next((lic for lic in _LICENSES if lic in members), "")
            copy = list(dict.fromkeys(wanted + ([license_file] if license_file else [])))
            folder = f"{name.lstrip('@').replace('/', '-')}@{ver}"
            dest_root = (plugin_root / "ui" / "vendor" / folder).resolve()
            if not dest_root.is_relative_to(plugin_root.resolve()):
                return {"ok": False, "error": "bad destination"}
            total = 0
            written: list[str] = []
            for rel in copy:
                member = members[rel]
                if member.size > MAX_FILE_BYTES:
                    return {"ok": False, "error": f"{rel} is over {MAX_FILE_BYTES // (1024 * 1024)} MB"}
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    return {"ok": False, "error": "those files add up to too much for one plugin"}
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                target = (dest_root / rel).resolve()
                if not target.is_relative_to(dest_root):
                    return {"ok": False, "error": f"refusing path {rel!r}"}
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(fh.read())
                written.append(target.relative_to(plugin_root.resolve()).as_posix())
    except VendorError as exc:
        return {"ok": False, "error": f"{name}@{ver}: {exc}"}
    except (tarfile.TarError, OSError, ValueError) as exc:
        return {"ok": False, "error": f"{name}@{ver}: couldn't read the package ({exc})"}
    use = []
    for rel in written:
        page_rel = rel[len("ui/"):] if rel.startswith("ui/") else rel
        if rel.endswith((".js", ".mjs")):
            use.append(f'<script src="{page_rel}"></script>')
        elif rel.endswith(".css"):
            use.append(f'<link rel="stylesheet" href="{page_rel}" />')
    return {"ok": True, "package": name, "version": ver, "written": written, "use": use,
            "note": "Paths in `use` are relative to ui/index.html; add one ../ per folder deeper."}
