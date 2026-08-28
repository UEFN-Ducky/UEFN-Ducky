#!/usr/bin/env python3
"""Publish UEFN-Ducky Setup.exe to the UEFN Ducky Store (https://uefnducky.org).

Default flow (always bumps):
  0. Sync __version__ up to the live Store version if Store is ahead
  1. Build release EXE (build_exes.py bumps patch once) + Inno Setup
  2. Direct-to-S3: ticket → PUT Setup.exe → complete → poll job
     (falls back to multipart POST /api/files/app-release if ticket API missing)
  3. MCP uds_app_release on that same site   (latest-only version + url + sha256)

Env:
  DUCKYOS_BASE_URL   the UEFN Ducky Store site (default https://uefnducky.org)
  DUCKYOS_API_KEY    staff key for that site (mcp_remote.plugins + files.write)

If DUCKYOS_API_KEY is unset, the key is read from ~/.cursor/mcp.json
(mcpServers.uefn-duckyos-site Authorization Bearer) or a .env file
(./.env or ~/.duckyos/.env).

Usage:
  py release/publish_app.py
  py release/publish_app.py --notes "Bug fixes"
  py release/publish_app.py --set-version 1.1.0   # minor/major (patch bump cannot cross)
  py release/publish_app.py --require-sign   # refuse unsigned (Chrome/SmartScreen)
  py release/publish_app.py --no-bump --exe dist/UEFN-Ducky-Setup-1.0.450.exe
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import http.client
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "https://uefnducky.org"
INIT_PY = ROOT / "ducky_app" / "frontend" / "__init__.py"
_VERSION_RE = re.compile(r"(?m)^(__version__\s*=\s*[\"'])([^\"']+)([\"'])")

_MCP_JSON = Path.home() / ".cursor" / "mcp.json"
_BEARER_RE = re.compile(r"^\s*[Bb]earer\s+(.+)$")
_ALLOWED_HOSTS = frozenset(
    {
        "uefnducky.org",
        "www.uefnducky.org",
        "uefn-ducky.duckyos.org",  # legacy platform subdomain
        "localhost",
        "127.0.0.1",
    }
)


# ---------------------------------------------------------------------------
# Credentials + host guard
# ---------------------------------------------------------------------------


def _is_uefn_ducky_host(host: str) -> bool:
    if host in _ALLOWED_HOSTS:
        return True
    return host.endswith(".uefnducky.org") or "uefn-ducky" in host


def assert_uefn_ducky_store_base(base: str | None = None) -> str:
    """Refuse apex Marketplace / wrong hosts — releases are the Store site only."""
    base = (base or "").strip().rstrip("/") or DEFAULT_BASE
    host = (urlparse(base).hostname or "").lower()
    if host in ("duckyos.org", "www.duckyos.org"):
        raise SystemExit(
            f"Refusing {base!r} — that is the DuckyOS Marketplace host.\n"
            f"Desktop Setup publishes only to the UEFN Ducky Store ({DEFAULT_BASE})."
        )
    if "marketplace" in host:
        raise SystemExit(f"Refusing marketplace host {base!r} — use {DEFAULT_BASE}")
    if host and not _is_uefn_ducky_host(host):
        raise SystemExit(
            f"Refusing {base!r} — expected an UEFN Ducky site host "
            f"(e.g. uefnducky.org), not Marketplace."
        )
    return base


def _bearer_from_mcp_json() -> str:
    if not _MCP_JSON.is_file():
        return ""
    try:
        data = json.loads(_MCP_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    servers = data.get("mcpServers") or {}
    for name in ("uefn-duckyos-site", "uefn-duckyos", "uefn-ducky"):
        entry = servers.get(name)
        if not isinstance(entry, dict):
            continue
        headers = entry.get("headers") or {}
        auth = str(headers.get("Authorization") or headers.get("authorization") or "")
        m = _BEARER_RE.match(auth)
        if m:
            return m.group(1).strip()
        env = entry.get("env") or {}
        key = str(env.get("DUCKYOS_API_KEY") or "").strip()
        if key:
            return key
    return ""


def _load_dotenv() -> None:
    candidates = [ROOT / ".env", Path.home() / ".duckyos" / ".env"]
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def ensure_store_env() -> tuple[str, str]:
    """Ensure DUCKYOS_BASE_URL + DUCKYOS_API_KEY are set; return (base, key)."""
    if not (os.environ.get("DUCKYOS_API_KEY") or "").strip():
        key = _bearer_from_mcp_json()
        if key:
            os.environ["DUCKYOS_API_KEY"] = key
    base = assert_uefn_ducky_store_base(os.environ.get("DUCKYOS_BASE_URL") or DEFAULT_BASE)
    os.environ["DUCKYOS_BASE_URL"] = base
    key = (os.environ.get("DUCKYOS_API_KEY") or "").strip()
    if not key:
        raise SystemExit(
            "Missing DUCKYOS_API_KEY.\n"
            "Set it in the environment, or add Bearer auth on mcpServers.uefn-duckyos-site "
            f"in {_MCP_JSON}."
        )
    return base, key


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def parse_version_tuple(version: str) -> tuple[int, int, int]:
    base = (version or "").strip().partition("+")[0]
    parts = base.split(".")
    if len(parts) != 3:
        raise ValueError(f"expected MAJOR.MINOR.PATCH, got {version!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def read_version() -> str:
    m = _VERSION_RE.search(INIT_PY.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"Could not parse __version__ from {INIT_PY}")
    return m.group(2)


def write_version(version: str) -> None:
    text = INIT_PY.read_text(encoding="utf-8")
    m = _VERSION_RE.search(text)
    if not m:
        raise SystemExit(f"Could not parse __version__ from {INIT_PY}")
    INIT_PY.write_text(text[: m.start(2)] + version + text[m.end(2) :], encoding="utf-8")


def fetch_store_version(base: str) -> str | None:
    """Public collect/app-version — no API key required."""
    url = base.rstrip("/") + "/api/plugins/uefn-ducky-store/collect/app-version"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": base.rstrip("/"),
            "User-Agent": "UEFN-Ducky-PublishApp/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            envelope = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        print(f"  warn: could not read store version ({exc})")
        return None
    if not isinstance(envelope, dict):
        return None
    # Host wraps plugin {handled,payload}; may nest again — prefer innermost currentVersion.
    payload: object = envelope
    best: dict | None = None
    for _ in range(4):
        if not isinstance(payload, dict):
            break
        if payload.get("currentVersion") or payload.get("version"):
            best = payload
        nested = payload.get("payload")
        if not isinstance(nested, dict):
            break
        payload = nested
    if best is None and isinstance(payload, dict):
        best = payload
    if not best:
        return None
    ver = str(best.get("currentVersion") or best.get("version") or "").strip()
    return ver or None


def sync_local_at_least_store(base: str) -> None:
    """If Store is ahead of local __version__, jump local up to Store before build bump."""
    store = fetch_store_version(base)
    if not store:
        print("  store version: (unknown)")
        return
    local = read_version()
    print(f"  store version: {store}  local: {local}")
    try:
        if parse_version_tuple(store) > parse_version_tuple(local):
            write_version(store)
            print(f"  synced __version__ {local} → {store} (Store was ahead)")
    except ValueError as exc:
        print(f"  warn: skip store sync ({exc})")


# ---------------------------------------------------------------------------
# Build + sign
# ---------------------------------------------------------------------------


def build_setup(*, require_sign: bool = False, bump: bool = True) -> str:
    """Build (bumping patch unless ``bump`` is off) into dist/UEFN-Ducky-Setup-<v>.exe."""
    print(f"=== Build release EXE ({'bumps patch' if bump else 'no bump'}) ===")
    cmd = [sys.executable, str(ROOT / "build" / "build_exes.py")]
    if not bump:
        cmd.append("--no-bump")
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    version = read_version()
    # Sign payload before Inno packs it into Setup (embedded EXE stays signed).
    payload = ROOT / "dist" / f"UEFN-Ducky-{version}.exe"
    if not payload.is_file():
        pending = ROOT / "dist" / f"UEFN-Ducky-{version}.pending.exe"
        payload = pending if pending.is_file() else payload
    if payload.is_file():
        cmd = [sys.executable, str(ROOT / "release" / "sign_windows.py")]
        if require_sign:
            cmd.append("--require")
        cmd.append(str(payload))
        print("=== Authenticode sign (payload) ===")
        subprocess.run(cmd, check=True, cwd=str(ROOT))
    print("=== Inno Setup installer ===")
    ps1 = ROOT / "release" / "installer" / "make_release_installer.ps1"
    subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
        check=True,
        cwd=str(ROOT),
    )
    setup = ROOT / "dist" / f"UEFN-Ducky-Setup-{version}.exe"
    if not setup.is_file():
        raise SystemExit(f"Installer did not produce {setup.name}")
    # Sign the outer Setup.exe — this is what Chrome downloads.
    cmd = [sys.executable, str(ROOT / "release" / "sign_windows.py")]
    if require_sign:
        cmd.append("--require")
    cmd.append(str(setup))
    print("=== Authenticode sign (Setup) ===")
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    return version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_setup_exe(version: str, override: Path | None) -> Path:
    if override is not None:
        if not override.is_file():
            raise SystemExit(f"Setup exe not found: {override}")
        return override
    candidate = ROOT / "dist" / f"UEFN-Ducky-Setup-{version}.exe"
    if not candidate.is_file():
        raise SystemExit(
            f"Missing {candidate.name} — default publish builds it; or pass --exe"
        )
    return candidate


# ---------------------------------------------------------------------------
# Store upload + release
# ---------------------------------------------------------------------------


def _presigned_put_file(upload_url: str, exe_path: Path, size: int) -> None:
    """Stream the file handle to the presigned URL (no second in-memory copy)."""
    parsed = urlparse(upload_url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise SystemExit(f"invalid uploadUrl scheme/host: {upload_url[:64]}…")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    headers = {
        "Content-Length": str(size),
        "Content-Type": "application/octet-stream",
        "User-Agent": "UEFN-Ducky-PublishApp/1.0",
    }
    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parsed.hostname, parsed.port, timeout=600)
    try:
        with exe_path.open("rb") as fh:
            conn.request("PUT", path, body=fh, headers=headers)
            resp = conn.getresponse()
            body = resp.read()
            if resp.status >= 400:
                detail = body.decode("utf-8", errors="replace")
                raise SystemExit(f"Presigned PUT HTTP {resp.status}: {detail}")
    finally:
        conn.close()


def _api_json(
    base: str,
    api_key: str,
    method: str,
    path: str,
    body: dict | None = None,
    timeout: int = 120,
) -> dict:
    url = base.rstrip("/") + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "UEFN-Ducky-PublishApp/1.0",
            "Origin": base.rstrip("/"),
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {path} HTTP {exc.code}: {detail}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{method} {path} bad response: {payload!r}")
    return payload


def upload_app_release(base: str, api_key: str, exe_path: Path) -> dict:
    """Direct-to-S3: ticket → PUT file handle → complete → poll job status."""
    filename = exe_path.name
    size = exe_path.stat().st_size
    digest = sha256_file(exe_path)
    ticket_resp = _api_json(
        base,
        api_key,
        "POST",
        "/api/files/app-release/ticket",
        {"filename": filename, "size": size, "sha256": digest},
    )
    upload_url = str(ticket_resp.get("uploadUrl") or "").strip()
    ticket = str(ticket_resp.get("ticket") or "").strip()
    if not upload_url or not ticket:
        # Fall back to legacy multipart if the new endpoints are not deployed yet.
        return _upload_app_release_multipart(base, api_key, exe_path)

    print(f"  ticket ok — streaming {size} bytes direct to object storage")
    _presigned_put_file(upload_url, exe_path, size)

    complete = _api_json(
        base,
        api_key,
        "POST",
        "/api/files/app-release/complete",
        {"ticket": ticket},
    )
    job_id = str(complete.get("jobId") or "").strip()
    if not job_id:
        raise SystemExit(f"complete missing jobId: {complete}")

    deadline = time.time() + 600
    while time.time() < deadline:
        status = _api_json(
            base,
            api_key,
            "GET",
            f"/api/files/app-release/status/{job_id}",
            timeout=60,
        )
        state = str(status.get("status") or "").lower()
        if state in ("complete", "completed", "succeeded", "ok"):
            if not status.get("ok", True) and status.get("error"):
                raise SystemExit(f"process failed: {status.get('error')}")
            return {
                "ok": True,
                "fileId": status.get("fileId") or "",
                "url": status.get("url") or f"/api/files/{status.get('fileId')}/content",
                "sha256": status.get("sha256") or digest,
                "size": status.get("size") or size,
                "latestUrl": "/api/files/app-release/latest",
            }
        if state in ("failed", "fail", "error"):
            raise SystemExit(f"process failed: {status.get('error') or status}")
        time.sleep(2)
    raise SystemExit(f"timed out waiting for job {job_id}")


def _upload_app_release_multipart(base: str, api_key: str, exe_path: Path) -> dict:
    boundary = "----DuckyAppReleaseBoundary7MA4YWxkTrZu0gW"
    filename = exe_path.name
    file_bytes = exe_path.read_bytes()
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode(),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    url = base.rstrip("/") + "/api/files/app-release"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "UEFN-Ducky-PublishApp/1.0",
            "Origin": base.rstrip("/"),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Upload HTTP {exc.code}: {detail}") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise SystemExit(f"Upload failed: {payload}")
    return payload


def mcp_call(base: str, api_key: str, name: str, arguments: dict) -> dict:
    url = base.rstrip("/") + "/api/v1/mcp"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "UEFN-Ducky-PublishApp/1.0",
            "Origin": base.rstrip("/"),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"MCP HTTP {exc.code}: {detail}") from exc
    if payload.get("error"):
        raise SystemExit(f"MCP error: {payload['error']}")
    result = payload.get("result") or {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text") or "").strip()
            if text.startswith("{"):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
            return {"ok": True, "text": text}
    return result if isinstance(result, dict) else {"ok": True, "result": result}


def absolute_url(base: str, url: str) -> str:
    text = (url or "").strip()
    if text.lower().startswith("https://") or text.lower().startswith("http://"):
        return text
    return base.rstrip("/") + "/" + text.lstrip("/")


def _self_check() -> None:
    assert parse_version_tuple("1.0.450") == (1, 0, 450)
    assert parse_version_tuple("1.0.450") < parse_version_tuple("1.0.451")
    # --set-version exists because a patch bump can never reach the next minor.
    assert parse_version_tuple("1.1.0") > parse_version_tuple("1.0.660")
    for bad in ("1.1", "1.1.0.0", "v1.1.0", ""):
        try:
            parse_version_tuple(bad)
            raise AssertionError(f"expected {bad!r} to be rejected")
        except ValueError:
            pass
    assert assert_uefn_ducky_store_base("https://uefnducky.org").endswith("uefnducky.org")
    try:
        assert_uefn_ducky_store_base("https://duckyos.org")
        raise AssertionError("expected apex host to be refused")
    except SystemExit:
        pass
    print("publish_app self-check ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=None, help="Skip build; publish this Setup exe")
    parser.add_argument("--notes", default="", help="Release notes for the in-app toast")
    parser.add_argument("--version", default="", help="Override version (only with --no-bump)")
    parser.add_argument(
        "--set-version",
        default="",
        help="Build and publish this exact version instead of bumping patch (e.g. 1.1.0)",
    )
    parser.add_argument(
        "--no-bump",
        action="store_true",
        help="Do not bump/rebuild — publish existing Setup at current __version__",
    )
    parser.add_argument("--self-check", action="store_true", help="Run version helper asserts and exit")
    parser.add_argument("--print-version", action="store_true", help="Print __version__ and exit")
    parser.add_argument(
        "--require-sign",
        action="store_true",
        help="Fail if Authenticode signing is not configured",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Bump/build Setup.exe and stop (CI Azure sign, then --no-bump --exe)",
    )
    args = parser.parse_args()

    if args.self_check:
        _self_check()
        return
    if args.print_version:
        print(read_version())
        return

    _load_dotenv()
    base, key = ensure_store_env()

    if args.exe is not None and not args.no_bump:
        raise SystemExit(
            "--exe skips the rebuild, so it requires --no-bump.\n"
            "Default publish always bumps + builds Setup, then uploads."
        )
    if args.build_only and (args.no_bump or args.exe is not None):
        raise SystemExit("--build-only always builds Setup; omit --no-bump and --exe")
    if (args.version or "").strip() and not args.no_bump:
        raise SystemExit("--version only makes sense with --no-bump")

    set_version = (args.set_version or "").strip()
    if set_version:
        if args.no_bump:
            raise SystemExit(
                "--set-version builds a fresh Setup, so it cannot pair with --no-bump.\n"
                "To publish an already-built Setup at a given version use --no-bump --version."
            )
        try:
            parse_version_tuple(set_version)
        except ValueError as exc:
            raise SystemExit(f"--set-version: {exc}") from exc

    if args.no_bump:
        version = (args.version or "").strip() or read_version()
        print(f"=== Publish without bump (version {version}) ===")
    elif set_version:
        # Patch bump can never cross a minor (1.0.660 → 1.0.661), so a minor/major
        # release has to write the version before an unbumped build.
        print("=== Publish to Store (explicit version) ===")
        print(f"  base: {base}")
        store_now = fetch_store_version(base)
        if store_now and parse_version_tuple(set_version) <= parse_version_tuple(store_now):
            raise SystemExit(
                f"Refusing publish: {set_version} is not newer than Store {store_now}."
            )
        before = read_version()
        write_version(set_version)
        version = build_setup(require_sign=args.require_sign, bump=False)
        print(f"  set {before} → {version}")
    else:
        print("=== Publish to Store (auto-bump) ===")
        print(f"  base: {base}")
        sync_local_at_least_store(base)
        before = read_version()
        version = build_setup(require_sign=args.require_sign)
        print(f"  bumped {before} → {version}")

    if args.build_only:
        if args.exe is not None:
            raise SystemExit("--build-only cannot pair with --exe")
        exe_path = find_setup_exe(version, None)
        print(f"build-only {exe_path}")
        print(f"VERSION={version}")
        return

    exe_path = find_setup_exe(version, args.exe)
    # --no-bump / --exe: sign the Setup we are about to upload (payload already baked in).
    if args.no_bump:
        cmd = [sys.executable, str(ROOT / "release" / "sign_windows.py")]
        if args.require_sign:
            cmd.append("--require")
        cmd.append(str(exe_path))
        print("=== Authenticode sign (Setup) ===")
        subprocess.run(cmd, check=True, cwd=str(ROOT))
    digest = sha256_file(exe_path)
    size = exe_path.stat().st_size
    print(f"publishing {exe_path.name} ({size} bytes, sha256={digest[:12]}…)")
    print(f"  version: {version}")
    print(f"  base:    {base}")

    store = fetch_store_version(base)
    if store:
        try:
            if parse_version_tuple(version) <= parse_version_tuple(store):
                raise SystemExit(
                    f"Refusing publish: {version} is not newer than Store {store}. "
                    "Re-run without --no-bump so it auto-bumps."
                )
        except ValueError:
            pass

    uploaded = upload_app_release(base, key, exe_path)
    file_id = str(uploaded.get("fileId") or "")
    remote_sha = str(uploaded.get("sha256") or "").lower()
    if remote_sha and remote_sha != digest:
        raise SystemExit(f"Server sha256 mismatch: local={digest} remote={remote_sha}")
    # Relative only — uds_app_release rejects absolute off-site URLs.
    installer_url = f"/api/files/{file_id}/content" if file_id else "/api/files/app-release/latest"
    print(f"  uploaded fileId={file_id}")
    print(f"  installerUrl={installer_url}")

    result = mcp_call(
        base,
        key,
        "uds_app_release",
        {
            "version": version,
            "installerUrl": installer_url,
            "sha256": digest,
            "fileId": file_id,
            "size": size,
            "notes": args.notes or "",
        },
    )
    print(json.dumps(result, indent=2))
    if result.get("ok") is False or result.get("error"):
        raise SystemExit(result.get("error") or "uds_app_release failed")
    print(f"ok — desktop apps will see {version} via collect/app-version")


if __name__ == "__main__":
    main()
