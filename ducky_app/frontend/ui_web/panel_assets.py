"""Recover this release's panel when an installed asset is missing or damaged."""
from __future__ import annotations

import atexit
import json
import logging
import shutil
import tempfile
import threading
import zipfile
import zlib
from pathlib import Path, PurePosixPath

_lock = threading.Lock()
_recovered: dict[Path, tempfile.TemporaryDirectory] = {}


def create_panel_archive(dist: Path, archive: Path) -> None:
    from frontend.ui_web.panel_httpd import verify_panel_dist

    verify_panel_dist(dist)
    # Stored, not deflated: Setup's solid LZMA2 stream then finds these exact bytes
    # next to the installed panel and the copy costs ~0 MB of download (deflated it
    # cost ~5 MB). The copy still takes the panel's size on disk.
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        for path in sorted(dist.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(dist).as_posix())


def _entries(bundle: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    entries = bundle.infolist()
    names = set()
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if (entry.is_dir() or path.is_absolute() or ".." in path.parts
                or "\\" in entry.filename or ":" in entry.filename
                or entry.filename in names
                or (entry.external_attr >> 16) & 0o170000 == 0o120000):
            raise ValueError(f"Invalid panel archive entry: {entry.filename}")
        names.add(entry.filename)
    if "index.html" not in names:
        raise ValueError("Panel recovery archive has no index.html")
    return entries


def _matches(root: Path, entries: list[zipfile.ZipInfo]) -> bool:
    for entry in entries:
        path = root / entry.filename
        try:
            if path.stat().st_size != entry.file_size:
                return False
            crc = 0
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    crc = zlib.crc32(chunk, crc)
            if crc != entry.CRC:
                return False
        except OSError:
            return False
    return True


def verify_bundled_panel(root: Path, archive: Path) -> None:
    """Release gate: every installed file must match the recovery archive."""
    from frontend.ui_web.panel_httpd import verify_panel_dist

    verify_panel_dist(root)
    with zipfile.ZipFile(archive) as bundle:
        entries = _entries(bundle)
        if bundle.testzip() is not None or not _matches(root, entries):
            raise ValueError("Packaged panel differs from its recovery archive")


def check_installed_panel(output: str) -> int:
    """Headless installer gate; never loads settings, chats or WebView2."""
    from frontend.bundle_root import packaged_data_root

    report = {"ok": False}
    try:
        data_root = packaged_data_root()
        if data_root is None:
            raise ValueError("Installer verification requires a packaged runtime")
        panel = data_root / "frontend" / "ui_web"
        verify_bundled_panel(panel / "web" / "dist", panel / "panel-dist.zip")
        report["ok"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    Path(output).write_text(json.dumps(report), encoding="utf-8")
    return 0 if report["ok"] else 1


def ensure_panel_dist(root: Path, archive: Path | None = None) -> Path:
    """Return a complete panel, including for read-only all-user installs."""
    from frontend.ui_web.panel_httpd import verify_panel_dist

    root = root.resolve()
    archive = archive or root.parent.parent / "panel-dist.zip"
    if not archive.is_file():
        # Source checkouts and older builds have no recovery archive.
        verify_panel_dist(root)
        return root
    with _lock:
        if root in _recovered:
            return Path(_recovered[root].name)
        with zipfile.ZipFile(archive) as bundle:
            entries = _entries(bundle)
            if _matches(root, entries):
                verify_panel_dist(root)
                return root
            recovery = tempfile.TemporaryDirectory(prefix="uefn-ducky-panel-recovery-")
            recovered_root = Path(recovery.name)
            try:
                for entry in entries:
                    target = recovered_root / entry.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(entry) as source, target.open("wb") as dest:
                        shutil.copyfileobj(source, dest)
                verify_bundled_panel(recovered_root, archive)
            except BaseException:
                recovery.cleanup()
                raise
        _recovered[root] = recovery
        atexit.register(recovery.cleanup)
        logging.getLogger(__name__).warning("Recovered incomplete installed panel: %s", root)
        return recovered_root
