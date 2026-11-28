"""Download the N-1 DMG for macos-install-check's update/rollback jobs to old.dmg.

The Store has no per-version download path; files live at /api/v1/files/<id>/content.
FROM_DMG (a Store file id or URL) picks N-1 directly; empty means N-1 is the feed's
current release, taken from the feed row and checked against its sha256.
"""
import hashlib
import json
import os
import sys
import urllib.request

BASE = "https://uefnducky.org"
# The Store answers 403 without the app's Origin + User-Agent.
UA = {"User-Agent": "UEFN-Ducky/install-check"}


def link_for(ref):
    if ref.startswith("https://"):
        return ref
    if ref.startswith("/"):
        return BASE + ref
    return f"{BASE}/api/v1/files/{ref}/content"


def feed_row(arch, version):
    req = urllib.request.Request(
        f"{BASE}/api/v1/plugins/uefn-ducky-store/collect/app-version",
        data=b"{}",
        headers={"Content-Type": "application/json", "Origin": BASE, **UA},
    )
    payload = json.load(urllib.request.urlopen(req, timeout=30)).get("payload") or {}
    current = payload.get("currentVersion")
    if current != version:
        sys.exit(f"from_version {version} is not the feed's current {current}: pass from_dmg_{arch} (its Store file id)")
    row = next((r for r in payload.get("assets") or [] if (r.get("os"), r.get("arch"), r.get("package")) == ("macos", arch, "dmg")), None)
    if not row:
        sys.exit(f"no macos {arch} dmg row in the feed ({current})")
    return row


def main():
    arch, version, ref = os.environ["ARCH"], os.environ["FROM_VERSION"], os.environ.get("FROM_DMG", "").strip()
    if not version:
        sys.exit("update/rollback need from_version (N-1)")
    row = None if ref else feed_row(arch, version)
    link = link_for(ref or row["url"])
    data = urllib.request.urlopen(urllib.request.Request(link, headers=UA), timeout=900).read()
    if row and hashlib.sha256(data).hexdigest() != str(row["sha256"]).lower():
        sys.exit("sha256 mismatch on the feed DMG")
    open("old.dmg", "wb").write(data)
    print("fetched", version, arch, link, len(data), "bytes")


if __name__ == "__main__":
    main()
