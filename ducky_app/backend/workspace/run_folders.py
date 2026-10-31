"""Folders a workflow run lends to the agents doing its steps.

A workflow names folders in its text inputs (an "Output root folder"). While one of its
Agent steps works, Ducky's file tools reach those folders for that step's chat only, and
the grant ends with the step. The grants live in a small AppData file so a coding agent's
own tool server (another process) sees them too; each one also expires on its own, so a
crash never leaves a folder open.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

_FILE = "run_folders.json"
_lock = threading.Lock()


def _path() -> Path:
    from frontend.settings import default_app_data_dir

    return default_app_data_dir() / _FILE


def _off_limits() -> list[str]:
    """System and app-data folders a workflow never lends, whatever it names."""
    names = ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "APPDATA", "LOCALAPPDATA")
    return [os.path.normcase(os.path.realpath(v)) for v in (os.environ.get(n, "") for n in names) if v]


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def usable_folder(raw: Any) -> str:
    """The absolute folder a workflow input names, or "" when it names none or one too
    broad or private to lend: a drive, the user folder or above it, system and app-data
    folders, or anything inside a dot-folder (.ssh, .aws, .claude …)."""
    text = str(raw or "").strip().strip('"').strip()
    if not text or "\n" in text:
        return ""
    text = os.path.expandvars(os.path.expanduser(text))
    if not os.path.isabs(text) or os.path.isfile(text):
        return ""
    full = os.path.realpath(os.path.abspath(text))
    norm = os.path.normcase(full)
    parts = Path(full).parts
    if len(parts) < 2 or any(p.startswith(".") for p in parts[1:]):
        return ""
    home = os.path.normcase(os.path.realpath(str(Path.home())))
    if _inside(home, norm):
        return ""
    if any(_inside(norm, blocked) for blocked in _off_limits()):
        return ""
    return full


def named_folders(nodes: Iterable[dict[str, Any]]) -> list[str]:
    """Folders a workflow's text inputs name (after %VARS% and ~)."""
    found: list[str] = []
    for node in nodes:
        if str(node.get("type") or "") != "input.text":
            continue
        folder = usable_folder((node.get("config") or {}).get("value"))
        if folder and folder not in found:
            found.append(folder)
    return found


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def grant(conv_id: str, folders: list[str], seconds: float) -> Callable[[], None]:
    """Lend ``folders`` to ``conv_id`` for at most ``seconds``. Returns the call that ends it."""
    folders = [f for f in folders if f]
    if not conv_id or not folders:
        return lambda: None
    key = uuid.uuid4().hex[:12]
    until = time.time() + max(float(seconds), 1.0)
    with _lock:
        data = {k: v for k, v in _load().items() if isinstance(v, dict) and float(v.get("until") or 0) > time.time()}
        data[key] = {"conv_id": conv_id, "folders": folders, "until": until}
        _save(data)

    def release() -> None:
        with _lock:
            data = _load()
            if data.pop(key, None) is not None:
                _save(data)

    return release


def _calling_conv() -> str:
    try:
        from backend.workspace import identity

        ctx = identity.resolve_context()
    except Exception:
        ctx = None
    return str(getattr(ctx, "conv_id", "") or "")


def granted(conv_id: str = "") -> list[str]:
    """Folders lent to this chat right now (the calling agent's chat when not given): a
    workflow step's, and the ones the person allowed for it (ask_to_lend)."""
    conv_id = conv_id or _calling_conv()
    if not conv_id:
        return []
    now = time.time()
    out: list[str] = []
    for row in _load().values():
        if isinstance(row, dict) and row.get("conv_id") == conv_id and float(row.get("until") or 0) > now:
            out += [str(f) for f in row.get("folders") or [] if f and str(f) not in out]
    try:
        from backend.tools.panel.permission_prompt import approved_folders

        out += [f for f in approved_folders(conv_id) if f not in out]
    except Exception:
        pass
    try:
        from backend.sandbox import paths as sandbox_paths

        if sandbox_paths.exists(conv_id):
            out.append(os.path.realpath(str(sandbox_paths.sandbox_dir(conv_id))))
    except Exception:
        pass
    return out


# "Allow once" lends the folder this long: the read and the write the agent asked for.
_ASKED_LEND_S = 120.0


def _nearest_folder(path: str) -> str:
    """``path`` when it is a folder, else its closest existing parent: a new file in a new
    subfolder asks for the folder that is already there."""
    p = Path(path)
    while not p.is_dir() and p.parent != p:
        p = p.parent
    return str(p)


def _is_verse_digest(path: str) -> bool:
    parts = [p.lower() for p in Path(path).parts]
    return "digests" in parts or bool(parts) and parts[-1].endswith(".digest.verse")


def ask_to_lend(path: str) -> str:
    """An agent's file tool reached ``path``, outside the person's Ducky projects: ask the
    person whether its chat may use that folder. "" when it may now, else why not."""
    if _is_verse_digest(path):
        # Oct 30 2026: an agent retried workspace_read_file on Fortnite.digest.verse in UEFN's
        # AppData four times; the digest tools read exactly those files.
        return ("That is a UEFN Verse digest (Epic's API listing). Read it with search_verse_digest(query) "
                "or get_verse_api(name); list_verse_digests lists every digest. File tools never open AppData.")
    conv_id = _calling_conv()
    if not conv_id:
        return "Tell the person their folder is outside their Ducky projects."
    folder = usable_folder(_nearest_folder(path))
    if not folder:
        return ("Ducky never opens system, app-data, hidden or whole-user folders. Ask the person to "
                "put the file in one of their projects or a normal folder, such as one in Documents.")
    from backend.tools.panel.permission_prompt import approve_outside_folder

    refusal = approve_outside_folder(conv_id, folder, path)
    if not refusal:
        grant(conv_id, [folder], _ASKED_LEND_S)
    return refusal


def is_granted_root(root: str, conv_id: str = "") -> bool:
    norm = os.path.normcase(os.path.realpath(root or ""))
    return any(os.path.normcase(f) == norm for f in granted(conv_id))
