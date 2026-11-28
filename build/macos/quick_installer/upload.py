#!/usr/bin/env python3
"""Put the Mac quick installer on the site: web-setup/UEFN-Ducky-Installer-macos.zip in public
storage, replaced in place (same file id, so the Mac button's link keeps working). Like the
Windows web Setup it is kept out of app-releases/ and only re-uploaded when main.swift changes.

    gh run download <macos-quick-installer run> -n UEFN-Ducky-Installer-macos
    py build/macos/quick_installer/upload.py UEFN-Ducky-Installer-macos.zip
"""

from __future__ import annotations

import base64
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NAME = "UEFN-Ducky-Installer-macos.zip"


def main(path: Path) -> None:
    # The CI artifact, not something else by that name: one app with the universal binary.
    with zipfile.ZipFile(path) as z:
        if "UEFN Ducky Installer.app/Contents/MacOS/UEFN Ducky Installer" not in z.namelist():
            raise SystemExit(f"{path} is not the quick installer zip")
    sys.path.insert(0, str(ROOT / "release"))
    import publish_app

    base, key = publish_app.ensure_store_env()
    result = publish_app.mcp_call(base, key, "storage_upload", {
        "filename": NAME,
        "prefix": "web-setup",
        "namespace": "media",
        "public": True,
        "replace": True,
        "mimeType": "application/zip",
        "contentBase64": base64.b64encode(path.read_bytes()).decode("ascii"),
    })
    file = result.get("file") or {}
    if not result.get("ok") or not file.get("id"):
        raise SystemExit(f"Upload failed: {result}")
    print(f"{base}/api/v1/files/{file['id']}/content  ({file.get('sizeBytes')} bytes, sha256 {file.get('sha256')})")
    print("Set it as the download block's macQuickInstallerUrl (the path part) to make it the Mac button.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
