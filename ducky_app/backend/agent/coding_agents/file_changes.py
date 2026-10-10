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
from backend.workspace.journal import FileChangeJournal
from backend.workspace.paths import content_hash, line_delta
from backend.workspace.policy import ALLOW
from backend.workspace.writer import WriteRecord

log = logging.getLogger(__name__)
MAX_BYTES = 2 * 1024 * 1024
_PATH_KEYS = {"path", "paths", "file_path", "filePath", "target_file", "relative_path",
              "cwd", "workdir", "directory", "root"}


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
        self.files: dict[Path, Snapshot | None] = {}
        self.roots: set[str] = set()
        self.completed: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self.discover({"cwd": str(self.cwd)})

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
                return
            edits = []
            for root, repo in self.repos.items():
                for path, before, after in repo.changes():
                    edits.append(self._record(root, path, before, after, str(tool.get("name") or "tool")))
            for path, before in self.files.items():
                after = _read(path)
                if before is not None and after is not None and before.digest != after.digest:
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
