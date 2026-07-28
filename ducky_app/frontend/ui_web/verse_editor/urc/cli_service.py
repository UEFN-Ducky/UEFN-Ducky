"""URC operations via urc.exe CLI (v1-style, no VS Code extension glue)."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from frontend.ui_web.verse_editor.urc.detect_urc import detect_urc_executable

_LOG = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _run_urc(urc_exe: Path, project_root: Path, args: list[str], timeout: float = 120.0) -> str:
    cmd = [str(urc_exe), *args]
    kwargs: dict[str, object] = {
        "cwd": str(project_root),
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    proc = subprocess.run(cmd, **kwargs)  # type: ignore[arg-type]
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        _LOG.error("urc command failed: %s (exit %s): %s", " ".join(args), proc.returncode, err)
        raise RuntimeError(err or f"urc exited {proc.returncode}")
    return (proc.stdout or "").strip()


def _file_names(items: Any) -> list[str]:
    """Normalize a URC change list to display paths, tolerating unknown item shapes."""
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            for key in ("path", "relativePath", "file", "filename", "name"):
                val = item.get(key)
                if isinstance(val, str) and val:
                    names.append(val)
                    break
    return names


def _parse_status(output: str) -> dict[str, Any]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid URC JSON: {output[:200]}") from exc
    if isinstance(data, dict) and "data" in data:
        return data
    return {"data": data}


class UrcCliService:
    def __init__(self) -> None:
        self._urc_path = ""
        self._project_root = ""

    def configure(self, project_root: str, urc_path: str = "") -> dict[str, object]:
        self._project_root = project_root.strip()
        detected = detect_urc_executable()
        self._urc_path = urc_path.strip() or str(detected.get("path") or "")
        if not self._urc_path:
            return {**detected, "project_root": self._project_root}
        return {
            "available": True,
            "path": self._urc_path,
            "project_root": self._project_root,
            "error": "",
        }

    def _exe(self) -> Path:
        if not self._urc_path:
            raise RuntimeError("URC not configured")
        return Path(self._urc_path)

    def _root(self) -> Path:
        if not self._project_root:
            raise RuntimeError("No project root")
        return Path(self._project_root)

    def get_status(self) -> dict[str, Any]:
        out = _run_urc(self._exe(), self._root(), ["project", "status", "--json"])
        parsed = _parse_status(out)
        data = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
        staged = data.get("stagedChanges") or data.get("staged") or []
        unstaged = data.get("unstagedChanges") or data.get("unstaged") or []
        staged_files = _file_names(staged)
        unstaged_files = _file_names(unstaged)
        return {
            "available": True,
            "staged_count": len(staged_files),
            "unstaged_count": len(unstaged_files),
            "staged": staged_files,
            "unstaged": unstaged_files,
            "raw": data,
            "error": "",
        }

    def commit(self, message: str = "") -> dict[str, Any]:
        _run_urc(
            self._exe(),
            self._root(),
            ["trigger", "--command", "CheckIn", "--json"],
        )
        return {"ok": True, "message": message or "CheckIn triggered"}

    def push(self) -> dict[str, Any]:
        return {"ok": False, "error": "Push not available via URC CLI v1 in this project"}

    def pull(self) -> dict[str, Any]:
        return {"ok": False, "error": "Pull not available via URC CLI v1 in this project"}


_urc_service = UrcCliService()


def get_urc_service() -> UrcCliService:
    return _urc_service
