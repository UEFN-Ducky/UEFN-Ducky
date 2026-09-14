import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import "monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon.css";
import { Icons } from "../../icons/Icons";
import { FileTypeIcon } from "../../verse-editor/components/FileTypeIcon";
import { useVerseEditorOptional } from "../../verse-editor/VerseEditorProvider";
import { lspKindToCodicon } from "../../verse-editor/outline/outlineSymbolIcons";
import { getApi } from "../../hooks/usePanelApi";
import { useProjectFileIndex } from "../../hooks/useProjectFileIndex";
import { useQuickOpenBridge } from "../../contexts/QuickOpenBridge";
import { useProjectFilesSettings } from "../../contexts/ProjectFilesSettingsContext";
import type { WorkspaceSearchResult } from "../../types/panel";
import { isVerseFile } from "../../verse-editor/utils/isVerseFile";
import { requestOpenSettings } from "../../navigation/openSettingsTab";
import { requestOpenSidebarPanel } from "../../navigation/openSidebarPanel";
import {
  fetchQuickOpenSymbols,
  getQuickOpenEditor,
  listEditorCommands,
  revealSymbolLine,
  runEditorCommand,
} from "./quickOpenEditorBridge";
import {
  flattenChats,
  rankChats,
  rankCommands,
  rankFiles,
  rankSymbols,
  resolveQuickOpenQuery,
  type QuickOpenMode,
} from "./quickOpenUtils";
import {
  clearQueries,
  mergeIdleResources,
  mergeIdleServices,
  pushQuery,
  pushResource,
  pushService,
  readQueries,
  readResources,
  readServices,
  resourceTypeLabel,
  type IdleService,
  type QuickOpenResource,
} from "./quickOpenRecents";

type PaletteRow =
  | { kind: "action"; id: string; label: string; hint?: string; mode: QuickOpenMode }
  | { kind: "file"; path: string; label: string; sub: string }
  | { kind: "chat"; id: string; label: string; sub?: string }
  | { kind: "command"; id: string; label: string }
  | { kind: "symbol"; name: string; line: number; kindNum: number; sub?: string }
  | { kind: "search-file"; path: string; label: string; sub: string; line: number; column: number }
  | { kind: "search-chat"; id: string; label: string; sub: string }
  | { kind: "section"; label: string }
  | { kind: "memory"; name: string; label: string; sub: string }
  | { kind: "ledger"; path: string; label: string; sub: string };

type IdleItem =
  | { kind: "history"; query: string }
  | { kind: "service"; service: IdleService }
  | { kind: "resource"; resource: QuickOpenResource };

const ACTIONS: { id: string; label: string; hint?: string; mode: QuickOpenMode }[] = [
  { id: "goto-file", label: "Go to File", mode: "file" },
  { id: "search-text", label: "Search for Text", hint: "%", mode: "text" },
  { id: "run-command", label: "Show and Run Commands", hint: ">", mode: "command" },
  { id: "goto-symbol", label: "Go to Symbol in File", hint: "@", mode: "symbol" },
  { id: "goto-ducky", label: "Go to Ducky", mode: "ducky" },
];

function basename(path: string): string {
  return path.split("/").pop() || path;
}

function serviceIcon(service: IdleService): ReactNode {
  if (service.kind === "chat") return <Icons.Duck />;
  if (service.id === "settings:Store") return <Icons.Puzzle />;
  if (service.id === "settings:Duckies") return <Icons.Duck />;
  if (service.id === "settings:Plans") return <Icons.Plan />;
  if (service.id === "settings:LLMs") return <Icons.Brain />;
  if (service.id === "settings:Appearance") return <Icons.Sparkles />;
  if (service.id === "settings:Audio") return <Icons.Speaker />;
  if (service.id === "files") return <Icons.File />;
  return <Icons.Gear />;
}

function resourceIcon(resource: QuickOpenResource): ReactNode {
  if (resource.kind === "file" && resource.path) {
    return <FileTypeIcon path={resource.path} size={16} />;
  }
  if (resource.kind === "chat") return <Icons.Duck />;
  if (resource.kind === "project") return <Icons.Folder />;
  return <Icons.Settings />;
}

export function QuickOpenPalette() {
  const { handlers, paletteOpen, mode, seedQuery, closePalette, setMode } = useQuickOpenBridge();
  const verseEditor = useVerseEditorOptional();
  const { treeRefreshToken } = useProjectFilesSettings();
  const { files, reload } = useProjectFileIndex(paletteOpen, treeRefreshToken);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [searchResults, setSearchResults] = useState<WorkspaceSearchResult | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [symbols, setSymbols] = useState<Awaited<ReturnType<typeof fetchQuickOpenSymbols>>>([]);
  const [symbolsLoading, setSymbolsLoading] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [services, setServices] = useState<IdleService[]>([]);
  const [resources, setResources] = useState<QuickOpenResource[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const refreshRecents = useCallback(() => {
    const chats = handlers ? flattenChats(handlers.folders, handlers.rootChats) : [];
    const tabs = handlers?.openTabs ?? [];
    setHistory(readQueries());
    setServices(mergeIdleServices(readServices(), chats, tabs));
    setResources(mergeIdleResources(readResources(), tabs));
  }, [handlers]);

  useEffect(() => {
    if (!paletteOpen) {
      setQuery("");
      setDebouncedQuery("");
      setSearchResults(null);
      setSymbols([]);
      setSelectedIndex(0);
      return;
    }
    if (seedQuery !== undefined) setQuery(seedQuery);
    refreshRecents();
    void reload();
    const t = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [paletteOpen, reload, seedQuery, refreshRecents]);

  useEffect(() => {
    const { mode: resolved, stripped } = resolveQuickOpenQuery(query, mode);
    const timer = window.setTimeout(() => setDebouncedQuery(stripped), resolved === "text" ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [query, mode]);

  const effectiveMode = useMemo((): QuickOpenMode => resolveQuickOpenQuery(query, mode).mode, [query, mode]);
  const strippedQuery = useMemo(() => resolveQuickOpenQuery(query, mode).stripped, [query, mode]);
  const showIdleHome = !strippedQuery && effectiveMode === "file";

  const idleItems = useMemo((): IdleItem[] => {
    if (!showIdleHome) return [];
    return [
      ...history.map((q) => ({ kind: "history" as const, query: q })),
      ...services.map((service) => ({ kind: "service" as const, service })),
      ...resources.map((resource) => ({ kind: "resource" as const, resource })),
    ];
  }, [showIdleHome, history, services, resources]);

  useEffect(() => {
    if (
      (effectiveMode !== "text" && effectiveMode !== "file" && effectiveMode !== "ducky") ||
      !debouncedQuery
    ) {
      setSearchResults(null);
      setSearchLoading(false);
      return;
    }
    const api = getApi();
    if (!api?.search_workspace) return;
    let cancelled = false;
    setSearchLoading(true);
    void api.search_workspace(debouncedQuery, "both", false, false, 80).then((data) => {
      if (!cancelled) {
        setSearchResults(data);
        setSearchLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [effectiveMode, debouncedQuery]);

  useEffect(() => {
    if (!paletteOpen || effectiveMode !== "symbol") {
      setSymbols([]);
      setSymbolsLoading(false);
      return;
    }
    let cancelled = false;
    setSymbolsLoading(true);
    void fetchQuickOpenSymbols().then((data) => {
      if (!cancelled) {
        setSymbols(data);
        setSymbolsLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [paletteOpen, effectiveMode, strippedQuery]);

  const editorCommands = useMemo(() => listEditorCommands(), [paletteOpen, effectiveMode, strippedQuery]);

  const rows = useMemo((): PaletteRow[] => {
    if (!handlers || showIdleHome) return [];
    const q = strippedQuery;
    const allChats = flattenChats(handlers.folders, handlers.rootChats);

    if (effectiveMode === "command") {
      if (!getQuickOpenEditor()) {
        if (!q) return ACTIONS.filter((a) => a.id === "run-command").map((a) => ({ kind: "action" as const, ...a }));
        return [];
      }
      return rankCommands(q, editorCommands).map((c) => ({ kind: "command" as const, id: c.id, label: c.label }));
    }

    if (effectiveMode === "symbol") {
      if (symbolsLoading) return [];
      if (!getQuickOpenEditor()) {
        if (!q) return ACTIONS.filter((a) => a.id === "goto-symbol").map((a) => ({ kind: "action" as const, ...a }));
        return [];
      }
      const editorCtx = getQuickOpenEditor();
      const fileLabel = editorCtx ? basename(editorCtx.path) : "No editor";
      return rankSymbols(q, symbols).map((s) => ({
        kind: "symbol" as const,
        name: s.name,
        line: s.line,
        kindNum: s.kind,
        sub: s.breadcrumb ? `${fileLabel} › ${s.breadcrumb}` : `${fileLabel}:${s.line}`,
      }));
    }

    if (effectiveMode === "text") {
      if (!q) return ACTIONS.filter((a) => a.id === "search-text").map((a) => ({ kind: "action" as const, ...a }));
      if (searchLoading) return [];
      if (!searchResults) return [];
      const out: PaletteRow[] = [];
      for (const fr of searchResults.file_results) {
        for (const m of fr.matches.slice(0, 3)) {
          out.push({
            kind: "search-file",
            path: fr.path,
            label: basename(fr.path),
            sub: `${fr.path}:${m.line} — ${m.preview.trim()}`,
            line: m.line,
            column: m.column,
          });
        }
      }
      for (const cr of searchResults.chat_results) {
        for (const m of cr.matches.slice(0, 2)) {
          out.push({ kind: "search-chat", id: cr.id, label: cr.title, sub: m.preview.trim() });
        }
      }
      return out.slice(0, 50);
    }

    if (!q && effectiveMode === "ducky") {
      return [
        { kind: "section" as const, label: "duckies" },
        ...allChats.slice(0, 40).map((c) => ({
          kind: "chat" as const,
          id: c.id,
          label: c.name,
          sub: c.duckyStyle || "Ducky",
        })),
      ];
    }

    if (effectiveMode === "ducky") {
      const named = rankChats(q, allChats, 30).map((c) => ({
        kind: "chat" as const,
        id: c.id,
        label: c.name,
        sub: c.duckyStyle || "Ducky",
      }));
      const seen = new Set(named.map((c) => c.id));
      const out: PaletteRow[] = named.length ? [{ kind: "section", label: "duckies" }, ...named] : [];
      if (searchResults) {
        const extra: PaletteRow[] = [];
        for (const cr of searchResults.chat_results) {
          if (seen.has(cr.id)) continue;
          extra.push({
            kind: "search-chat",
            id: cr.id,
            label: cr.title,
            sub: (cr.matches[0]?.preview || "").trim(),
          });
        }
        for (const mem of searchResults.memory_results || []) {
          extra.push({ kind: "memory", name: mem.name, label: mem.name, sub: mem.preview });
        }
        if (extra.length) out.push({ kind: "section", label: "in history & memory" }, ...extra);
      }
      return out.slice(0, 50);
    }

    const fileRows = rankFiles(q, files).map((f) => ({
      kind: "file" as const,
      path: f.path,
      label: f.name,
      sub: f.path,
    }));
    const chatRows = rankChats(q, allChats, 12).map((c) => ({
      kind: "chat" as const,
      id: c.id,
      label: c.name,
      sub: c.duckyStyle || "Ducky",
    }));
    const out: PaletteRow[] = [];
    if (fileRows.length) out.push({ kind: "section", label: "files" }, ...fileRows);
    if (chatRows.length) out.push({ kind: "section", label: "duckies" }, ...chatRows);
    if (searchResults) {
      const seenFiles = new Set(fileRows.map((f) => f.path));
      const seenChats = new Set(chatRows.map((c) => c.id));
      const extras: PaletteRow[] = [];
      for (const fr of searchResults.file_results) {
        if (seenFiles.has(fr.path)) continue;
        const m = fr.matches[0];
        extras.push({
          kind: "search-file",
          path: fr.path,
          label: basename(fr.path),
          sub: m ? `${fr.path}:${m.line} — ${String(m.preview).trim()}` : fr.path,
          line: Number(m?.line || 1),
          column: Number(m?.column || 1),
        });
      }
      for (const cr of searchResults.chat_results) {
        if (seenChats.has(cr.id)) continue;
        extras.push({
          kind: "search-chat",
          id: cr.id,
          label: cr.title,
          sub: (cr.matches[0]?.preview || "").trim(),
        });
      }
      for (const led of searchResults.ledger_results || []) {
        if (seenFiles.has(led.path)) continue;
        extras.push({ kind: "ledger", path: led.path, label: basename(led.path), sub: `ledger · ${led.path}` });
      }
      for (const hist of searchResults.history_results || []) {
        if (seenFiles.has(hist.path)) continue;
        extras.push({
          kind: "ledger",
          path: hist.path,
          label: basename(hist.path),
          sub: hist.ducky_name ? `history · ${hist.ducky_name}` : `history · ${hist.path}`,
        });
      }
      for (const mem of searchResults.memory_results || []) {
        extras.push({ kind: "memory", name: mem.name, label: mem.name, sub: mem.preview });
      }
      if (extras.length) out.push({ kind: "section", label: "in content & ledger" }, ...extras.slice(0, 24));
    }
    return out.slice(0, 60);
  }, [
    handlers,
    showIdleHome,
    strippedQuery,
    effectiveMode,
    files,
    searchResults,
    searchLoading,
    symbols,
    symbolsLoading,
    editorCommands,
  ]);

  useEffect(() => {
    if (showIdleHome) {
      setSelectedIndex(0);
      return;
    }
    const first = rows.findIndex((r) => r.kind !== "section");
    setSelectedIndex(first >= 0 ? first : 0);
  }, [rows.length, effectiveMode, debouncedQuery, showIdleHome, idleItems.length]);

  const rememberQuery = useCallback((raw: string) => {
    const stripped = resolveQuickOpenQuery(raw, mode).stripped;
    if (stripped) setHistory(pushQuery(stripped));
  }, [mode]);

  const openChat = useCallback(
    (id: string, name: string) => {
      if (!handlers) return;
      const chat = flattenChats(handlers.folders, handlers.rootChats).find((c) => c.id === id);
      handlers.onOpenChat(chat ?? { id, name });
      pushService(`chat:${id}`);
      pushResource({ id: `chat:${id}`, kind: "chat", label: name, chatId: id });
      closePalette();
    },
    [handlers, closePalette],
  );

  const openFile = useCallback(
    (path: string, name: string) => {
      if (!handlers) return;
      handlers.onOpenFile(path, name);
      pushResource({ id: `file:${path}`, kind: "file", label: name, path });
      closePalette();
    },
    [handlers, closePalette],
  );

  const activateService = useCallback(
    (service: IdleService) => {
      pushService(service.id);
      if (service.kind === "chat" && service.chatId) {
        openChat(service.chatId, service.label);
        return;
      }
      if (service.id === "files") {
        requestOpenSidebarPanel("files");
        closePalette();
        return;
      }
      if (service.id.startsWith("settings:")) {
        const tab = service.id.slice("settings:".length);
        pushResource({ id: service.id, kind: "settings", label: service.label, settingsTab: tab });
        requestOpenSettings(tab);
        closePalette();
      }
    },
    [openChat, closePalette],
  );

  const activateResource = useCallback(
    (resource: QuickOpenResource) => {
      if (resource.kind === "chat" && resource.chatId) {
        openChat(resource.chatId, resource.label);
        return;
      }
      if (resource.kind === "file" && resource.path) {
        openFile(resource.path, isVerseFile(resource.path) ? basename(resource.path) : resource.label);
        return;
      }
      if (resource.kind === "settings") {
        pushService(resource.settingsTab ? `settings:${resource.settingsTab}` : "settings:General");
        requestOpenSettings(resource.settingsTab || "General");
        closePalette();
      }
    },
    [openChat, openFile, closePalette],
  );

  const selectIdle = useCallback(
    (item: IdleItem) => {
      if (item.kind === "history") {
        setQuery(item.query);
        return;
      }
      if (item.kind === "service") {
        activateService(item.service);
        return;
      }
      activateResource(item.resource);
    },
    [activateService, activateResource],
  );

  const selectRow = useCallback(
    (row: PaletteRow) => {
      if (!handlers) return;
      if (row.kind === "section") return;
      if (row.kind === "action") {
        setMode(row.mode);
        if (row.mode === "text") setQuery("%");
        else if (row.mode === "command") setQuery(">");
        else if (row.mode === "symbol") setQuery("@");
        else setQuery("");
        return;
      }
      rememberQuery(query);
      if (row.kind === "file") {
        openFile(row.path, isVerseFile(row.path) ? basename(row.path) : row.label);
        return;
      }
      if (row.kind === "chat") {
        openChat(row.id, row.label);
        return;
      }
      if (row.kind === "command") {
        runEditorCommand(row.id);
        closePalette();
        return;
      }
      if (row.kind === "symbol") {
        if (getQuickOpenEditor()) revealSymbolLine(row.line);
        closePalette();
        return;
      }
      if (row.kind === "search-file") {
        handlers.onOpenFile(row.path, basename(row.path));
        verseEditor?.requestReveal(row.path, row.line, row.column);
        pushResource({ id: `file:${row.path}`, kind: "file", label: basename(row.path), path: row.path });
        closePalette();
        return;
      }
      if (row.kind === "ledger") {
        openFile(row.path, basename(row.path));
        return;
      }
      if (row.kind === "memory") {
        closePalette();
        return;
      }
      if (row.kind === "search-chat") {
        openChat(row.id, row.label);
      }
    },
    [handlers, closePalette, setMode, verseEditor, rememberQuery, query, openFile, openChat],
  );

  const moveIdle = (delta: number, from = selectedIndex) => {
    if (idleItems.length === 0) return 0;
    return Math.max(0, Math.min(idleItems.length - 1, from + delta));
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      closePalette();
      return;
    }

    if (showIdleHome) {
      const current = idleItems[selectedIndex];
      const serviceCols = window.matchMedia("(max-width: 720px)").matches ? 2 : 4;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => {
          if (current?.kind === "service") {
            const servicesStart = history.length;
            const next = i + serviceCols;
            if (next < servicesStart + services.length) return next;
            return moveIdle(1, servicesStart + services.length - 1);
          }
          return moveIdle(1, i);
        });
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => {
          if (current?.kind === "service") {
            const servicesStart = history.length;
            const next = i - serviceCols;
            if (next >= servicesStart) return next;
            return history.length ? history.length - 1 : 0;
          }
          return moveIdle(-1, i);
        });
        return;
      }
      if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && current?.kind === "service") {
        e.preventDefault();
        const dir = e.key === "ArrowRight" ? 1 : -1;
        const servicesStart = history.length;
        const servicesEnd = servicesStart + services.length - 1;
        setSelectedIndex((i) => Math.max(servicesStart, Math.min(servicesEnd, i + dir)));
        return;
      }
      if (e.key === "Enter" && idleItems[selectedIndex]) {
        e.preventDefault();
        selectIdle(idleItems[selectedIndex]);
      }
      return;
    }

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((i) => {
        let n = Math.min(i + 1, Math.max(0, rows.length - 1));
        while (n < rows.length - 1 && rows[n]?.kind === "section") n += 1;
        return n;
      });
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((i) => {
        let n = Math.max(i - 1, 0);
        while (n > 0 && rows[n]?.kind === "section") n -= 1;
        return n;
      });
      return;
    }
    if (e.key === "Enter" && rows[selectedIndex]) {
      e.preventDefault();
      selectRow(rows[selectedIndex]);
    }
  };

  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-row-index="${selectedIndex}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  if (!paletteOpen) return null;

  const placeholder =
    effectiveMode === "text"
      ? "Search files and duckies for text…"
      : effectiveMode === "ducky"
        ? "Type a ducky name…"
        : effectiveMode === "command"
          ? "Type a command name…"
          : effectiveMode === "symbol"
            ? "Type a symbol name…"
            : "Search files, duckies, and history…";

  const listLoading = searchLoading || symbolsLoading;
  const projectLabel = handlers?.projectName?.trim();
  const footerText = projectLabel ? `Searching ${projectLabel}` : handlers ? "Searching this project" : "No project open";

  const selectedIdle = showIdleHome ? idleItems[selectedIndex] : null;
  const historyStart = 0;
  const servicesStart = history.length;
  const resourcesStart = history.length + services.length;

  return createPortal(
    <div className="quick-open-overlay no-drag" onMouseDown={(e) => e.target === e.currentTarget && closePalette()}>
      <div className="quick-open-palette" onKeyDown={onKeyDown}>
        <div className="quick-open-palette-input-wrap">
          <Icons.Search />
          <input
            ref={inputRef}
            type="text"
            className="quick-open-palette-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={placeholder}
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <div className="quick-open-palette-list" ref={listRef}>
          {showIdleHome ? (
            <>
              <div className="quick-open-palette-heading">
                <span>Search history</span>
                {history.length > 0 ? (
                  <button
                    type="button"
                    className="quick-open-palette-clear"
                    onClick={() => {
                      clearQueries();
                      setHistory([]);
                      setSelectedIndex(0);
                    }}
                  >
                    Clear all
                  </button>
                ) : null}
              </div>
              {history.length === 0 ? (
                <div className="quick-open-palette-empty quick-open-palette-empty--quiet">No recent searches</div>
              ) : (
                history.map((q, i) => {
                  const index = historyStart + i;
                  const isSelected = selectedIdle?.kind === "history" && selectedIndex === index;
                  return (
                    <button
                      key={`history-${q}`}
                      type="button"
                      data-row-index={index}
                      className={`quick-open-palette-row${isSelected ? " is-selected" : ""}`}
                      onMouseEnter={() => setSelectedIndex(index)}
                      onClick={() => setQuery(q)}
                    >
                      <span className="quick-open-palette-row-icon">
                        <Icons.Clock />
                      </span>
                      <span className="quick-open-palette-row-label">{q}</span>
                    </button>
                  );
                })
              )}

              <div className="quick-open-palette-heading">Recent agents</div>
              <div className="quick-open-services-grid" role="list">
                {services.map((service, i) => {
                  const index = servicesStart + i;
                  const isSelected = selectedIdle?.kind === "service" && selectedIndex === index;
                  return (
                    <button
                      key={service.id}
                      type="button"
                      data-row-index={index}
                      className={`quick-open-service-tile${isSelected ? " is-selected" : ""}`}
                      onMouseEnter={() => setSelectedIndex(index)}
                      onClick={() => activateService(service)}
                    >
                      <span className="quick-open-service-tile-icon">{serviceIcon(service)}</span>
                      <span className="quick-open-service-tile-label">{service.label}</span>
                    </button>
                  );
                })}
              </div>

              <div className="quick-open-palette-heading">Recent resources</div>
              {resources.length === 0 ? (
                <div className="quick-open-palette-empty quick-open-palette-empty--quiet">Open a ducky or file to pin it here</div>
              ) : (
                resources.map((resource, i) => {
                  const index = resourcesStart + i;
                  const isSelected = selectedIdle?.kind === "resource" && selectedIndex === index;
                  return (
                    <button
                      key={resource.id}
                      type="button"
                      data-row-index={index}
                      className={`quick-open-palette-row${isSelected ? " is-selected" : ""}`}
                      onMouseEnter={() => setSelectedIndex(index)}
                      onClick={() => activateResource(resource)}
                    >
                      <span className="quick-open-palette-row-icon">{resourceIcon(resource)}</span>
                      <span className="quick-open-palette-row-label">{resource.label}</span>
                      <span className="quick-open-palette-row-type">{resourceTypeLabel(resource.kind)}</span>
                    </button>
                  );
                })
              )}
            </>
          ) : rows.length === 0 && listLoading ? (
            <div className="quick-open-palette-empty">Searching…</div>
          ) : rows.length === 0 ? (
            <div className="quick-open-palette-empty">
              {query.trim()
                ? effectiveMode === "symbol" && !getQuickOpenEditor()
                  ? "Open a file in the editor first"
                  : "No results"
                : "Type to search"}
            </div>
          ) : (
            rows.map((row, index) => {
              const isSelected = index === selectedIndex;
              const rowKey =
                row.kind === "symbol"
                  ? `${row.name}-${row.line}`
                  : "path" in row
                    ? row.path
                    : "id" in row
                      ? row.id
                      : row.label;
              if (row.kind === "section") {
                return (
                  <div key={`${row.kind}-${index}-${row.label}`} className="quick-open-palette-section">
                    {row.label}
                  </div>
                );
              }
              return (
                <button
                  key={`${row.kind}-${index}-${rowKey}`}
                  type="button"
                  data-row-index={index}
                  className={`quick-open-palette-row${isSelected ? " is-selected" : ""}`}
                  onMouseEnter={() => setSelectedIndex(index)}
                  onClick={() => selectRow(row)}
                >
                  {row.kind === "action" ? (
                    <>
                      <span className="quick-open-palette-row-label">{row.label}</span>
                      {row.hint ? <span className="quick-open-palette-row-hint">{row.hint}</span> : null}
                    </>
                  ) : row.kind === "file" || row.kind === "search-file" || row.kind === "ledger" ? (
                    <>
                      <span className="quick-open-palette-row-icon">
                        <FileTypeIcon path={row.path} size={14} />
                      </span>
                      <span className="quick-open-palette-row-body">
                        <span className="quick-open-palette-row-label">{row.label}</span>
                        <span className="quick-open-palette-row-sub">{row.sub}</span>
                      </span>
                    </>
                  ) : row.kind === "symbol" ? (
                    <>
                      <span className={`quick-open-palette-row-icon codicon ${lspKindToCodicon(row.kindNum)}`} />
                      <span className="quick-open-palette-row-body">
                        <span className="quick-open-palette-row-label">{row.name}</span>
                        {row.sub ? <span className="quick-open-palette-row-sub">{row.sub}</span> : null}
                      </span>
                    </>
                  ) : (
                    <span className="quick-open-palette-row-body">
                      <span className="quick-open-palette-row-label">{row.label}</span>
                      {"sub" in row && row.sub ? <span className="quick-open-palette-row-sub">{row.sub}</span> : null}
                    </span>
                  )}
                </button>
              );
            })
          )}
        </div>
        <div className="quick-open-palette-footer">{footerText}</div>
      </div>
    </div>,
    document.body,
  );
}
