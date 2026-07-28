import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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

  recentTabs,

  resolveQuickOpenQuery,

  type QuickOpenMode,

} from "./quickOpenUtils";



const PALETTE_WIDTH = 560;



type PaletteRow =

  | { kind: "action"; id: string; label: string; hint?: string; mode: QuickOpenMode }

  | { kind: "recent"; tabId: string; label: string; sub?: string; tabKind: "file" | "chat" }

  | { kind: "file"; path: string; label: string; sub: string }

  | { kind: "chat"; id: string; label: string }

  | { kind: "command"; id: string; label: string }

  | { kind: "symbol"; name: string; line: number; kindNum: number; sub?: string }

  | { kind: "search-file"; path: string; label: string; sub: string; line: number; column: number }

  | { kind: "search-chat"; id: string; label: string; sub: string };



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

  const inputRef = useRef<HTMLInputElement>(null);

  const listRef = useRef<HTMLDivElement>(null);



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

    void reload();

    const t = window.setTimeout(() => inputRef.current?.focus(), 0);

    return () => window.clearTimeout(t);

  }, [paletteOpen, reload, seedQuery]);



  useEffect(() => {

    const { mode: resolved, stripped } = resolveQuickOpenQuery(query, mode);

    const timer = window.setTimeout(

      () => setDebouncedQuery(stripped),

      resolved === "text" ? 300 : 0,

    );

    return () => window.clearTimeout(timer);

  }, [query, mode]);



  const effectiveMode = useMemo((): QuickOpenMode => {

    return resolveQuickOpenQuery(query, mode).mode;

  }, [query, mode]);



  const strippedQuery = useMemo(() => resolveQuickOpenQuery(query, mode).stripped, [query, mode]);



  useEffect(() => {

    if (effectiveMode !== "text" || !debouncedQuery) {

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

    if (!handlers) return [];

    const q = strippedQuery;

    const allChats = flattenChats(handlers.folders, handlers.rootChats);



    if (effectiveMode === "command") {

      if (!getQuickOpenEditor()) {

        if (!q) return ACTIONS.filter((a) => a.id === "run-command").map((a) => ({ kind: "action" as const, ...a }));

        return [];

      }

      return rankCommands(q, editorCommands).map((c) => ({

        kind: "command" as const,

        id: c.id,

        label: c.label,

      }));

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

          out.push({

            kind: "search-chat",

            id: cr.id,

            label: cr.title,

            sub: m.preview.trim(),

          });

        }

      }

      return out.slice(0, 50);

    }



    if (!q) {

      const out: PaletteRow[] = ACTIONS.map((a) => ({ kind: "action", ...a }));

      for (const tab of recentTabs(handlers.openTabs)) {

        out.push({

          kind: "recent",

          tabId: tab.id,

          label: tab.name,

          sub: tab.kind === "file" ? tab.path : "Ducky",

          tabKind: tab.kind === "file" ? "file" : "chat",

        });

      }

      return out;

    }



    if (effectiveMode === "ducky") {

      return rankChats(q, allChats).map((c) => ({

        kind: "chat" as const,

        id: c.id,

        label: c.name,

      }));

    }



    const fileRows = rankFiles(q, files).map((f) => ({

      kind: "file" as const,

      path: f.path,

      label: f.name,

      sub: f.path,

    }));

    const chatRows =

      effectiveMode === "file"

        ? rankChats(q, allChats, 8).map((c) => ({

            kind: "chat" as const,

            id: c.id,

            label: c.name,

          }))

        : [];

    return [...fileRows, ...chatRows];

  }, [

    handlers,

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

    setSelectedIndex(0);

  }, [rows.length, effectiveMode, debouncedQuery]);



  const selectRow = useCallback(

    (row: PaletteRow) => {

      if (!handlers) return;

      if (row.kind === "action") {

        setMode(row.mode);

        if (row.mode === "text") setQuery("%");

        else if (row.mode === "command") setQuery(">");

        else if (row.mode === "symbol") setQuery("@");

        else setQuery("");

        return;

      }

      if (row.kind === "recent") {

        const tab = handlers.openTabs.find((t) => t.id === row.tabId);

        if (!tab) return;

        if (tab.kind === "file" && tab.path) {

          handlers.onOpenFile(tab.path, tab.name);

        } else if (tab.kind === "chat" && tab.chatId) {

          handlers.onOpenChat({ id: tab.chatId, name: tab.name, duckyStyle: tab.duckyStyle });

        }

        closePalette();

        return;

      }

      if (row.kind === "file") {

        handlers.onOpenFile(row.path, isVerseFile(row.path) ? basename(row.path) : row.label);

        closePalette();

        return;

      }

      if (row.kind === "chat") {

        const chat = flattenChats(handlers.folders, handlers.rootChats).find((c) => c.id === row.id);

        handlers.onOpenChat(chat ?? { id: row.id, name: row.label });

        closePalette();

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

        closePalette();

        return;

      }

      if (row.kind === "search-chat") {

        const chat = flattenChats(handlers.folders, handlers.rootChats).find((c) => c.id === row.id);

        handlers.onOpenChat(chat ?? { id: row.id, name: row.label });

        closePalette();

      }

    },

    [handlers, closePalette, setMode, verseEditor],

  );



  const onKeyDown = (e: React.KeyboardEvent) => {

    if (e.key === "Escape") {

      e.preventDefault();

      closePalette();

      return;

    }

    if (e.key === "ArrowDown") {

      e.preventDefault();

      setSelectedIndex((i) => Math.min(i + 1, Math.max(0, rows.length - 1)));

      return;

    }

    if (e.key === "ArrowUp") {

      e.preventDefault();

      setSelectedIndex((i) => Math.max(i - 1, 0));

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

            : "Search files, content, and symbols…";



  const listLoading = searchLoading || symbolsLoading;



  return createPortal(

    <div className="quick-open-overlay no-drag" onMouseDown={(e) => e.target === e.currentTarget && closePalette()}>

      <div className="quick-open-palette" style={{ width: PALETTE_WIDTH }} onKeyDown={onKeyDown}>

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

          {listLoading ? (

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

              const showRecentHeader =

                row.kind === "recent" &&

                (index === 0 || rows[index - 1]?.kind !== "recent") &&

                rows[index - 1]?.kind === "action";

              const rowKey =

                row.kind === "symbol"

                  ? `${row.name}-${row.line}`

                  : "path" in row

                    ? row.path

                    : "id" in row

                      ? row.id

                      : row.label;

              return (

                <div key={`${row.kind}-${index}-${rowKey}`}>

                  {showRecentHeader ? (

                    <div className="quick-open-palette-section">recently opened</div>

                  ) : null}

                  <button

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

                    ) : row.kind === "file" || row.kind === "search-file" ? (

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

                      <>

                        <span className="quick-open-palette-row-body">

                          <span className="quick-open-palette-row-label">{row.label}</span>

                          {"sub" in row && row.sub ? (

                            <span className="quick-open-palette-row-sub">{row.sub}</span>

                          ) : null}

                        </span>

                        {row.kind === "recent" ? (

                          <span className="quick-open-palette-recent-tag">recent</span>

                        ) : null}

                      </>

                    )}

                  </button>

                </div>

              );

            })

          )}

        </div>

      </div>

    </div>,

    document.body,

  );

}


