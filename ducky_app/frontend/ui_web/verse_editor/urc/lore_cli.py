"""Read-only island sync state via UEFN's ``lore.exe`` CLI (status, history, revision diff, locks).

Never syncs, pushes, commits or stages. Text output only (lore 0.8.6 has no JSON flag).
Lore prints paths relative to the CWD, so every command runs with cwd = the island root.
Parsing is pure (``parse_*``, ``compare_histories``); ``_run_lore`` is the only subprocess
call (tests patch it).

Sync state is measured against the remote ``main`` branch, not ``lore status``'s "Remote
revision": after an editor save UEFN moves the island to a personal ``main-<id>`` branch and
pushes its autosave there, so status reads "in sync" while ``main`` still lacks the change.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from frontend.ui_web.verse_editor.urc.detect_urc import detect_lore_executable

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
# The shared branch every PC checks in to.
REMOTE_BRANCH = "main"
# Revisions walked (first parent) to find where local and remote main histories meet.
_HISTORY_DEPTH = 200

_BRANCH_RE = re.compile(r"^On branch (\S+) revision (\d+) -> ([0-9a-f]+)")
_ENTRY_RE = re.compile(r"^([A-Z]) (.+)$")
_FIELD_RE = re.compile(r"^(Revision|Signature|Merge|Creator)\s*:\s*(.+)$")
# Epic display names pad with invisible fillers (Hangul filler U+3164 and kin): "Michㅤㅤou".
_FILLERS = re.compile("[ᅟᅠㅤﾠ​-‍⠀]+")
# ``lore lock query``: "<rel> by <name> on branch <id>"; ``lore lock status``: "... on <date>".
_LOCK_RE = re.compile(r"^(.+) by (.+?) on (?:branch \S+|\w{3}, .+)$")
# ``lore auth info``: "ID: <hex>" / "Name: <display name>".
_AUTH_RE = re.compile(r"^(ID|Name):\s*(.*)$")
_ME_TTL_S = 600.0  # the signed-in account rarely changes: one ``auth info`` per 10 min
_me_cache: dict[str, tuple[float, dict[str, str]]] = {}
_me_lock = threading.Lock()
# Per-user editor collections, never shared between PCs.
_EXCLUDED_PREFIX = "content/developers/"
# Lore's copies beside a text file it merged with conflicts (Oct 27): never pushed.
_SIDE_FILE_RE = re.compile(r"~(base|mine|theirs)$", re.IGNORECASE)
# Text files a merge can leave conflict markers in, read up to this many bytes each.
_TEXT_EXTS = frozenset({".verse", ".txt", ".json", ".ini", ".uefnproject", ".uplugin", ".csv"})
_MARKER_MAX_BYTES = 512 * 1024
_MARKER_OPEN_RE = re.compile(r"^<{7} ", re.MULTILINE)
_MARKER_CLOSE_RE = re.compile(r"^>{7} ", re.MULTILINE)
# How this PC changed a file (``overlap``'s ``local``).
_CHANGE_WORDS = {"A": "added", "M": "modified", "D": "deleted"}


def _norm(path: str) -> str:
    return path.strip().replace("\\", "/")


def parse_entry(line: str) -> tuple[str, str] | None:
    """``A Content/Foo.uasset`` → ``("A", "Content/Foo.uasset")``. Directories keep their ``/``."""
    m = _ENTRY_RE.match(line.rstrip())
    if not m:
        return None
    path = m.group(2)
    if " -> " in path:  # move/copy: the new location is what the remote lacks
        path = path.split(" -> ", 1)[1]
    return m.group(1), _norm(path)


def parse_status(text: str) -> dict[str, Any]:
    """Parse ``lore status``: repository, local branch/head, and working-tree changes.

    Entries under "Untracked files:" go to ``untracked``; every other change section (staged,
    dirty) goes to ``staged`` as ``(code, rel)`` pairs. Its "Remote revision" / "Local branch is
    …" lines track the current (maybe personal) branch, so they are ignored.
    """
    out: dict[str, Any] = {
        "repository": "",
        "local_branch": "",
        "local_revision": None,
        "local_signature": "",
        "staged": [],
        "untracked": [],
    }
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Repository "):
            out["repository"] = line.split(" ", 1)[1].strip()
        elif m := _BRANCH_RE.match(line):
            out["local_branch"] = m.group(1)
            out["local_revision"] = int(m.group(2))
            out["local_signature"] = m.group(3)
        elif line.endswith(":") and not _ENTRY_RE.match(line):
            section = "untracked" if line.lower().startswith("untracked") else "staged"
        elif section and (entry := parse_entry(raw)):
            out[section].append(entry)
    return out


def parse_history(text: str) -> list[dict[str, Any]]:
    """``lore history`` (full format) → ``[{revision, signature, merges, creator, message}]``,
    newest first.

    The list follows first parents; ``merges`` holds the other parent(s) a check-in merged in
    (UEFN merges the personal autosave branch into ``main``). ``creator`` is the Epic display
    name as lore prints it, ``message`` the first line of the check-in description.
    """
    out: list[dict[str, Any]] = []
    for raw in text.splitlines():
        m = _FIELD_RE.match(raw.strip())
        if not m:
            if out and raw[:1].isspace() and raw.strip() and not out[-1]["message"]:
                out[-1]["message"] = raw.strip()
            continue
        key, val = m.group(1), m.group(2).strip()
        if key == "Revision":
            out.append({"revision": int(val) if val.isdigit() else None, "signature": "", "merges": [],
                        "creator": "", "message": ""})
        elif out and key == "Signature":
            out[-1]["signature"] = val
        elif out and key == "Creator":
            out[-1]["creator"] = val
        elif out:
            out[-1]["merges"].extend(val.split())
    return out


def display_name(name: str) -> str:
    """An Epic display name without its invisible padding: "Michㅤㅤou" → "Mich ou"."""
    return " ".join(_FILLERS.sub(" ", name or "").split())


def parse_locks(text: str) -> list[dict[str, str]]:
    """``lore lock query`` / ``lore lock status`` → ``[{rel, owner_raw}]`` (``owner_raw`` as lore
    prints it, invisible padding included). Header lines ("Locks found:", "Files locked for
    edit:") and anything else are skipped."""
    out: list[dict[str, str]] = []
    for raw in text.splitlines():
        m = _LOCK_RE.match(raw.strip())
        if m:
            out.append({"rel": _norm(m.group(1)), "owner_raw": m.group(2).strip()})
    return out


def parse_auth_info(text: str) -> dict[str, str]:
    """``lore auth info`` → ``{id, name}`` (``name`` raw, padding included; "" when missing)."""
    out = {"id": "", "name": ""}
    for raw in text.splitlines():
        m = _AUTH_RE.match(raw.strip())
        if m:
            out["id" if m.group(1) == "ID" else "name"] = m.group(2).strip()
    return out


def _reachable(history: list[dict[str, Any]]) -> set[str]:
    return {sig for rev in history for sig in (rev["signature"], *rev["merges"]) if sig}


def compare_histories(local: list[dict[str, Any]], remote: list[dict[str, Any]]) -> dict[str, Any]:
    """Local head vs remote main head → {merge_base, behind, ahead, in_sync}.

    merge_base = newest local first-parent revision the remote reaches ("" when none in range).
    behind = remote head not in the local ancestry; ahead = local head not reachable from remote.
    """
    local_head = local[0]["signature"] if local else ""
    remote_head = remote[0]["signature"] if remote else ""
    remote_set = _reachable(remote)
    base = next((rev["signature"] for rev in local if rev["signature"] in remote_set), "")
    behind = bool(remote_head) and remote_head not in _reachable(local)
    ahead = bool(local_head) and base != local_head
    return {"merge_base": base, "behind": behind, "ahead": ahead, "in_sync": not behind and not ahead}


def incoming(local: list[dict[str, Any]], remote: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remote main's first-parent revisions the local head lacks, newest first:
    ``[{revision, signature, creator, message, own}]``. ``own``: the check-in merges a revision
    the local history already has (this PC's own autosave checked in to main), so it brings
    nothing made elsewhere."""
    local_set = _reachable(local)
    out: list[dict[str, Any]] = []
    for rev in remote:
        if rev["signature"] in local_set:
            break
        out.append({
            "revision": rev["revision"],
            "signature": rev["signature"],
            "creator": display_name(rev.get("creator", "")),
            "message": rev.get("message", ""),
            "own": any(sig in local_set for sig in rev["merges"]),
        })
    return out


def parse_revision_diff(text: str) -> list[tuple[str, str]]:
    """``lore revision diff`` lines → ``(code, rel)`` pairs. Directories are listed without ``/``."""
    return [e for line in text.splitlines() if (e := parse_entry(line))]


def _excluded(rel: str) -> bool:
    rel = rel.rstrip("/")
    return (rel.lower() + "/").startswith(_EXCLUDED_PREFIX) or bool(_SIDE_FILE_RE.search(rel))


def to_pending_files(project_root: str, entries: list[tuple[str, str]]) -> list[str]:
    """Absolute forward-slash paths of files that exist on disk, deduped, in order.

    Deletes (``D``) and anything missing on disk are dropped: there is no file to send.
    An untracked ``dir/`` expands to the files under it (lore lists only the folder). Bare
    directory lines from ``revision diff`` are skipped: it lists their files too.
    """
    root = Path(project_root)
    seen: set[str] = set()
    out: list[str] = []

    def add(p: Path) -> None:
        rel = p.relative_to(root).as_posix()
        if _excluded(rel):
            return
        abs_path = p.as_posix()
        if abs_path not in seen:
            seen.add(abs_path)
            out.append(abs_path)

    for code, rel in entries:
        if code == "D" or not rel or rel.startswith("../") or _excluded(rel):
            continue
        p = root / rel.rstrip("/")
        if rel.endswith("/"):
            for child in sorted(p.rglob("*")) if p.is_dir() else []:
                if child.is_file():
                    add(child)
        elif p.is_file():
            add(p)
    return out


def _files_only(project_root: str, entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Entries naming files: ``revision diff`` lists a folder (no trailing ``/``) before its files,
    so a path some other entry sits under, or a folder on disk, is dropped. Untracked ``dir/``
    lines stay (they stand for every file under them). Content/Developers/** is excluded."""
    parents: set[str] = set()
    for _, rel in entries:
        parts = rel.lower().rstrip("/").split("/")
        parents.update("/".join(parts[:i]) for i in range(1, len(parts)))
    root = Path(project_root)
    out: list[tuple[str, str]] = []
    for code, rel in entries:
        if not rel or rel.startswith("../") or _excluded(rel):
            continue
        if not rel.endswith("/") and (rel.lower() in parents or (root / rel).is_dir()):
            continue
        out.append((code, rel))
    return out


def _run_lore(project_root: str, args: list[str], timeout: float) -> tuple[bool, str]:
    """Run ``lore.exe -P --repository <root> <args>``. Returns (ok, stdout or error)."""
    detected = detect_lore_executable()
    if not detected.get("available"):
        return False, str(detected.get("error") or "lore.exe not found")
    cmd = [str(detected["path"]), "-P", "--repository", project_root, *args]
    kwargs: dict[str, object] = {
        "cwd": project_root,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, **kwargs)  # type: ignore[arg-type]
    except subprocess.TimeoutExpired:
        return False, f"lore {args[0]} timed out after {timeout:g}s"
    except OSError as exc:
        return False, f"lore {args[0]} failed: {exc}"
    stdout = proc.stdout or ""
    if proc.returncode != 0 or stdout.lstrip().startswith("[Error]"):
        err = (proc.stderr or stdout).strip().splitlines()
        return False, (err[0] if err else f"lore {args[0]} exited {proc.returncode}")
    return True, stdout


def _failed(error: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, **extra, "error": error}


def _state(project_root: str, timeout: float) -> tuple[dict[str, Any] | None, str]:
    """status + local history + remote main history → parsed state (with ``merge_base``)."""
    if not (Path(project_root) / ".lore").is_dir():
        return None, "not a Lore repository (no .lore folder)"
    ok, out = _run_lore(project_root, ["status"], timeout)
    if not ok:
        return None, out
    st = parse_status(out)
    if not st["local_signature"]:
        return None, "lore status shows no current revision"
    depth = str(_HISTORY_DEPTH)
    ok, local_out = _run_lore(project_root, ["history", depth, "--revision", st["local_signature"]], timeout)
    if not ok:
        return None, local_out
    ok, remote_out = _run_lore(project_root, ["--remote", "history", depth, "--branch", REMOTE_BRANCH], timeout)
    if not ok:
        return None, remote_out
    remote = parse_history(remote_out)
    if not remote:
        return None, f"remote branch {REMOTE_BRANCH} has no revisions"
    local = parse_history(local_out)
    cmp = compare_histories(local, remote)
    if not cmp["merge_base"]:
        return None, f"no revision shared with remote {REMOTE_BRANCH} in the last {depth}"
    st.update(
        cmp,
        changes=[*st["staged"], *st["untracked"]],
        remote_branch=REMOTE_BRANCH,
        remote_revision=remote[0]["revision"],
        remote_signature=remote[0]["signature"],
        incoming=incoming(local, remote),
    )
    return st, ""


def _head(project_root: str, args: list[str], timeout: float) -> dict:
    if not (Path(project_root) / ".lore").is_dir():
        return _failed("not a Lore repository (no .lore folder)")
    ok, out = _run_lore(project_root, args, timeout)
    if not ok:
        return _failed(out)
    extra: dict[str, Any] = {}
    if args == ["status"]:
        st = parse_status(out)
        rev, sig = st["local_revision"], st["local_signature"]
        extra["changes"] = [*st["staged"], *st["untracked"]]
    else:
        hist = parse_history(out)
        rev, sig = (hist[0]["revision"], hist[0]["signature"]) if hist else (None, "")
    if not sig:
        return _failed("lore shows no revision")
    return {"ok": True, "revision": rev, "signature": sig, **extra, "error": ""}


def remote_head(project_root: str, *, timeout: float = 30.0) -> dict:
    """{ok, revision, signature, error}: the head of remote ``main``, one lore call (~1 s)."""
    return _head(project_root, ["--remote", "history", "1", "--branch", REMOTE_BRANCH], timeout)


def local_head(project_root: str, *, timeout: float = 30.0) -> dict:
    """{ok, revision, signature, changes, error}: the island's current revision and its
    working-tree ``(code, rel)`` changes (one ``lore status``, as in :func:`repo_state`)."""
    return _head(project_root, ["status"], timeout)


def repo_state(project_root: str, *, timeout: float = 60.0) -> dict:
    """{ok, available, repository, branch, local_revision:int|None, remote_revision:int|None,
    behind:bool, ahead:bool, in_sync:bool, staged:[rel paths], untracked:[rel paths], error:str}
    (+ local_branch, remote_branch="main", local_signature, remote_signature, merge_base, and
    ``incoming``: the remote revisions the local head lacks, see :func:`incoming`; ``changes``:
    the working-tree ``(code, rel)`` pairs, staged then untracked, deletes included).
    ``branch`` = ``local_branch``; remote and behind/ahead/in_sync are vs remote ``main``.
    ok=False with error when lore.exe is missing, the folder has no .lore, or lore
    fails/times out. ``timeout`` applies to each of the 3 lore calls."""
    available = bool(detect_lore_executable().get("available"))
    if not available:
        return _failed("lore.exe not found — install Fortnite/UEFN or set LORE_CLI_LOCATION", available=False)
    st, err = _state(project_root, timeout)
    if st is None:
        return _failed(err, available=True)
    return {
        "ok": True,
        "available": True,
        **st,
        "branch": st["local_branch"],
        "staged": [rel for _, rel in st["staged"]],
        "untracked": [rel for _, rel in st["untracked"]],
        "error": "",
    }


def _committed(project_root: str, st: dict[str, Any], timeout: float) -> tuple[list[tuple[str, str]] | None, str]:
    """Files this PC's commits changed since the merge base (autosaves main lacks): one
    ``revision diff`` when the local head is ahead, none otherwise."""
    if not st.get("ahead"):
        return [], ""
    ok, out = _run_lore(project_root, ["revision", "diff", st["merge_base"], "--target", st["local_signature"]], timeout)
    return (parse_revision_diff(out), "") if ok else (None, out)


def pending_paths(project_root: str, *, timeout: float = 60.0) -> dict:
    """{ok, paths:[absolute file paths, forward slashes], error} — every file not yet on remote
    ``main``: working-tree changes (staged + untracked) plus files changed between the merge base
    of (local head, remote main head) and the local head (autosave commits, pushed to a personal
    branch or not). Only files that exist on disk are listed: deletes are dropped, untracked
    directories expand to their files. Content/Developers/** and the ``~base`` / ``~mine`` /
    ``~theirs`` copies Lore leaves beside a conflicted merge are excluded.
    ``timeout`` applies to each lore call (up to 4)."""
    st, err = _state(project_root, timeout)
    if st is None:
        return _failed(err, paths=[])
    committed, err = _committed(project_root, st, timeout)
    if committed is None:
        return _failed(err, paths=[])
    try:
        paths = to_pending_files(project_root, [*st["changes"], *committed])
    except (OSError, ValueError) as exc:
        return _failed(f"listing pending files failed: {exc}", paths=[])
    return {"ok": True, "paths": paths, "error": ""}


def conflict_marked(paths: list[str]) -> list[str]:
    """The text files among ``paths`` that still hold merge conflict markers (a line starting
    ``<<<<<<< `` and one starting ``>>>>>>> ``, as Lore writes them). Reads the first
    :data:`_MARKER_MAX_BYTES` of each; unreadable files are skipped. Never raises."""
    out: list[str] = []
    for path in paths:
        if Path(path).suffix.lower() not in _TEXT_EXTS:
            continue
        try:
            with open(path, "rb") as fh:
                text = fh.read(_MARKER_MAX_BYTES).decode("utf-8", errors="replace")
        except OSError:
            continue
        if _MARKER_OPEN_RE.search(text) and _MARKER_CLOSE_RE.search(text):
            out.append(path)
    return out


def whoami(project_root: str, *, timeout: float = 30.0) -> dict[str, str]:
    """{id, name}: the Epic account lore is signed in with on this PC (``lore auth info``, local,
    ~0.3 s), cached :data:`_ME_TTL_S`. Empty strings when lore can't say. Never raises."""
    now = time.monotonic()
    with _me_lock:
        hit = _me_cache.get(project_root)
        if hit and now - hit[0] < _ME_TTL_S:
            return dict(hit[1])
    ok, out = _run_lore(project_root, ["auth", "info"], timeout)
    me = parse_auth_info(out) if ok else {"id": "", "name": ""}
    if me["name"]:
        with _me_lock:
            _me_cache[project_root] = (now, me)
    return dict(me)


def _same_owner(owner_raw: str, me_raw: str) -> bool:
    if not me_raw:
        return False
    return owner_raw.strip() == me_raw.strip() or display_name(owner_raw) == display_name(me_raw)


def locks(project_root: str, *, timeout: float = 60.0) -> dict:
    """{ok, locks:[{path, rel, owner, owner_raw, mine}], me, error}: the files locked on remote
    ``main`` (UEFN locks a file as soon as an actor is modified, until check-in or revert), one
    ``lore lock query --branch main`` (~1.1 s) plus a cached ``lore auth info``.

    ``path`` is absolute with forward slashes, ``owner`` the display name without padding.
    ``mine``: the lock owner is the account lore is signed in with here (:func:`whoami`,
    compared by display name: lore prints no owner id). When that account is unknown, ok=False
    (locks still listed, all ``mine=False``) so nobody mistakes this PC's own locks for another's.
    Never raises."""
    try:
        if not (Path(project_root) / ".lore").is_dir():
            return _failed("not a Lore repository (no .lore folder)", locks=[], me="")
        ok, out = _run_lore(project_root, ["lock", "query", "--branch", REMOTE_BRANCH], timeout)
        if not ok:
            return _failed(out, locks=[], me="")
        me_raw = whoami(project_root, timeout=min(timeout, 30.0))["name"]
        root = Path(project_root)
        rows = [
            {
                "path": (root / lk["rel"]).as_posix(),
                "rel": lk["rel"],
                "owner": display_name(lk["owner_raw"]),
                "owner_raw": lk["owner_raw"],
                "mine": _same_owner(lk["owner_raw"], me_raw),
            }
            for lk in parse_locks(out)
        ]
        me = display_name(me_raw)
        if not me:
            return _failed("lore auth info names no signed-in account", locks=rows, me="")
        return {"ok": True, "locks": rows, "me": me, "error": ""}
    except Exception as exc:  # noqa: BLE001 — never raises
        return _failed(f"lock query failed: {exc}", locks=[], me="")


def incoming_paths(project_root: str, *, state: dict | None = None, timeout: float = 60.0) -> dict:
    """{ok, files:[{rel, path, code, deleted}], error}: the files remote ``main`` changed since
    the merge base (what a pull brings), deletions included (``code`` "D", ``deleted``).
    ``state`` is a :func:`repo_state` result (read when not given: 3 calls); on top of it, one
    ``revision diff`` (~1 s), none when the island isn't behind. Never raises."""
    try:
        st = state or repo_state(project_root, timeout=timeout)
        if not st.get("ok"):
            return _failed(str(st.get("error") or "could not read the repository"), files=[])
        if not st.get("behind"):
            return {"ok": True, "files": [], "error": ""}
        args = ["revision", "diff", st["merge_base"], "--target", st["remote_signature"]]
        ok, out = _run_lore(project_root, args, timeout)
        if not ok:
            return _failed(out, files=[])
        root = Path(project_root)
        files = [
            {"rel": rel, "path": (root / rel).as_posix(), "code": code, "deleted": code == "D"}
            for code, rel in _files_only(project_root, parse_revision_diff(out))
        ]
        return {"ok": True, "files": files, "error": ""}
    except Exception as exc:  # noqa: BLE001 — never raises
        return _failed(f"listing incoming files failed: {exc}", files=[])


def overlap(project_root: str, *, state: dict | None = None, timeout: float = 60.0) -> dict:
    """{ok, files:[{rel, path, local, remote}], error}: files a pull would bring (remote ``main``
    changed them, :func:`incoming_paths`) that this PC changed too and main lacks: working-tree
    changes (deletes and untracked included) and this PC's commits since the merge base.
    ``local`` is "added" / "modified" / "deleted" ("changed" for another lore code), ``remote``
    the diff code ("A", "M", "D"). A file deleted on both sides isn't listed.
    Cost on top of ``state`` (a :func:`repo_state` result, read when not given): nothing when
    the island isn't behind, else 1 ``revision diff``, +1 when this PC has commits main lacks.
    Never raises."""
    try:
        st = state or repo_state(project_root, timeout=timeout)
        inc = incoming_paths(project_root, state=st, timeout=timeout)
        if not inc["ok"] or not inc["files"]:
            return {"ok": inc["ok"], "files": [], "error": inc["error"]}
        committed, err = _committed(project_root, st, timeout)
        if committed is None:
            return _failed(err, files=[])
        mine: dict[str, str] = {}
        folders: list[tuple[str, str]] = []
        # Working-tree lines last: they are the newest state of a file.
        for code, rel in _files_only(project_root, [*committed, *(st.get("changes") or [])]):
            if rel.endswith("/"):
                folders.append((code, rel.lower()))
            else:
                mine[rel.lower()] = code
        files: list[dict[str, str]] = []
        for f in inc["files"]:
            low = f["rel"].lower()
            code = mine.get(low) or next((c for c, d in folders if low.startswith(d)), "")
            if not code or (code == "D" and f["deleted"]):
                continue
            files.append({"rel": f["rel"], "path": f["path"], "local": _CHANGE_WORDS.get(code, "changed"),
                          "remote": f["code"]})
        return {"ok": True, "files": files, "error": ""}
    except Exception as exc:  # noqa: BLE001 — never raises
        return _failed(f"comparing changes failed: {exc}", files=[])
