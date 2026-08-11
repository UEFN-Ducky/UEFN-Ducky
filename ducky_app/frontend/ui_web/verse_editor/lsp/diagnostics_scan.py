"""Batch verse-lsp scan for all project .verse files — pushes verse_diagnostics_sync to UI."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from frontend.ui_web.verse_editor.lsp import diagnostics_cache
from frontend.ui_web.verse_editor.lsp.project_root import normalize_verse_lsp_project_root
from frontend.ui_web.verse_editor.lsp.stdio_session import LspStdioSession, to_monaco_file_uri
from frontend.ui_web.verse_editor.lsp.verse_workspace import discover_verse_workspace

_active_scan: LspStdioSession | None = None
_active_root = ""
_abort_epoch = 0
_scan_seq = 0
_state_lock = threading.Lock()
# One ephemeral scan at a time; concurrent requests queue here instead of killing
# the in-flight scan (design rule: never drop a scheduled check).
_scan_serialize = threading.Lock()

ProgressFn = Callable[..., None]


def allocate_scan_id() -> int:
    """Monotonic id tagged onto every progress event so the UI can drop stale scans."""
    global _scan_seq
    with _state_lock:
        _scan_seq += 1
        return _scan_seq


def is_scan_active(project_root: str = "") -> bool:
    root = normalize_verse_lsp_project_root(project_root) if project_root else ""
    with _state_lock:
        if _active_scan is None:
            return False
        return not root or _active_root == root


def abort_active_scan() -> None:
    """Stop the in-flight ephemeral scan and cancel any queued ones (Stop button, project switch)."""
    global _active_scan, _active_root, _abort_epoch
    with _state_lock:
        session = _active_scan
        _active_scan = None
        _active_root = ""
        _abort_epoch += 1
    if session is not None:
        try:
            session.stop()
        except Exception:
            pass


def _scan_aborted(session: LspStdioSession) -> bool:
    """True when abort_active_scan() replaced/stopped this session."""
    with _state_lock:
        return _active_scan is not session


def _norm_rel(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("/")


def _registry_key(path: str) -> str:
    return _norm_rel(path).lower()


def _count_severities(diagnostics: list[dict[str, Any]]) -> tuple[int, int]:
    errors = 0
    warnings = 0
    for d in diagnostics:
        sev = d.get("severity")
        if sev == 2:
            warnings += 1
        elif sev in (3, 4):
            continue
        else:
            errors += 1
    return errors, warnings


def _diagnostics_to_items(diags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # verse-lsp often emits the same Unknown-identifier twice on one line (type + ctor).
    best: dict[tuple[str, int, str], dict[str, Any]] = {}
    for d in diags:
        rng = d.get("range") or {}
        start = rng.get("start") or {}
        sev = d.get("severity")
        kind = "warning" if sev == 2 else "error" if sev not in (3, 4) else "info"
        if kind == "info":
            continue
        line = int(start.get("line", 0)) + 1
        column = int(start.get("character", 0)) + 1
        message = str(d.get("message") or "")
        key = (kind, line, message)
        prev = best.get(key)
        if prev is None or column < int(prev["column"]):
            best[key] = {
                "line": line,
                "column": column,
                "message": message,
                "severity": kind,
            }
    return sorted(best.values(), key=lambda i: (i["line"], i["column"]))


def _collect_verse_files(project_root: str) -> list[tuple[str, str]]:
    content = Path(project_root) / "Content"
    if not content.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(content.rglob("*.verse")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(Path(project_root))).replace("\\", "/")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        out.append((rel, text))
    return out


def _wait_for_diagnostics(
    session: LspStdioSession,
    *,
    min_count: int = 1,
    max_s: float = 3.0,
    stable_s: float = 0.35,
) -> None:
    """Short wait for publishDiagnostics — avoids blocking on $/progress end."""
    deadline = time.monotonic() + max_s
    stable_since: float | None = None
    last_count = -1
    while time.monotonic() < deadline:
        count = session.diagnostic_uri_count()
        if count >= min_count:
            if count == last_count:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= stable_s:
                    return
            else:
                stable_since = None
        last_count = count
        time.sleep(0.08)


def _refresh_document_diagnostics(
    session: LspStdioSession,
    doc_uri: str,
    content: str,
    *,
    settle_s: float = 0.35,
) -> list[dict[str, Any]] | None:
    """Re-analyze one doc after batch didOpen.

    verse-lsp often publishes a false empty diagnostic set for files opened after
    the first in a batch; a didChange (same text) forces the real parse result.
    """
    session.notify(
        "textDocument/didChange",
        {
            "textDocument": {"uri": doc_uri, "version": 2},
            "contentChanges": [{"text": content}],
        },
    )
    deadline = time.monotonic() + settle_s
    last: list[dict[str, Any]] | None = None
    while time.monotonic() < deadline:
        pub = session.published_diagnostics(doc_uri)
        if pub is not None:
            last = pub
        time.sleep(0.06)
    return last if last is not None else session.published_diagnostics(doc_uri)


def _pull_file_diagnostics(
    session: LspStdioSession,
    doc_uri: str,
    *,
    timeout_s: float = 8.0,
) -> list[dict[str, Any]] | None:
    """Diagnostics for one file, or None when the check FAILED (timeout/transport).

    None must never be recorded as "0 errors" — a wedged verse-lsp was wiping real
    errors from the cache/problems panel while the editor still showed squiggles.
    """
    push_diags = session.diagnostics_for_uri(doc_uri)
    try:
        pull = session.request(
            "textDocument/diagnostic",
            {"textDocument": {"uri": doc_uri}},
            timeout_s=timeout_s,
        )
        if isinstance(pull, dict) and isinstance(pull.get("items"), list):
            diags = [item.get("diagnostics", item) for item in pull["items"] if isinstance(item, dict)]
            flat: list[dict[str, Any]] = []
            for item in diags:
                if isinstance(item, list):
                    flat.extend([d for d in item if isinstance(d, dict)])
                elif isinstance(item, dict):
                    flat.append(item)
            return flat if flat else push_diags
    except Exception:
        # Pull failed — a push may still have delivered results for this file.
        if push_diags:
            return push_diags
        return None
    return push_diags


def list_verse_scan_paths(project_root: str, *, full: bool = True) -> list[str]:
    """Relative registry keys for UI progress (all files or stale-only when incremental)."""
    root = normalize_verse_lsp_project_root(project_root)
    if not root:
        return []
    if full:
        return [_registry_key(rel) for rel, _ in _collect_verse_files(root)]
    return diagnostics_cache.stale_keys(root)


def _cached_file_result(key: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": key,
        "errors": int(entry.get("errors") or 0),
        "warnings": int(entry.get("warnings") or 0),
        "items": list(entry.get("items") or []),
    }


def scan_project_verse_diagnostics(
    project_root: str,
    *,
    full: bool = False,
    quiet_s: float = 2.0,
    wait_s: float = 60.0,
    fast: bool = False,
    on_progress: ProgressFn | None = None,
    scan_id: int | None = None,
    skip_started: bool = False,
) -> dict[str, Any]:
    """Run ephemeral verse-lsp scan; return summary for MCP + UI sync.

    Scans are serialized: a request while another scan runs waits for it, then
    checks only files still stale (per-batch cache persistence makes that cheap).
    Only abort_active_scan() — Stop button or a project-root switch — kills a scan.
    """
    sid = scan_id if scan_id is not None else allocate_scan_id()

    def prog(**kwargs: object) -> None:
        if on_progress is not None:
            on_progress(scan_id=sid, **kwargs)

    root = normalize_verse_lsp_project_root(project_root)
    if not root:
        raise ValueError("No project root set")

    _ = quiet_s, wait_s  # legacy params — batch scan no longer blocks on global progress

    open_batch = 16 if fast else 8
    pull_timeout = 3.0 if fast else 6.0
    batch_settle_s = 1.2 if fast else 2.5
    refresh_settle_s = 0.18 if fast else 0.35

    with _state_lock:
        other_root = _active_root
        epoch = _abort_epoch
    if other_root and other_root != root:
        # Project switched — results of the old-root scan are moot.
        abort_active_scan()
        with _state_lock:
            epoch = _abort_epoch

    with _scan_serialize:
        return _run_scan(
            root,
            full=full,
            open_batch=open_batch,
            pull_timeout=pull_timeout,
            batch_settle_s=batch_settle_s,
            refresh_settle_s=refresh_settle_s,
            epoch=epoch,
            prog=prog,
            skip_started=skip_started,
        )


def _cached_summary(root: str) -> dict[str, Any]:
    cached = diagnostics_cache.files_for_ui(diagnostics_cache.load(root))
    return {
        "files": cached,
        "scanned": 0,
        "with_errors": sum(1 for f in cached if f["errors"] > 0),
        "with_warnings": sum(1 for f in cached if f["warnings"] > 0),
        "aborted": True,
    }


def _run_scan(
    root: str,
    *,
    full: bool,
    open_batch: int,
    pull_timeout: float,
    batch_settle_s: float,
    refresh_settle_s: float,
    epoch: int,
    prog: ProgressFn,
    skip_started: bool = False,
) -> dict[str, Any]:
    with _state_lock:
        superseded = _abort_epoch != epoch
    if superseded:
        # Stopped/switched while queued — drop this scan quietly (no UI events).
        return _cached_summary(root)

    if not diagnostics_cache.cache_enabled():
        full = True

    cache = diagnostics_cache.load(root)
    stale_set = set(diagnostics_cache.stale_keys(root, cache))
    files = _collect_verse_files(root)

    if not full and not stale_set:
        cached = diagnostics_cache.files_for_ui(cache)
        # No work — don't flash the Problems spinner for a no-op.
        with_errors = sum(1 for f in cached if f["errors"] > 0)
        with_warnings = sum(1 for f in cached if f["warnings"] > 0)
        return {
            "files": cached,
            "scanned": 0,
            "with_errors": with_errors,
            "with_warnings": with_warnings,
        }

    scan_keys = [_registry_key(rel) for rel, _ in files] if full else sorted(stale_set)
    # Dedupe registry keys (same basename in different folders is fine — keys are full rel paths).
    seen_keys: set[str] = set()
    deduped_scan_keys: list[str] = []
    for key in scan_keys:
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_scan_keys.append(key)
    scan_keys = deduped_scan_keys

    files_by_key = {_registry_key(rel): (rel, content) for rel, content in files}
    to_scan = [files_by_key[k] for k in scan_keys if k in files_by_key]

    if not to_scan and not full:
        cached = diagnostics_cache.files_for_ui(cache)
        return {
            "files": cached,
            "scanned": 0,
            "with_errors": sum(1 for f in cached if f["errors"] > 0),
            "with_warnings": sum(1 for f in cached if f["warnings"] > 0),
        }

    if not skip_started:
        prog(phase="started", total=len(scan_keys), paths=scan_keys, index=0, path="")
    if not files:
        prog(phase="done", total=0, index=0, path="")
        return {"files": [], "scanned": 0, "with_errors": 0, "with_warnings": 0}

    results: list[dict[str, Any]] = []
    cached_files = cache.get("files") or {}
    if not full:
        # Keep fresh cached results in the payload, but do NOT emit progress for them —
        # progress total is only the files we are actually re-checking.
        for key in sorted(cached_files):
            if key in stale_set:
                continue
            entry = cached_files[key]
            if not isinstance(entry, dict):
                continue
            results.append(_cached_file_result(key, entry))

    ws = discover_verse_workspace(root)
    session = LspStdioSession(root)
    root_path = Path(root)
    # Trust signals for whether this scan may authoritatively REPLACE the registry.
    analyzed = 0  # files verse-lsp actually answered for (empty result = clean is fine)
    failed = 0  # pulls that threw/timed out (server wedged or crashed)
    session_alive = True
    global _active_scan, _active_root
    with _state_lock:
        if _abort_epoch != epoch:
            return _cached_summary(root)
        _active_scan = session
        _active_root = root
    try:
        session.start()
        wf = [{"uri": to_monaco_file_uri(f["path"]), "name": f["name"]} for f in ws["workspace_folders"]]
        session.request(
            "initialize",
            {
                "processId": None,
                "rootUri": wf[0]["uri"] if wf else session.file_uri(""),
                "capabilities": {
                    "workspace": {
                        "workspaceFolders": True,
                        "diagnostics": {"workspaceDiagnostics": True},
                    },
                    "textDocument": {"publishDiagnostics": {"relatedInformation": True}},
                },
                "workspaceFolders": wf,
            },
            timeout_s=20.0,
        )
        session.notify("initialized", {})
        changes = [{"uri": to_monaco_file_uri(p), "type": 1} for p in ws["watch_files"]]
        if changes:
            session.notify("workspace/didChangeWatchedFiles", {"changes": changes})

        uri_to_rel: dict[str, str] = {}
        total_scan = len(to_scan)
        pull_index = 0

        for batch_start in range(0, total_scan, open_batch):
            if _scan_aborted(session) or not session.is_alive():
                break
            batch = to_scan[batch_start : batch_start + open_batch]
            batch_uris: list[tuple[str, str, str, str]] = []

            for rel, content in batch:
                if _scan_aborted(session):
                    break
                key = _registry_key(rel)
                global_open = batch_start + len(batch_uris) + 1
                prog(phase="opening", index=global_open, total=total_scan, path=key)
                doc_uri = session.file_uri(rel)
                uri_to_rel[doc_uri] = rel
                batch_uris.append((doc_uri, rel, key, content))
                session.notify(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": doc_uri,
                            "languageId": "verse",
                            "version": 1,
                            "text": content,
                        }
                    },
                )

            if not batch_uris:
                continue

            ready = session.diagnostic_uri_count()
            prog(
                phase="analyzing",
                index=min(batch_start + len(batch_uris), total_scan),
                total=total_scan,
                path="",
                message=f"Analyzing {min(batch_start + len(batch_uris), total_scan)}/{total_scan}…",
            )
            _wait_for_diagnostics(
                session,
                min_count=ready + len(batch_uris),
                max_s=batch_settle_s,
            )

            for doc_uri, rel, key, content in batch_uris:
                if _scan_aborted(session):
                    break
                pull_index += 1
                prog(phase="checking", index=pull_index, total=total_scan, path=key)
                # Batch didOpen can publish a false empty set for later files — refresh
                # each doc with didChange before reading (matches Epic VS Code behavior).
                diags = _refresh_document_diagnostics(
                    session,
                    doc_uri,
                    content,
                    settle_s=refresh_settle_s,
                )
                if diags is None:
                    diags = _pull_file_diagnostics(session, doc_uri, timeout_s=pull_timeout)
                if diags is None:
                    # Check FAILED — keep whatever the cache already knows about this
                    # file instead of recording a false "0 errors".
                    failed += 1
                    cached_entry = (cache.get("files") or {}).get(key)
                    if isinstance(cached_entry, dict):
                        file_result = _cached_file_result(key, cached_entry)
                        results.append(file_result)
                        prog(phase="file", index=pull_index, total=total_scan, path=key, file=file_result)
                    else:
                        prog(phase="file", index=pull_index, total=total_scan, path=key)
                    continue
                analyzed += 1
                items = _diagnostics_to_items(diags)
                errors = sum(1 for i in items if i["severity"] == "error")
                warnings = sum(1 for i in items if i["severity"] == "warning")
                # verse-lsp intermittently emits false positives (named-arg / literal
                # tuple mismatches) that UEFN's compiler rejects. A second didChange
                # pass on the same text usually comes back clean — don't pin Problems
                # on a one-shot flake. Transport death mid-confirm keeps the first read.
                if (errors or warnings) and session.is_alive():
                    try:
                        diags2 = _refresh_document_diagnostics(
                            session,
                            doc_uri,
                            content,
                            settle_s=refresh_settle_s,
                        )
                    except Exception:
                        diags2 = None
                    if diags2 is not None:
                        items2 = _diagnostics_to_items(diags2)
                        if not items2:
                            items = items2
                            errors = 0
                            warnings = 0
                file_result = {
                    "path": key,
                    "errors": errors,
                    "warnings": warnings,
                    "items": items,
                }
                abs_path = root_path / _norm_rel(rel)
                try:
                    fp = diagnostics_cache.fingerprint(abs_path)
                    diagnostics_cache.apply_file(root, cache, key, fp, file_result, persist=False)
                except OSError:
                    pass
                results.append(file_result)
                prog(phase="file", index=pull_index, total=total_scan, path=key, file=file_result)

            # Persist per batch so an interrupted scan never loses checked files —
            # the next (queued) scan only rechecks what is genuinely still stale.
            diagnostics_cache.save(root, cache)

        # Capture liveness BEFORE finally stops the session (stop() nulls the proc).
        session_alive = session.is_alive()
    finally:
        session.stop()
        with _state_lock:
            if _active_scan is session:
                _active_scan = None
                _active_root = ""

    if full:
        diagnostics_cache.prune_deleted(root, cache)
    diagnostics_cache.save(root, cache)

    # A scan may authoritatively REPLACE the registry only if verse-lsp stayed alive AND
    # answered for at least one file. A crashed/wedged server that answered for nothing
    # must NOT wipe existing errors to a false all-clear: mark it aborted so the caller
    # merges (replace=False) and keeps the last known problems.
    unhealthy = total_scan > 0 and (not session_alive or analyzed == 0)

    prog(phase="done", index=len(results), total=len(results), path="")
    with_errors = sum(1 for f in results if f["errors"] > 0)
    with_warnings = sum(1 for f in results if f["warnings"] > 0)
    return {
        "files": results,
        "scanned": len(results),
        "with_errors": with_errors,
        "with_warnings": with_warnings,
        "aborted": unhealthy,
    }
