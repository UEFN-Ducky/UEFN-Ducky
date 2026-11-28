"""A loopback release feed for macos-install-check: the app's update check reads it through
DUCKY_UPDATE_BASE_URL=http://127.0.0.1:8765 (plain http is allowed only on loopback).

Usage: python3 build/macos/serve_test_feed.py <folder> <version> <arch>
Serves <folder>/uefn-ducky-update-<version>-macos-<arch>.zip as the feed's app-zip row and
answers every /api/ request with the feed, as the Store's collect/app-version does.
"""
import hashlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765


def feed(version: str, arch: str, name: str, data: bytes) -> bytes:
    row = {"os": "macos", "arch": arch, "package": "app-zip", "url": f"http://127.0.0.1:{PORT}/{name}",
           "sha256": hashlib.sha256(data).hexdigest(), "sig_url": ""}
    return json.dumps({"handled": True, "payload": {"currentVersion": version, "assets": [row]}}).encode()


def main(folder: str, version: str, arch: str) -> None:
    name = f"uefn-ducky-update-{version}-macos-{arch}.zip"
    data = (Path(folder) / name).read_bytes()
    body = feed(version, arch, name, data)

    class Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            out = body if self.path.startswith("/api/") else data if self.path == f"/{name}" else b""
            self.send_response(200 if out else 404)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        do_GET = do_POST = _reply

    print(f"test feed: {version} {arch} on 127.0.0.1:{PORT} ({len(data)} bytes)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main(*sys.argv[1:4])
