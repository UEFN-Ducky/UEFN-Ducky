"""Get a new version ready in the background: only the files that changed are downloaded.

The Store's update feed names a pack (the whole app folder as one zip) and a signed
manifest (each file's hash and byte range in the pack). A file this PC already has with
the same hash is copied from a version on disk; every other file is read from the pack
with an HTTP range request, inflated and checked. The new folder only becomes a version
(``versions/app-<v>`` with ``.ready``) once every file, the manifest's signature and the
EXEs' Authenticode signature check out. ``frontend.app_versions`` starts it from then on.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from frontend import __version__
from frontend.app_versions import (
    PARTIAL_SUFFIX,
    READY_MARKER,
    is_ready,
    parse_version,
    ready_versions,
    stable_exe,
    version_dir,
    versions_dir,
)

APP_UPDATE_SLUG = "uefn-ducky-app"
MANIFEST_ENTRY = "manifest.json"
SIGNER = "Mindful Path Company"
# Ranges closer than this are read as one request; one request reads at most MAX_SPAN.
MERGE_GAP = 256 * 1024
MAX_SPAN = 32 * 1024 * 1024

Progress = Callable[[int, int], None]


class StageError(RuntimeError):
    """Staging stopped; the message says why in plain words."""


def is_loopback(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")


def feed_update(payload: dict[str, Any] | None, current: str = __version__) -> dict[str, Any] | None:
    """The feed's update pack when it is for a version newer than ``current``."""
    update = payload.get("update") if isinstance(payload, dict) else None
    if not isinstance(update, dict):
        return None
    version = str(update.get("version") or "").strip()
    remote, mine = parse_version(version), parse_version(current)
    if remote is None or mine is None or remote <= mine:
        return None
    if not all(str(update.get(k) or "").strip() for k in ("packUrl", "manifestUrl", "manifestSha256")):
        return None
    return update


def signed_record(update: dict[str, Any]) -> dict[str, Any]:
    """The record the Store signed (the version-signature format, see uefn_plugins/signing.py)."""
    return {
        "slug": APP_UPDATE_SLUG,
        "version": str(update.get("version") or ""),
        "sha256": str(update.get("manifestSha256") or "").lower(),
        "visibility": "public",
        "team_id": "",
        "compiled": False,
        "issued_at": int(update.get("issuedAt") or 0),
    }


def signature_ok(update: dict[str, Any], *, base_url: str) -> bool:
    """The Store's signature over the manifest checks out. A loopback feed (a local
    rehearsal) may be unsigned; nothing else may."""
    signature = str(update.get("signature") or "").strip()
    if not signature:
        return is_loopback(base_url)
    from backend.uefn_plugins.signing import verify_signature

    return verify_signature(signed_record(update), signature)


def _request(url: str, *, start: int | None = None, end: int | None = None, timeout: float = 60.0):
    headers = {"User-Agent": f"UEFN-Ducky/{__version__}"}
    if start is not None and end is not None:
        headers["Range"] = f"bytes={start}-{end - 1}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def fetch(url: str) -> bytes:
    """The whole of ``url``; a dropped connection continues where it stopped (Range)."""
    from frontend.update_network import backoff, download_resumable

    buf = io.BytesIO()
    download_resumable(url, buf, headers={"User-Agent": f"UEFN-Ducky/{__version__}"}, timeout=60.0, delay=backoff)
    return buf.getvalue()


def _content_range_start(resp: Any) -> int | None:
    text = str(resp.headers.get("Content-Range") or "")
    try:
        return int(text.split(" ", 1)[1].split("-", 1)[0]) if text.startswith("bytes ") else None
    except (IndexError, ValueError):
        return None


def fetch_range(url: str, start: int, end: int) -> bytes | None:
    """Bytes ``[start, end)`` of ``url``, or ``None`` when the server ignored the range.

    A span is up to 32 MB. A connection that drops part way (TLS EOF, reset, short body)
    asks for the rest only, and every try that got new bytes starts the count again; it
    gives up after ATTEMPTS tries in a row with nothing new, waiting longer each time.
    """
    from frontend.update_network import ATTEMPTS, MAX_TRIES, backoff, retryable

    want = end - start
    buf = bytearray()
    misses = 0
    for _ in range(MAX_TRIES):
        before = len(buf)
        try:
            with _request(url, start=start + before, end=end) as resp:
                if getattr(resp, "status", 200) != 206:
                    return None
                got = _content_range_start(resp)
                if got is not None and got != start + before:
                    raise StageError("the update server sent the wrong part of the update")
                while len(buf) < want:
                    chunk = resp.read(min(256 * 1024, want - len(buf)))
                    if not chunk:
                        break
                    buf += chunk
            if len(buf) == want:
                return bytes(buf)
            raise http.client.IncompleteRead(bytes(), want - len(buf))
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if not retryable(exc):
                raise
            misses = 0 if len(buf) > before else misses + 1
            if misses >= ATTEMPTS:
                raise
            time.sleep(backoff(misses - 1 if misses else 0))
    raise StageError("the update kept stopping part way")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(path) and not pure.is_absolute() and ".." not in pure.parts and ":" not in path and "\\" not in path


def read_manifest(raw: bytes, *, version: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            manifest = json.loads(bundle.read(MANIFEST_ENTRY))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise StageError(f"the update's file list is unreadable ({exc})") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if str(manifest.get("version") or "") != version or not isinstance(files, list) or not files:
        raise StageError("the update's file list is for another version")
    for entry in files:
        if not isinstance(entry, dict) or not safe_relative(str(entry.get("path") or "")):
            raise StageError("the update's file list names a file outside the app folder")
    if not any(entry["path"] == "UEFN-Ducky.exe" for entry in files):
        raise StageError("the update has no UEFN-Ducky.exe")
    return manifest


def local_sources() -> list[Path]:
    """App folders on this PC to copy unchanged files from: the running one first."""
    seen: list[Path] = []
    for folder in (Path(sys.executable).parent, stable_exe().parent, *(f for _, f in ready_versions())):
        try:
            resolved = folder.resolve()
        except OSError:
            continue
        if resolved not in seen and (resolved / "UEFN-Ducky.exe").is_file():
            seen.append(resolved)
    return seen


def _same_file(path: Path, entry: dict[str, Any]) -> bool:
    try:
        return path.is_file() and path.stat().st_size == int(entry["size"]) and _sha256_file(path) == entry["sha256"]
    except OSError:
        return False


def _place_local(entry: dict[str, Any], dest: Path, sources: list[Path]) -> bool:
    """Copy (hard-link between downloaded versions) a file this PC already has."""
    root = versions_dir().resolve()
    for folder in sources:
        src = folder / entry["path"]
        if not _same_file(src, entry):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Downloaded versions are never written in place, so they can share files.
            # The installed folder can be (Setup rewrites it), so it is copied.
            if folder.is_relative_to(root):
                os.link(src, dest)
                return True
        except OSError:
            pass
        try:
            shutil.copy2(src, dest)
            return True
        except OSError:
            continue
    return False


def _inflate(entry: dict[str, Any], raw: bytes) -> bytes:
    method = int(entry.get("method") or 0)
    if method == zipfile.ZIP_STORED:
        body = raw
    elif method == zipfile.ZIP_DEFLATED:
        body = zlib.decompressobj(-15).decompress(raw)
    else:
        raise StageError(f"{entry['path']}: unknown packing {method}")
    if len(body) != int(entry["size"]) or hashlib.sha256(body).hexdigest() != entry["sha256"]:
        raise StageError(f"{entry['path']} did not match its hash")
    return body


def _write(dest: Path, body: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(body)
    os.replace(tmp, dest)


def spans(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group pack entries into few range requests (close neighbours read together)."""
    groups: list[list[dict[str, Any]]] = []
    for entry in sorted(entries, key=lambda e: int(e["offset"])):
        if groups:
            last = groups[-1]
            first_start = int(last[0]["offset"])
            last_end = int(last[-1]["offset"]) + int(last[-1]["csize"])
            gap = int(entry["offset"]) - last_end
            if gap <= MERGE_GAP and int(entry["offset"]) + int(entry["csize"]) - first_start <= MAX_SPAN:
                last.append(entry)
                continue
        groups.append([entry])
    return groups


def authenticode_ok(exe: Path) -> bool:
    """The EXE carries a valid signature from the publisher."""
    if sys.platform != "win32":
        return True
    ps = (
        f"$s = Get-AuthenticodeSignature -LiteralPath '{str(exe).replace(chr(39), chr(39) * 2)}'; "
        "if ($s.Status -eq 'Valid') { $s.SignerCertificate.Subject } else { 'INVALID ' + $s.Status }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return SIGNER.lower() in (out or "").lower() and "INVALID" not in out


def stage(update: dict[str, Any], *, base_url: str, progress: Progress | None = None) -> Path:
    """Make ``versions/app-<v>`` ready from the feed's ``update``; returns the folder."""
    version = str(update["version"])
    final = version_dir(version)
    if is_ready(final):
        return final
    if not signature_ok(update, base_url=base_url):
        raise StageError("the update's signature did not check out")
    manifest_url = urljoin(base_url.rstrip("/") + "/", str(update["manifestUrl"]).lstrip("/"))
    pack_url = urljoin(base_url.rstrip("/") + "/", str(update["packUrl"]).lstrip("/"))
    raw = fetch(manifest_url)
    if hashlib.sha256(raw).hexdigest() != str(update["manifestSha256"]).lower():
        raise StageError("the update's file list did not match its signature")
    manifest = read_manifest(raw, version=version)

    partial = final.with_name(final.name + PARTIAL_SUFFIX)
    partial.mkdir(parents=True, exist_ok=True)
    sources = local_sources()
    todo: list[dict[str, Any]] = []
    for entry in manifest["files"]:
        dest = partial / entry["path"]
        if _same_file(dest, entry) or _place_local(entry, dest, sources):
            continue
        todo.append(entry)

    total = sum(int(e["csize"]) for e in todo)
    done = 0
    if progress:
        progress(done, total)
    whole: bytes | None = None
    for group in spans(todo):
        start = int(group[0]["offset"])
        end = int(group[-1]["offset"]) + int(group[-1]["csize"])
        data = None if whole is not None else fetch_range(pack_url, start, end)
        if data is None:
            # The server answered without ranges: read the pack once and take files from it.
            if whole is None:
                whole = fetch(pack_url)
            data = whole[start:end]
        for entry in group:
            at = int(entry["offset"]) - start
            _write(partial / entry["path"], _inflate(entry, data[at : at + int(entry["csize"])]))
        done += end - start
        if progress:
            progress(min(done, total), total)

    wanted = {entry["path"] for entry in manifest["files"]}
    for leftover in [p for p in partial.rglob("*") if p.is_file()]:
        rel = leftover.relative_to(partial).as_posix()
        if rel not in wanted and rel != READY_MARKER:
            leftover.unlink(missing_ok=True)
    if not is_loopback(base_url):
        for exe in sorted(partial.glob("*.exe")):
            if not authenticode_ok(exe):
                raise StageError(f"{exe.name} is not signed by {SIGNER}")
    (partial / READY_MARKER).write_text(
        json.dumps({"version": version, "files": len(wanted), "downloaded": total, "at": time.time()}),
        encoding="utf-8",
    )
    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
    os.replace(partial, final)
    return final


def prune(running: str = __version__) -> list[str]:
    """Drop downloaded versions nobody needs: keep the running one, the newest ready one
    and the newest one older than the running one (the fallback). A folder still in use
    (a bridge of that version running) can't be renamed and is left for later."""
    root = versions_dir()
    keep: set[str] = {running}
    ready = ready_versions()
    mine = parse_version(running) or (0, 0, 0)
    if ready and (parse_version(ready[0][0]) or (0, 0, 0)) > mine:
        keep.add(ready[0][0])
    older = [v for v, _ in ready if (parse_version(v) or (0, 0, 0)) < mine]
    if older:
        keep.add(older[0])
    removed: list[str] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return removed
    for entry in entries:
        if not entry.is_dir() or not entry.name.startswith("app-"):
            continue
        name = entry.name[len("app-") :]
        if name.endswith(PARTIAL_SUFFIX):
            # A partial download older than the running version is never finished.
            base = name[: -len(PARTIAL_SUFFIX)]
            if (parse_version(base) or (0, 0, 0)) > mine:
                continue
        elif name in keep:
            continue
        doomed = entry.with_name(f"{entry.name}.remove-{os.getpid()}")
        try:
            os.replace(entry, doomed)
        except OSError:
            continue
        shutil.rmtree(doomed, ignore_errors=True)
        removed.append(entry.name)
    return removed
