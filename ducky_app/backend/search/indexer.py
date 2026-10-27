"""Keep the search index in ``ducky.db`` up to date with the skills and tools on this PC.

Sources: every skill pack (shipped, user-made, Store, and every installed plugin's
packs, enabled or not) and the full MCP tool catalog. Each source has a revision
(the pack files' stat stamp, the catalog's content hash) stored in
``search_state``; a source whose revision has not moved is skipped, and within a
changed source only rows whose text changed are re-embedded.

Refreshes run on one background thread per process ("search-index"). Triggers:
the first search in a process, :func:`request_refresh` (plugin install / update /
uninstall / enable / disable, Skills studio saves, a new tool catalog) and a
periodic check. Any Ducky process (the app, an IDE bridge, an agent's bridge) may
index: rows are upserted by key, so two processes indexing at once agree.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.search import chunker
from backend.store.repos import search as repo

_log = logging.getLogger("uefn_ducky.search")

CHECK_EVERY_S = 30.0
DEBOUNCE_S = 0.75
IDLE_EXIT_S = 10.0  # the worker thread ends after this long with nothing to do
# Records a plugin test wrote are purged when the test ends; these outlived a test whose
# app was closed first. No test runs this long.
SANDBOX_RECORD_TTL_S = 6 * 3600.0

_state_lock = threading.Lock()
_wake = threading.Event()
_thread: threading.Thread | None = None
_pending: set[str] = set()
_last_check = 0.0
_running = False
_done = threading.Event()
_done.set()
_stop = threading.Event()
_last_report: dict[str, Any] = {}


@dataclass
class PackSource:
    pack_id: str
    root: Path
    manifest: dict[str, Any]
    origin: str
    plugin_id: str = ""

    @property
    def source_key(self) -> str:
        return f"pack:{self.pack_id}"


# --------------------------------------------------------------------------- sources


def _origin_for(manifest: dict[str, Any]) -> str:
    kind = str(manifest.get("kind") or "")
    if kind == "plugin":
        return "plugin"
    if kind == "bundled":
        return "shipped"
    if kind == "store":
        return "store"
    core = next((s for s in manifest.get("subskills") or [] if isinstance(s, dict) and s.get("id") == "core"), {})
    return str(core.get("origin") or "user")


def live_pack_sources() -> list[PackSource]:
    """Every pack on this PC, including packs of installed plugins that are switched off."""
    from backend.skills import store as skills

    owned: dict[str, tuple[str, Path]] = {}
    try:
        from backend.uefn_plugins.store import list_plugin_owned_skills

        for entry in list_plugin_owned_skills():
            pid = str(entry.get("pack_id") or "")
            if pid:
                owned[pid] = (str(entry.get("plugin_id") or ""), Path(str(entry.get("path") or "")))
    except Exception:  # noqa: BLE001
        pass
    out: list[PackSource] = []
    seen: set[str] = set()
    for pack_id in [*skills.list_pack_ids(), *owned]:
        if pack_id in seen:
            continue
        seen.add(pack_id)
        manifest = skills.load_pack_manifest(pack_id)
        root = next((r for r in skills._pack_roots(pack_id) if skills._is_pack_dir(r)), None)
        if manifest is None or root is None:
            plugin_id, path = owned.get(pack_id, ("", Path()))
            if not (path / skills.PACK_FILE).is_file():
                continue
            root = path
            manifest = skills._manifest_from_skill_dir(path, pack_id, "plugin")
        plugin_id = owned.get(pack_id, ("", Path()))[0] or str(manifest.get("source_plugin_id") or "")
        out.append(PackSource(pack_id, root, manifest, _origin_for(manifest), plugin_id))
    return out


def pack_sources_from_dirs(entries: Iterable[tuple[str, Path, str, str]]) -> list[PackSource]:
    """Explicit packs: (pack_id, folder, origin, plugin_id). Used by the eval runner and tests."""
    from backend.skills import store as skills

    out: list[PackSource] = []
    for pack_id, root, origin, plugin_id in entries:
        if (Path(root) / skills.PACK_FILE).is_file():
            manifest = skills._manifest_from_skill_dir(Path(root), pack_id, "plugin" if plugin_id else "custom")
            out.append(PackSource(pack_id, Path(root), manifest, origin, plugin_id))
    return out


def _owner_rows(tools: list[Any]) -> list[dict[str, Any]]:
    from backend.agent.toolsets.tool_index import tool_catalog_row

    plugin_of: dict[str, tuple[str, str]] = {}
    try:
        from backend.uefn_plugins.host import uefn_agent_tool_rows, uefn_plugin_tool_group_rows

        for row in list(uefn_agent_tool_rows()) + list(uefn_plugin_tool_group_rows()):
            pid = str(row.get("id") or "").strip()
            label = str(row.get("label") or pid).strip() or pid
            for name in row.get("tool_names") or []:
                if isinstance(name, str) and name.strip():
                    plugin_of[name.strip()] = (pid, label)
    except Exception:  # noqa: BLE001
        pass
    try:
        from frontend.ui_web.mcp_catalog import _tool_category_map

        categories = _tool_category_map()
    except Exception:  # noqa: BLE001
        categories = {}
    rows: list[dict[str, Any]] = []
    for tool in tools:
        row = tool_catalog_row(tool)
        name = row["name"]
        if "__" in name:
            row["owner_label"] = f"MCP server: {name.split('__', 1)[0]}"
        elif name in plugin_of:
            row["plugin_id"], label = plugin_of[name]
            row["owner_label"] = f"Plugin: {label}"
            row["owner_about"] = _plugin_about(row["plugin_id"])
        else:
            row["owner_label"] = categories.get(name, ("", "UEFN Ducky"))[1]
        rows.append(row)
    return rows


def _plugin_about(plugin_id: str) -> str:
    """The plugin's own one-line description, so "media kit" or "my trailer" reaches its tools
    (Oct 26: "search team assets I have" ranked site storage tools over Island Media Kit's)."""
    try:
        from backend.uefn_plugins.store import load_plugin_manifest

        about = str((load_plugin_manifest(plugin_id) or {}).get("description") or "")
    except Exception:  # noqa: BLE001
        return ""
    return " ".join(about.split())[:200]


def live_tool_rows() -> tuple[list[dict[str, Any]], str] | None:
    """(tool rows, catalog revision), or None when the catalog is not complete in this process."""
    try:
        from backend.workspace.ai_ignore import current_policy

        if current_policy().strict:
            return None  # the strict policy lists only safe tools; never shrink the index to them
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.uefn_plugins.host import plugins_ready

        if not plugins_ready():
            return None
    except Exception:  # noqa: BLE001
        pass
    from backend.agent.toolsets.tool_index import catalog_revision

    try:
        tools = _list_tools_blocking_unfiltered()
    except Exception:  # noqa: BLE001
        _log.debug("search: tool listing failed", exc_info=True)
        return None
    return _owner_rows(tools), catalog_revision(tools)


def _list_tools_blocking_unfiltered() -> list[Any]:
    import asyncio
    import concurrent.futures

    from backend.agent.tools import list_mcp_tools

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # The search-index thread: each nested server's connect and listing is bounded
        # by the MCP pool; a cap here would abandon slow cold listings for good.
        return asyncio.run(list_mcp_tools(apply_filters=False))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="search-list-tools")
    try:
        return pool.submit(lambda: asyncio.run(list_mcp_tools(apply_filters=False))).result(timeout=60)
    finally:
        # Not `with`: its exit waits for the listing, so the 60 s bound never applied.
        pool.shutdown(wait=False)


def _enabled_mcp_servers() -> set[str]:
    try:
        from backend.mcp_plugins.store import list_mcp_servers

        return {str(s.get("id") or "") for s in list_mcp_servers() if s.get("enabled")}
    except Exception:  # noqa: BLE001
        return set()


# --------------------------------------------------------------------------- indexing


def _embed(docs: list[repo.Doc]) -> tuple[dict[str, bytes], str]:
    from backend.search.embedder import get_embedder

    emb = get_embedder()
    if emb is None or not docs:
        return {}, (emb.model_id if emb else "")
    vectors = emb.encode([chunker.embed_text(d) for d in docs])
    return {d.doc_key: vectors[i].tobytes() for i, d in enumerate(docs)}, emb.model_id


def _model_id() -> str:
    from backend.search.embedder import get_embedder

    emb = get_embedder()
    return emb.model_id if emb else ""


def index_source(source_key: str, docs: list[repo.Doc], revision: str, *, keep: set[str] | None = None) -> dict[str, int]:
    """Write one source: only new or changed rows (and rows without a vector) are embedded."""
    model = _model_id()
    have = repo.existing(source_key, model)
    need = [d for d in docs if d.doc_key not in have or have[d.doc_key][1] != d.content_hash or (model and not have[d.doc_key][2])]
    vectors, model = _embed(need)
    return repo.write_source(source_key, docs, vectors, model=model or "", revision=revision, keep=keep)


def _pack_revision(src: PackSource, model: str) -> str:
    from backend.skills.store import _pack_stamp

    return f"{chunker.CHUNKER_VERSION}|{model}|{src.root}|{src.origin}|{src.plugin_id}|{_pack_stamp(src.root)}"


def index_packs(sources: list[PackSource], *, prune: bool = True) -> dict[str, int]:
    model = _model_id()
    states = repo.states()
    stats = {"packs": 0, "skipped": 0, "added": 0, "changed": 0, "removed": 0}
    current = {s.source_key for s in sources}
    for src in sources:
        revision = _pack_revision(src, model)
        if states.get(src.source_key, ("", 0.0))[0] == revision and repo.count_source(src.source_key):
            stats["skipped"] += 1
            continue
        docs = chunker.pack_docs(src.pack_id, src.root, src.manifest, origin=src.origin, plugin_id=src.plugin_id)
        res = index_source(src.source_key, docs, revision)
        stats["packs"] += 1
        for k in ("added", "changed", "removed"):
            stats[k] += res[k]
    if prune:
        for key in list(states) + repo.source_keys():
            if key.startswith("pack:") and key not in current:
                stats["removed"] += repo.drop_source(key)
    return stats


def index_tools(rows: list[dict[str, Any]], catalog_rev: str) -> dict[str, int]:
    model = _model_id()
    revision = f"{chunker.CHUNKER_VERSION}|{model}|{catalog_rev}"
    if repo.get_state("tools") == revision and repo.count_source("tools"):
        return {"skipped": 1}
    docs = chunker.tool_docs(rows)
    names = {d.doc_key for d in docs}
    # A nested MCP server that is enabled but not answering keeps its last rows, and so
    # does a desktop plugin that is enabled but not registered in this process yet (a
    # slow boot register, a bridge started before the plugin was switched on): the
    # catalog lists only what is loaded here.
    enabled = _enabled_mcp_servers()
    listed_plugins = {str(r.get("plugin_id") or "") for r in rows}
    enabled_plugins = _enabled_desktop_plugins() - listed_plugins - _tried_and_not_loaded()
    owners = repo.tool_owners("tools")
    keep = {
        key for key in repo.existing("tools", model)
        if key not in names and (
            ("__" in key and key.split(":", 1)[1].split("__", 1)[0] in enabled)
            or owners.get(key, "") in enabled_plugins
        )
    }
    return index_source("tools", docs, revision, keep=keep)


def _tried_and_not_loaded() -> set[str]:
    """Enabled plugins that failed to load or are paused here: not "still registering",
    so their old tool rows go (process-local state; another process keeps serving its own)."""
    try:
        from backend.uefn_plugins.host import tried_and_not_loaded

        return tried_and_not_loaded()
    except Exception:  # noqa: BLE001
        return set()


def _enabled_desktop_plugins() -> set[str]:
    try:
        from backend.uefn_plugins.store import get_enabled_plugin_ids

        return {str(p) for p in get_enabled_plugin_ids() if p}
    except Exception:  # noqa: BLE001
        return set()


def fill_missing_vectors() -> int:
    model = _model_id()
    if not model:
        return 0
    filled = 0
    docs = repo.missing_vectors(model)
    if docs:
        vectors, model = _embed(docs)
        # Only rows that still hold the text the vectors were made from (see set_vectors).
        filled = repo.set_vectors(vectors, model, {d.doc_key: d.content_hash for d in docs})
    try:
        from backend.search import sealed_records
        from backend.store import account_rows

        filled += sealed_records.fill_missing_vectors(account_rows.current())
    except Exception:  # noqa: BLE001 — the account's key isn't here yet: the next fill does them
        _log.debug("search: sealed record vectors not filled yet", exc_info=True)
    return filled


def refresh_now(*, packs: bool = True, tools: bool = True) -> dict[str, Any]:
    """Index every changed source in this thread; returns what it did."""
    started = time.perf_counter()
    report: dict[str, Any] = {}
    if packs:
        try:
            from backend.skills.store import pass_memo

            with pass_memo():  # each pack's files are stat'd once per pass, not three times
                report["packs"] = index_packs(live_pack_sources())
        except Exception as exc:  # noqa: BLE001
            _log.warning("search: indexing skills failed (%s)", type(exc).__name__, exc_info=True)
            report["packs_error"] = type(exc).__name__
    if tools:
        try:
            live = live_tool_rows()
            report["tools"] = index_tools(*live) if live else {"deferred": 1}
        except Exception as exc:  # noqa: BLE001
            _log.warning("search: indexing tools failed (%s)", type(exc).__name__, exc_info=True)
            report["tools_error"] = type(exc).__name__
    try:
        report["filled"] = fill_missing_vectors()
    except Exception:  # noqa: BLE001
        _log.debug("search: filling vectors failed", exc_info=True)
    try:
        # A plugin test whose app was closed before the test ended never purged its sandbox.
        report["stale_sandbox_records"] = repo.drop_stale_sandbox_records(SANDBOX_RECORD_TTL_S)
    except Exception:  # noqa: BLE001
        _log.debug("search: dropping stale sandbox records failed", exc_info=True)
    report["seconds"] = round(time.perf_counter() - started, 3)
    report["at"] = time.time()
    global _last_report
    _last_report = report
    return report


# --------------------------------------------------------------------------- background


def _worker() -> None:
    global _running, _last_check, _thread
    while True:
        if not _wake.wait(IDLE_EXIT_S):
            with _state_lock:
                if not _wake.is_set():
                    _thread = None
                    return
            continue
        if _stop.is_set():
            with _state_lock:
                _thread = None
                _done.set()
            return
        time.sleep(DEBOUNCE_S)
        with _state_lock:
            _wake.clear()
            reasons = set(_pending)
            _pending.clear()
            _running = True
            _done.clear()
        # The tool catalog is listed only when something says it changed (or was never
        # indexed): listing it can wake nested MCP servers, a skill stat pass cannot.
        quiet = {"skills-only", "periodic"}
        try:
            # Inside the try: a failed read here used to kill the worker with _running
            # still True (periodic checks stopped, the Database card said "Indexing" for good).
            try:
                tools = bool(reasons - quiet) or repo.get_state("tools") is None
            except Exception:  # noqa: BLE001
                tools = bool(reasons - quiet)
            refresh_now(tools=tools)
        except Exception:  # noqa: BLE001
            _log.debug("search: refresh failed", exc_info=True)
        finally:
            with _state_lock:
                _running = False
                _last_check = time.monotonic()
                if not _wake.is_set():
                    _done.set()


def autoindex_enabled() -> bool:
    """Background indexing; tests switch it off (DUCKY_SEARCH_AUTOINDEX=0) and index explicitly,
    so a refresh thread never outlives its test and writes into the next test's database."""
    import os

    return os.environ.get("DUCKY_SEARCH_AUTOINDEX", "").strip() != "0"


def request_refresh(reason: str = "") -> None:
    """Schedule a background refresh (debounced); returns at once."""
    global _thread
    if not autoindex_enabled():
        return
    with _state_lock:
        _pending.add(reason or "requested")
        _done.clear()
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, name="search-index", daemon=True)
            _thread.start()
        _wake.set()


def maybe_refresh(*, wait_s: float = 0.0) -> None:
    """Called before a search: refresh when stale; wait up to ``wait_s`` when the index is empty."""
    now = time.monotonic()
    if now - _last_check >= CHECK_EVERY_S and not _running and not _wake.is_set():
        request_refresh("periodic")
    if wait_s > 0 and (_running or _wake.is_set()):
        _done.wait(wait_s)


def wait_idle(timeout: float = 30.0) -> bool:
    return _done.wait(timeout)


def last_report() -> dict[str, Any]:
    return dict(_last_report)


def is_running() -> bool:
    return _running or _wake.is_set()


def on_change(reason: str) -> Callable[[], None]:
    """A callback for code that changes skills or plugins."""
    return lambda: request_refresh(reason)


def forget_plugin(plugin_id: str) -> None:
    """Uninstall: drop the plugin's records now and refuse its later record writes (from a
    thread that outlived the unload, or its copy in a bridge); packs and tools go with the
    next refresh."""
    try:
        repo.forget_records(plugin_id)
    except Exception:  # noqa: BLE001
        _log.warning("search: dropping the records of %s failed", plugin_id, exc_info=True)
    request_refresh(f"uninstall:{plugin_id}")


def plugin_installed(plugin_id: str) -> None:
    """Install or reinstall: the plugin may write records again."""
    try:
        repo.clear_plugin_gone(plugin_id)
    except Exception:  # noqa: BLE001
        _log.warning("search: re-allowing records of %s failed", plugin_id, exc_info=True)


def reset_for_tests() -> None:
    """End a worker a test started (it would index the next test's database) and forget state."""
    global _last_check, _last_report
    worker = _thread
    if worker is not None and worker.is_alive():
        _stop.set()
        _wake.set()
        worker.join(5.0)
    with _state_lock:
        _pending.clear()
        _wake.clear()
        _done.set()
        _stop.clear()
    _last_check = 0.0
    _last_report = {}


def status() -> dict[str, Any]:
    """What Settings shows: rows per kind, vectors, the model, when it last indexed."""
    from backend.search.embedder import get_embedder, unavailable_reason

    emb = get_embedder()
    out: dict[str, Any] = {"ok": True, "running": is_running(), "model": emb.model_id if emb else "",
                           "model_error": "" if emb else (unavailable_reason() or "no model")}
    try:
        out.update(repo.counts())
    except Exception as exc:  # noqa: BLE001
        out.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    report = last_report()
    if report:
        out["last_report"] = report
    return out


def rebuild() -> dict[str, Any]:
    """Drop every derived row (plugin records stay) and index everything again (in the background)."""
    repo.wipe()
    request_refresh("rebuild")
    return {"ok": True, "started": True}
