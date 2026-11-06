"""Settings > Sounds: pick an audio file and copy it into AppData/sounds/."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

ALLOWED = {".mp3", ".wav", ".ogg", ".m4a", ".webm"}


def _pick_with_webview(win: Any) -> str | None:
    try:
        import webview

        try:
            open_type = webview.FileDialog.OPEN
        except AttributeError:
            open_type = getattr(webview, "OPEN_DIALOG", 10)
        result = win.create_file_dialog(
            open_type,
            file_types=(
                "Audio (*.mp3;*.wav;*.ogg;*.m4a;*.webm)",
                "All files (*.*)",
            ),
        )
        return str(result[0]) if result else None
    except Exception:
        return None


def _pick_with_tk() -> str | None:
    """Raises when no dialog can be opened."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        return filedialog.askopenfilename(
            title="Choose sound file",
            parent=root,
            filetypes=[
                ("Audio", "*.mp3 *.wav *.ogg *.m4a *.webm"),
                ("All", "*.*"),
            ],
        ) or None
    finally:
        root.destroy()


def copy_into_sounds(picked: str | None, sounds_dir: Path) -> dict[str, Any]:
    """Copy *picked* into *sounds_dir* under a safe unique name: ``{ok, filename}`` or ``{ok: False, error}``."""
    if not picked:
        return {"ok": False, "error": "cancelled"}
    src = Path(picked)
    if not src.is_file():
        return {"ok": False, "error": "File not found"}
    ext = src.suffix.lower()
    if ext not in ALLOWED:
        return {"ok": False, "error": f"Unsupported audio type: {ext or '(none)'}"}
    sounds_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in src.stem)[:48] or "sound"
    filename = f"{safe_stem}_{uuid.uuid4().hex[:8]}{ext}"
    try:
        shutil.copy2(src, sounds_dir / filename)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "filename": filename}


def import_sound_file(win: Any) -> dict[str, Any]:
    """Pick an audio file (the panel window's dialog, else Tk) and copy it into AppData/sounds/."""
    from backend.skills.discovery import appdata_dir

    picked = _pick_with_webview(win) if win is not None else None
    if not picked:
        try:
            picked = _pick_with_tk()
        except Exception:
            return {"ok": False, "error": "Could not open file dialog"}
    return copy_into_sounds(picked, appdata_dir() / "sounds")
