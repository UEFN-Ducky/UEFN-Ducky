"""The update pack a release ships beside Setup.

The whole app folder (``dist/UEFN-Ducky-<v>/``) goes into one zip, and a small manifest
zip says where each file's compressed bytes sit in it. An installed Ducky reads the
manifest, keeps every file it already has, fetches only the changed ones with HTTP
ranges, and switches to the new version without running Setup.

    py release/update_pack.py dist/UEFN-Ducky-1.2.410 1.2.410 dist
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import zipfile
from pathlib import Path

PACK_NAME = "UEFN-Ducky-Update-{version}.zip"
MANIFEST_NAME = "UEFN-Ducky-Update-{version}-files.zip"
MANIFEST_ENTRY = "manifest.json"
MANIFEST_FORMAT = 1
_LOCAL_HEADER = b"PK\x03\x04"
# Files a build may or may not ship (the native launcher needs cargo). Marked
# "optional": true in the manifest; readers that predate the key just ignore it.
OPTIONAL_FILES = frozenset({"Ducky-Launcher.exe", "WebView2Loader.dll"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pack_paths(out_dir: Path, version: str) -> tuple[Path, Path]:
    return out_dir / PACK_NAME.format(version=version), out_dir / MANIFEST_NAME.format(version=version)


def _remove_older_packs(out_dir: Path, keep: tuple[Path, Path]) -> None:
    for old in out_dir.glob("UEFN-Ducky-Update-*.zip"):
        if old not in keep:
            old.unlink(missing_ok=True)


def build_update_pack(app_dir: Path, version: str, out_dir: Path) -> tuple[Path, Path, dict]:
    """Write the pack and manifest zips for ``app_dir``; returns ``(pack, manifest_zip, manifest)``."""
    if not (app_dir / "UEFN-Ducky.exe").is_file():
        raise SystemExit(f"update pack: {app_dir} has no UEFN-Ducky.exe")
    pack, manifest_zip = pack_paths(out_dir, version)
    files = sorted((p for p in app_dir.rglob("*") if p.is_file()), key=lambda p: p.relative_to(app_dir).as_posix())
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(pack, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in files:
            rel = path.relative_to(app_dir).as_posix()
            hashes[rel] = _sha256(path)
            bundle.write(path, rel)
    entries = []
    with pack.open("rb") as raw, zipfile.ZipFile(pack) as bundle:
        for info in bundle.infolist():
            raw.seek(info.header_offset)
            header = raw.read(30)
            if header[:4] != _LOCAL_HEADER:
                raise SystemExit(f"update pack: bad local header for {info.filename}")
            name_len, extra_len = struct.unpack("<HH", header[26:30])
            entry = {
                "path": info.filename,
                "size": info.file_size,
                "sha256": hashes[info.filename],
                "offset": info.header_offset + 30 + name_len + extra_len,
                "csize": info.compress_size,
                "method": info.compress_type,
            }
            if info.filename in OPTIONAL_FILES:
                entry["optional"] = True
            entries.append(entry)
    manifest = {
        "format": MANIFEST_FORMAT,
        "version": version,
        "pack": {"name": pack.name, "size": pack.stat().st_size, "sha256": _sha256(pack)},
        "files": entries,
    }
    with zipfile.ZipFile(manifest_zip, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(MANIFEST_ENTRY, json.dumps(manifest, separators=(",", ":")))
    _remove_older_packs(out_dir, (pack, manifest_zip))
    return pack, manifest_zip, manifest


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    pack, manifest_zip, manifest = build_update_pack(Path(argv[0]), argv[1], Path(argv[2]))
    print(f"{pack.name}: {len(manifest['files'])} files, {manifest['pack']['size']} bytes; {manifest_zip.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
