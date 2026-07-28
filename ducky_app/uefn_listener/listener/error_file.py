"""Append editor-side errors to %LOCALAPPDATA%/UEFN-Ducky/errors.jsonl.

Self-contained (the in-editor listener cannot import frontend / uefn_ducky). Writes the same
JSON-lines format the control panel's Errors window reads: {"ts", "source", "message"}.
"""

import json
import os
import time

try:
    from pathlib import Path
except Exception:  # extremely defensive — Path is always present on 3.11
    Path = None

_last_message = None  # in-session dedupe of consecutive identical errors


def _errors_path():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "UEFN-Ducky", "errors.jsonl")


def record_error(source, message):
    """Append one error line. No-ops on any failure — logging must never break the editor."""
    global _last_message
    try:
        message = (message or "").strip()
        if not message or message == _last_message:
            return
        _last_message = message
        path = _errors_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {"ts": time.time(), "source": source, "message": message[:4000]}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
