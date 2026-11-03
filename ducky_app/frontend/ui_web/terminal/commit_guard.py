"""An agent's git commit never takes another live chat's files.

Nov 3 2026: chat 236c4da0 committed "Team tab: ..." in uefn-plugin-account and swept in
another chat's new backend/model3d.py and storage_save.py and its edits to three more files.
Before an agent's command with ``git commit`` runs, the files it would commit (staged ones,
plus what a ``git add`` or ``commit -a`` in the same command takes) are looked up in the
change journal's last-writer index; a file another chat wrote, while that chat is live (not
archived or deleted), refuses the command with the files and the chat named.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import subprocess
from typing import Callable

# Ends the refusal: the agent means to commit the other chat's work (a lead integrating).
OVERRIDE = "ducky:commit-others"
_SPLIT = re.compile(r"&&|\|\||[;|\n]")


def _git(cwd: str, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10.0,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001 - no git, no guard
        return ""
    return out.stdout if out.returncode == 0 else ""


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _git_call(words: list[str], cwd: str) -> tuple[str, list[str], str]:
    """(subcommand, its args, the folder it runs in) for a ``git ...`` segment; ('', [], cwd) otherwise."""
    if not words or os.path.basename(words[0]).lower() not in ("git", "git.exe"):
        return "", [], cwd
    rest = words[1:]
    while rest[:1] == ["-C"] and len(rest) > 1:
        cwd = os.path.join(cwd, rest[1])
        rest = rest[2:]
    return (rest[0], rest[1:], cwd) if rest else ("", [], cwd)


def to_commit(command: str, cwd: str) -> tuple[str, list[str]]:
    """(repo top, files relative to it) the command's ``git commit`` would take; ('', []) when it
    has none. Counts what is staged now and what its own ``git add`` / ``commit -a`` adds."""
    commits, adds, everything, tracked = False, [], False, False  # tracked: -u / commit -a (no new files)
    here = ""
    for segment in _SPLIT.split(command or ""):
        words = _words(segment.strip())
        if words[:1] in (["cd"], ["Set-Location"]) and len(words) > 1:
            cwd = os.path.join(cwd, words[1])
            continue
        sub, args, where = _git_call(words, cwd)
        if sub == "add":
            here = where
            flags = [a for a in args if a.startswith("-")]
            specs = [a for a in args if not a.startswith("-")]
            everything = everything or bool(set(flags) & {"-A", "--all"})
            tracked = tracked or bool(set(flags) & {"-u", "--update"})
            adds += [_key(os.path.join(where, s)) for s in specs]
        elif sub == "commit":
            commits, here = True, where
            if any(a in ("-a", "--all") or re.fullmatch(r"-[a-zA-Z]*a[a-zA-Z]*", a) for a in args):
                tracked = True
    if not commits:
        return "", []
    top = _git(here or cwd, "rev-parse", "--show-toplevel").strip()
    if not top:
        return "", []
    files = {f for f in _git(top, "diff", "--cached", "--name-only", "-z").split("\0") if f}
    if everything or tracked or adds:
        for flag, all_of_them in (("-m", everything or tracked), ("-o", everything)):
            for rel in (f for f in _git(top, "ls-files", flag, "--exclude-standard", "-z").split("\0") if f):
                full = _key(os.path.join(top, rel))
                if all_of_them or any(full == a or full.startswith(a.rstrip("\\/") + os.sep)
                                      or fnmatch.fnmatch(full, a) for a in adds):
                    files.add(rel)
    return top, sorted(files)


def _writer(path: str) -> tuple[str, str]:
    """(chat id, ducky name) of the last journaled writer of *path*, ('', '') when unknown."""
    from backend.workspace.lanes import project_roots
    from backend.workspace.runtime import get_writer

    full = path.replace("\\", "/")
    roots = [r for r in project_roots() if full.lower().startswith(r.lower().rstrip("/") + "/")]
    stamp_of = getattr(get_writer().journal, "index_stamp", None)
    if not roots or stamp_of is None:
        return "", ""
    root = max(roots, key=len)
    try:
        stamp = stamp_of(full[len(root.rstrip("/")) + 1:], project_root=root) or {}
    except Exception:  # noqa: BLE001 - no ledger for that project
        return "", ""
    if stamp.get("deleted"):
        return "", ""
    return str(stamp.get("conv_id") or ""), str(stamp.get("ducky_name") or "")


def _live(conv_id: str) -> bool:
    """The chat is running, or exists and isn't archived."""
    from frontend.archive_folder import is_archive_folder_id
    from frontend.ui_web.agent_modes import is_agent_running
    from frontend.ui_web.project_chats import load_conversation

    if is_agent_running(conv_id):
        return True
    conv = load_conversation(conv_id)
    return conv is not None and not is_archive_folder_id(str(getattr(conv, "folder_id", "") or ""))


def check(command: str, cwd: str, conv_id: str, *,
          writer: Callable[[str], tuple[str, str]] = _writer, live: Callable[[str], bool] = _live) -> str:
    """'' when the command may run, else why not (the other chats' files, and what to do)."""
    if not conv_id or OVERRIDE in (command or "") or "commit" not in (command or ""):
        return ""
    top, files = to_commit(command, cwd)
    theirs: list[str] = []
    for rel in files:
        try:
            who, name = writer(os.path.join(top, rel))
        except Exception:  # noqa: BLE001 - attribution is best effort
            continue
        if who and who != conv_id and live(who):
            theirs.append(f"{rel} ({name or 'another chat'}, chat {who[:8]})")
    if not theirs:
        return ""
    more = f" and {len(theirs) - 8} more" if len(theirs) > 8 else ""
    return ("Not run: this commit would take files another live chat wrote: " + "; ".join(theirs[:8]) + more
            + ". Stage only your own files by name (git add <file>; never git add -A, git add . or commit -a), "
            "unstage theirs with git restore --staged <file>, and commit again. If you really mean to commit "
            f"their work, add the comment `# {OVERRIDE}` to the command.")
