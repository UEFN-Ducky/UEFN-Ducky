"""Observe outside-agent edits without taking ownership of their filesystem writes."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from backend.workspace.identity import RunContext
from backend.workspace.journal import SOURCE_UNKNOWN, FileChangeJournal
from backend.workspace.paths import content_hash, line_delta
from backend.workspace.policy import ALLOW
from backend.workspace.writer import WriteRecord

log = logging.getLogger(__name__)
MAX_BYTES = 2 * 1024 * 1024
_PATH_KEYS = {"path", "paths", "file_path", "filePath", "target_file", "relative_path",
              "cwd", "workdir", "directory", "root"}
_FOLDER_KEYS = {"cwd", "workdir", "directory", "root"}
_SHELL_KEYS = ("command", "cmd", "script")
# Each agent's own file tools (Codex, Claude Code, Cursor): they write files by definition.
_NATIVE_EDIT_TOOLS = {"file_change", "apply_patch", "Edit", "Write", "MultiEdit", "NotebookEdit",
                      "edit", "write", "edit_file", "write_file", "delete_file"}


@dataclass(frozen=True)
class Snapshot:
    text: str = ""
    exists: bool = False

    @cached_property
    def digest(self) -> str:
        return content_hash(self.text) if self.exists else ""


def _decode(data: bytes) -> Snapshot | None:
    if len(data) > MAX_BYTES or b"\x00" in data:
        return None
    try:
        return Snapshot(data.decode("utf-8"), True)
    except UnicodeDecodeError:
        return None


def _read(path: Path) -> Snapshot | None:
    try:
        if path.is_symlink() or path.stat().st_size > MAX_BYTES:
            return None
        with path.open("rb") as stream:
            return _decode(stream.read(MAX_BYTES + 1))
    except FileNotFoundError:
        return Snapshot()
    except OSError:
        return None


def _git(root: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *args],
        capture_output=True, timeout=15, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return proc.stdout


def _dirty(root: Path) -> set[str]:
    parts = iter(_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0"))
    paths: set[str] = set()
    for part in parts:
        if not part:
            continue
        paths.add(os.fsdecode(part[3:]))
        if b"R" in part[:2] or b"C" in part[:2]:
            paths.add(os.fsdecode(next(parts)))
    return paths


def _ignored(root: Path, names: set[str]) -> set[str]:
    if not names:
        return set()
    proc = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--stdin", "-z"],
        input=b"\0".join(os.fsencode(p) for p in names) + b"\0",
        capture_output=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {os.fsdecode(p) for p in proc.stdout.split(b"\0") if p}


class _Repository:
    def __init__(self, root: Path) -> None:
        self.root = root
        try:
            self.head = _git(root, "rev-parse", "HEAD").decode().strip()
        except subprocess.CalledProcessError:
            self.head = ""
        self.previous = {name: _read(root / name) for name in _dirty(root)}

    def baseline(self, name: str) -> Snapshot | None:
        if name in self.previous:
            return self.previous[name]
        if not self.head:
            return Snapshot()
        try:
            # Checkout filters preserve CRLF just as the clean working tree did.
            size = int(_git(self.root, "cat-file", "-s", f"{self.head}:{name}"))
            if size > MAX_BYTES:
                return None
            return _decode(_git(self.root, "cat-file", "--filters", f"{self.head}:{name}"))
        except subprocess.CalledProcessError:
            return Snapshot()

    def changes(self) -> list[tuple[Path, Snapshot, Snapshot]]:
        names = _dirty(self.root) | self.previous.keys()
        if self.head:
            # A shell tool may edit AND commit; status alone would miss it.
            names |= {os.fsdecode(p) for p in _git(
                self.root, "diff", "--name-only", "-z", self.head, "HEAD"
            ).split(b"\0") if p}
        names -= _ignored(self.root, set(names))
        edits = []
        for name in sorted(names):
            path = self.root / name
            if not path.resolve().is_relative_to(self.root) or ".git" in Path(name).parts:
                continue
            before, after = self.baseline(name), _read(path)
            if before is not None and after is not None and before.digest != after.digest:
                edits.append((path, before, after))
            self.previous[name] = after
        return edits


# A folder outside git has no history to diff against, and Codex reports an apply_patch
# edit only after it landed, so a file first seen then had no "before" (no card, no
# Changes entry). Its text files are snapshotted when the turn starts instead.
# Hidden folders (".git", ".ducky", ".venv" …) are never walked either: Oct 21 2026 a UEFN
# island's ".lore/mutable" (Lore's store, ~20,000 binary files) filled the file cap and every
# Codex turn opened each of them, 13 s before launch and 0.5 s again after every tool call.
_FOLDER_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", "venv",
    "Intermediate", "Saved", "DerivedDataCache", "Binaries", "__ExternalActors__", "__ExternalObjects__",
})
_BINARY_SUFFIXES = frozenset({
    ".uasset", ".umap", ".ubulk", ".uexp", ".upk", ".pak", ".utoc", ".ucas",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tga", ".dds", ".exr", ".hdr", ".psd", ".ico", ".webp",
    ".wav", ".mp3", ".ogg", ".flac", ".mp4", ".mov", ".avi", ".webm",
    ".fbx", ".obj", ".glb", ".blend", ".abc", ".zip", ".7z", ".rar", ".gz",
    ".exe", ".dll", ".pdb", ".lib", ".so", ".dylib", ".ttf", ".otf", ".woff", ".woff2",
    ".bin", ".db", ".sqlite", ".pyc",
})
FOLDER_MAX_FILES = 20_000
FOLDER_MAX_TEXT_BYTES = 64 * 1024 * 1024


class _Folder:
    """Text files of a folder outside git, as they were when the turn started."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.stats: dict[str, tuple[int, int]] = {}
        self.texts: dict[str, Snapshot] = {}
        budget = FOLDER_MAX_TEXT_BYTES
        for rel, stat in self._walk():
            self.stats[rel] = stat
            if stat[1] <= MAX_BYTES and stat[1] <= budget:
                snapshot = _read(root / rel)
                if snapshot is not None:
                    self.texts[rel] = snapshot
                    budget -= stat[1]

    def _walk(self):
        count = 0
        stack = [self.root]
        while stack:
            try:
                entries = list(os.scandir(stack.pop()))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in _FOLDER_SKIP_DIRS and not entry.name.startswith("."):
                            stack.append(Path(entry.path))
                        continue
                    if Path(entry.name).suffix.lower() in _BINARY_SUFFIXES:
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                count += 1
                if count > FOLDER_MAX_FILES:
                    return
                yield Path(entry.path).relative_to(self.root).as_posix(), (stat.st_mtime_ns, stat.st_size)

    def changes(self) -> list[tuple[Path, Snapshot, Snapshot]]:
        now = dict(self._walk())
        edits = []
        for rel in sorted(set(now) | set(self.stats)):
            old, new = self.stats.get(rel), now.get(rel)
            if old == new:
                continue
            path = self.root / rel
            before = self.texts.get(rel, Snapshot() if old is None else None)
            after = _read(path) if new is not None else Snapshot()
            if new is None:
                self.stats.pop(rel, None)
                self.texts.pop(rel, None)
            else:
                self.stats[rel] = new
                if after is not None:
                    self.texts[rel] = after
            if before is not None and after is not None and before.digest != after.digest:
                edits.append((path, before, after))
        return edits


def _folder_worth_snapshotting(path: Path) -> bool:
    """A project folder, not a drive root or the whole user profile."""
    try:
        home = Path.home().resolve()
    except OSError:
        home = None
    return path.is_dir() and path.parent != path and path != home


def _ledger_roots() -> list[Path]:
    """Every Ducky project root (each has its own change ledger)."""
    try:
        from backend.workspace.lanes import project_roots

        return [Path(r).resolve() for r in project_roots()]
    except Exception:  # noqa: BLE001 - no project list: only the repo's own ledger is read
        return []


def _named_paths(value: Any, key: str = ""):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _named_paths(child, child_key)
    elif isinstance(value, list):
        for child in value:
            yield from _named_paths(child, key)
    elif isinstance(value, str):
        if key in _PATH_KEYS:
            yield value
        elif key in {"patch", "input"}:
            yield from re.findall(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$", value, re.M)
        elif key in {"command", "cmd", "script"}:
            # Discover quoted paths (including Windows spaces), absolute tokens,
            # and a literal `cd`/`Set-Location` without executing any command.
            for match in re.finditer(r"['\"]([^'\"\r\n]+)['\"]|((?:[A-Za-z]:[\\/]|/)[^\s;'\"]+)", value):
                candidate = match.group(1) or match.group(2)
                if Path(candidate).is_absolute() or (
                    Path(candidate).suffix and not re.search(r"[;(){}=]", candidate)
                ):
                    yield candidate
            # Plain relative filenames in commands (e.g. Set-Content note.txt).
            yield from re.findall(r"(?<![\w/\\])([\w./\\-]+\.[\w-]+)(?=\s|$)", value)
            yield from re.findall(r"(?:^|[;&]\s*)(?:cd|Set-Location)\s+([^\s;'\"]+)", value)


class TurnFileChanges:
    """Per-turn baselines, tool-row metadata, and the existing Changes journal."""

    def __init__(self, cwd: str, ctx: RunContext, journal: FileChangeJournal) -> None:
        self.cwd = Path(cwd).resolve()
        self.ctx, self.journal = ctx, journal
        self.repos: dict[Path, _Repository] = {}
        self.folders: dict[Path, _Folder] = {}
        self.files: dict[Path, Snapshot | None] = {}
        self.roots: set[str] = set()
        self.completed: list[dict[str, Any]] = []
        self.started = time.time()
        # Per running shell call: digest of each changed repo file when the call started.
        self._pre_call: dict[str, dict[str, str]] = {}
        self._lock = threading.RLock()
        self.discover({"cwd": str(self.cwd)})
        if not any(self.cwd.is_relative_to(root) for root in self.repos) and _folder_worth_snapshotting(self.cwd):
            self.folders[self.cwd] = _Folder(self.cwd)

    def discover(self, arguments: dict[str, Any]) -> None:
        base = Path(str(arguments.get("cwd") or arguments.get("workdir") or self.cwd))
        if not base.is_absolute():
            base = self.cwd / base
        for name in _named_paths(arguments):
            path = Path(name)
            if not path.is_absolute():
                path = base / path
            if path.is_symlink():
                continue
            path = path.resolve()
            if any(path.is_relative_to(folder) for folder in self.folders):
                continue  # the turn-start folder snapshot already covers it
            # Keep nested repositories distinct from an already discovered parent.
            if any(path.is_relative_to(root) and not any(
                (parent / ".git").exists() for parent in (path, *path.parents)
                if parent != root and parent.is_relative_to(root)
            ) for root in self.repos):
                continue
            parent = path if path.is_dir() else path.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            try:
                root = Path(os.fsdecode(_git(parent, "rev-parse", "--show-toplevel")).strip()).resolve()
            except (OSError, subprocess.SubprocessError):
                if not path.is_dir() and path not in self.files:
                    self.files[path] = _read(path)
                continue
            if root not in self.repos:
                self.repos[root] = _Repository(root)

    def _absolute(self, arguments: Any) -> list[Path]:
        """The files a tool call names (no folder keys), absolute."""
        if not isinstance(arguments, dict):
            return []
        base = Path(str(arguments.get("cwd") or arguments.get("workdir") or self.cwd))
        if not base.is_absolute():
            base = self.cwd / base
        names = _named_paths({k: v for k, v in arguments.items() if k not in _FOLDER_KEYS})
        return [Path(n) if Path(n).is_absolute() else base / n for n in names]

    def _note_team_folders(self, paths: list[Path]) -> None:
        """An outside agent's edit in a plugin's shared folder goes to the team as the AI's
        (its ducky's name), not as the person's own (team_folders.note_ai_edit)."""
        if not paths:
            return
        try:
            from backend.uefn_plugins.team_folders import note_ai_edit

            note_ai_edit(paths, ducky=self.ctx.ducky_name)
        except Exception:  # noqa: BLE001 — attribution never fails a turn
            log.debug("noting an AI edit in a team folder failed", exc_info=True)

    def _reach(self, tool_name: str, arguments: Any):
        """Which changed files one tool call may claim as its own edits.

        A shell command can write anything. An edit tool (Codex file_change, Claude
        Edit/Write, Ducky's workspace_* tools) claims only the files it names; a folder
        it names (cwd, a listed directory) claims nothing. Any other call claims nothing.
        """
        if not isinstance(arguments, dict):
            arguments = {}
        if any(isinstance(arguments.get(key), str) and arguments[key].strip() for key in _SHELL_KEYS):
            return lambda _path: True
        base = Path(str(arguments.get("cwd") or arguments.get("workdir") or self.cwd))
        if not base.is_absolute():
            base = self.cwd / base
        named = set()
        for name in _named_paths({k: v for k, v in arguments.items() if k not in _FOLDER_KEYS}):
            path = Path(name)
            named.add(os.path.normcase(str((path if path.is_absolute() else base / path).resolve())))
        if not named and tool_name in _NATIVE_EDIT_TOOLS:
            return lambda _path: True  # the agent's own edit tool, reported without its paths
        return lambda path: os.path.normcase(str(Path(path).resolve())) in named

    def _theirs(self, root: Path, path: Path, after: Snapshot) -> bool:
        """Another run's journaled write (a parallel chat's workspace_* edit) left this exact
        content: a shell call that ran meanwhile must not show or journal it as its own."""
        stamp = self.journal.index_stamp(path.relative_to(root).as_posix(), project_root=str(root)) or {}
        return bool(stamp.get("run_id")) and stamp.get("run_id") != self.ctx.run_id \
            and stamp.get("hash", "") == after.digest

    @staticmethod
    def _is_shell(arguments: Any) -> bool:
        return isinstance(arguments, dict) and any(
            isinstance(arguments.get(key), str) and arguments[key].strip() for key in _SHELL_KEYS)

    @staticmethod
    def _call_key(tool: dict[str, Any]) -> str:
        return str(tool.get("id") or tool.get("name") or "")

    def _changed_now(self) -> dict[str, str]:
        """Digest of every changed file in the turn's repos right now."""
        out: dict[str, str] = {}
        for root in self.repos:
            try:
                names = _dirty(root)
            except (OSError, subprocess.SubprocessError):
                continue
            for name in names:
                snap = _read(root / name)
                if snap is not None:
                    out[os.path.normcase(str((root / name).resolve()))] = snap.digest
        return out

    def _not_this_call(self, root: Path, path: Path, after: Snapshot, pre: dict[str, str]) -> bool:
        """A shell call saw this change but nothing ties it to this chat.

        Only for a file with no stamp in its own ledger: it was already like this when the
        call started, or another chat's row for it sits in an enclosing project's ledger
        (Nov 3 2026: the Guards seat's writes, journaled under Documents/GitHub, were taken
        by the Chats seat's next ducky_terminal_run in the repo).
        """
        stamp = self.journal.index_stamp(path.relative_to(root).as_posix(), project_root=str(root)) or {}
        if stamp.get("run_id") or stamp.get("conv_id") or (stamp and stamp.get("source") != SOURCE_UNKNOWN):
            return False
        if pre.get(os.path.normcase(str(path.resolve()))) == after.digest:
            return True
        own = root.resolve()
        for other in _ledger_roots():
            if other == own or not path.resolve().is_relative_to(other):
                continue
            try:
                theirs = self.journal.index_stamp(path.resolve().relative_to(other).as_posix(),
                                                  project_root=str(other)) or {}
            except Exception:  # noqa: BLE001 - that project has no ledger
                continue
            who = str(theirs.get("conv_id") or "")
            if who and who != self.ctx.conv_id and not theirs.get("deleted") and (
                    theirs.get("hash") == after.digest or float(theirs.get("ts") or 0) >= self.started):
                return True
        return False

    def _record_unknown(self, root: Path, path: Path, before: Snapshot, after: Snapshot, tool: str) -> None:
        """Move the last-writer stamp to nobody: no chat is credited, nobody is refused."""
        added, removed = line_delta(before.text, after.text)
        writer = {"source": SOURCE_UNKNOWN, "run_id": "", "conv_id": "", "ducky_name": "", "tool": tool}
        self.journal.record(WriteRecord(
            op="write" if after.exists else "delete", path=path.relative_to(root).as_posix(), from_path="",
            before=before.text, after=after.text, before_hash=before.digest,
            after_hash=after.digest, existed_before=before.exists, tool=tool,
            writer=writer, ctx=None, ts=time.time(),
            lines_added=added, lines_removed=removed, decision=ALLOW,
            project_root=str(root), abs_path=str(path),
        ))

    def _record(self, root: Path, path: Path, before: Snapshot, after: Snapshot, tool: str) -> dict[str, Any]:
        relative = path.relative_to(root).as_posix()
        added, removed = line_delta(before.text, after.text)
        stamp = self.journal.index_stamp(relative, project_root=str(root)) or {}
        # MCP writes already went through ProjectWriter. Don't journal them twice.
        if stamp.get("run_id") != self.ctx.run_id or stamp.get("hash") != after.digest:
            self.journal.record(WriteRecord(
                op="write" if after.exists else "delete", path=relative, from_path="",
                before=before.text, after=after.text, before_hash=before.digest,
                after_hash=after.digest, existed_before=before.exists, tool=tool,
                writer=self.ctx.as_writer(tool=tool), ctx=self.ctx, ts=time.time(),
                lines_added=added, lines_removed=removed, decision=ALLOW,
                project_root=str(root), abs_path=str(path),
            ))
        self.roots.add(str(root))
        self._note_team_folders([path])
        # An absolute editor key works even when this repo is not the open project.
        return {"path": "abs:" + path.as_posix(), "before": before.text, "after": after.text,
                "linesAdded": added, "linesRemoved": removed,
                "kind": "create" if not before.exists else "write"}

    def process(self, event: dict[str, Any]) -> None:
        if event.get("type") not in {"tool", "tool_done"}:
            return
        tool = event.get("tool")
        if not isinstance(tool, dict):
            return
        with self._lock:
            self.discover(tool.get("arguments") or {})
            if event["type"] != "tool_done":
                # Before an edit tool runs: a file it names in a plugin's team folder is the
                # AI's change (a read names files too, and changes nothing).
                if str(tool.get("name") or "") in _NATIVE_EDIT_TOOLS:
                    self._note_team_folders(self._absolute(tool.get("arguments") or {}))
                elif self._is_shell(tool.get("arguments")):
                    self._pre_call[self._call_key(tool)] = self._changed_now()
                return
            edits = []
            # Snapshots always advance; only what this call could have written is its edit.
            # Another agent working in the same folder at the same time was credited to
            # whatever call ended next (a plan tick "created" the other writer's file).
            # Nov 2 2026: a 9-minute ducky_terminal_run in one chat showed four .verse diffs
            # that a second chat wrote in the same project meanwhile.
            reach = self._reach(str(tool.get("name") or ""), tool.get("arguments") or {})
            owns = lambda path, root, after: reach(path) and not self._theirs(root, path, after)  # noqa: E731
            shell = self._is_shell(tool.get("arguments"))
            pre = self._pre_call.pop(self._call_key(tool), {})
            name = str(tool.get("name") or "tool")
            for root, repo in self.repos.items():
                for path, before, after in repo.changes():
                    if not owns(path, root, after):
                        continue
                    if shell and self._not_this_call(root, path, after, pre):
                        self._record_unknown(root, path, before, after, name)
                        continue
                    edits.append(self._record(root, path, before, after, name))
            for root, folder in self.folders.items():
                for path, before, after in folder.changes():
                    if owns(path, root, after):
                        edits.append(self._record(root, path, before, after, str(tool.get("name") or "tool")))
            for path, before in self.files.items():
                after = _read(path)
                if before is not None and after is not None and before.digest != after.digest and owns(path, path.parent, after):
                    edits.append(self._record(path.parent, path, before, after, str(tool.get("name") or "tool")))
                self.files[path] = after
            # These snapshots are authoritative, including no-change and skip cases.
            tool.pop("fileEdit", None)
            tool["fileEdits"] = edits
            if edits:
                tool["fileEdit"] = edits[0]
            self.completed.append({"name": tool.get("name"), "id": tool.get("id"), "edits": edits})

    def wrap(self, push):
        def wrapped(event):
            try:
                self.process(event)
            except Exception:
                log.warning("Could not capture coding-agent file changes", exc_info=True)
            push(event)
        return wrapped

    def enrich_blocks(self, blocks: list[dict[str, Any]]) -> None:
        pending = list(self.completed)
        for block in blocks:
            if block.get("type") != "tool_call":
                continue
            match = next((row for row in pending if (
                row["id"] == block.get("id") if row["id"] else row["name"] == block.get("name")
            )), None)
            if match is None:
                continue
            pending.remove(match)
            block.pop("file_edit", None)
            block["file_edits"] = match["edits"]
            if match["edits"]:
                block["file_edit"] = match["edits"][0]

    def close(self, status: str) -> None:
        for root in self.roots:
            try:
                self.journal.end_run(self.ctx.run_id, status, project_root=root)
            except Exception:
                log.warning("Could not close coding-agent Changes run for %s", root, exc_info=True)
