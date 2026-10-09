"""Google fonts picked in Appearance, downloaded once and served from this PC.

The app never loads anything from the internet at runtime: the first time a font is
picked, its stylesheet and font files are fetched from Google Fonts into AppData, and the
panel loads them from the local panel server (``/__fonts/<slug>/…``) from then on.
"""

from __future__ import annotations

import re
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from frontend.app_paths import resolve_app_data_dir

_CSS_HOST = "https://fonts.googleapis.com/css2"
_FONT_HOST = "https://fonts.gstatic.com/"
# Google serves woff2 only to browsers it recognises.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_FAMILY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ]{0,63}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FILE = re.compile(r"^(?:font\.css|\d{1,3}\.woff2)$")
_URL = re.compile(r"url\((https://fonts\.gstatic\.com/[^)\s'\"]+)\)")
MAX_CSS_BYTES = 256 * 1024
MAX_FONT_BYTES = 2 * 1024 * 1024
MAX_FILES = 120
_LOCK = threading.Lock()


def fonts_dir() -> Path:
    return resolve_app_data_dir() / "fonts"


def font_slug(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", family.strip().lower()).strip("-")


def _get(url: str, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA, "Accept": "text/css,*/*;q=0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — Google Fonts hosts only
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError("font download too big")
    return data


def cache_google_font(family: str) -> dict[str, Any]:
    """Download ``family`` once; returns ``{"ok", "href"}`` with the local stylesheet path."""
    name = (family or "").strip().strip("'\"")
    if not _FAMILY.match(name):
        return {"ok": False, "error": "not a font family name"}
    slug = font_slug(name)
    folder = fonts_dir() / slug
    href = f"/__fonts/{slug}/font.css"
    with _LOCK:
        if (folder / "font.css").is_file():
            return {"ok": True, "href": href, "cached": True}
        query = urllib.parse.urlencode({"family": f"{name}:wght@400;500;600;700", "display": "swap"})
        try:
            css = _get(f"{_CSS_HOST}?{query}", MAX_CSS_BYTES).decode("utf-8", "replace")
            urls = list(dict.fromkeys(_URL.findall(css)))
            if not urls or len(urls) > MAX_FILES:
                return {"ok": False, "error": f"Google Fonts has no font named {name!r}"}
            tmp = folder.with_name(folder.name + ".part")
            tmp.mkdir(parents=True, exist_ok=True)
            for i, url in enumerate(urls):
                if not url.startswith(_FONT_HOST):
                    continue
                (tmp / f"{i}.woff2").write_bytes(_get(url, MAX_FONT_BYTES))
                css = css.replace(f"url({url})", f"url(/__fonts/{slug}/{i}.woff2)")
            (tmp / "font.css").write_text(css, encoding="utf-8")
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
            tmp.replace(folder)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            shutil.rmtree(folder.with_name(folder.name + ".part"), ignore_errors=True)
            return {"ok": False, "error": f"couldn't download {name}: {exc}"}
    return {"ok": True, "href": href, "cached": False}


def font_file(slug: str, name: str) -> Path | None:
    """A cached font file for the panel server, or None (never outside the fonts folder)."""
    if not _SLUG.match(slug or "") or not _FILE.match(name or ""):
        return None
    path = (fonts_dir() / slug / name).resolve()
    root = fonts_dir().resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path
