import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getApi } from "./usePanelApi";
import { useVerseEditorOptional } from "../verse-editor/VerseEditorProvider";
import type {
  FolderItem,
  WorkspaceSearchResult,
  WorkspaceSearchScope,
} from "../types/panel";

function basename(path: string): string {
  return path.split("/").pop() || path;
}

function duckyLabel(count: number): string {
  return count === 1 ? "1 ducky" : `${count} duckies`;
}

export interface UseSidebarTextSearchOptions {
  folders: FolderItem[];
  onFileSelect: (path: string, name: string) => void;
  onChatSelect: (chat: { id: string; name: string }) => void;
  defaultScope?: WorkspaceSearchScope;
  lockedScope?: WorkspaceSearchScope;
}

export function useSidebarTextSearch({
  folders,
  onFileSelect,
  onChatSelect,
  defaultScope = "both",
  lockedScope,
}: UseSidebarTextSearchOptions) {
  const [query, setQuery] = useState("");
  const [replaceWith, setReplaceWith] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [scope, setScope] = useState<WorkspaceSearchScope>(lockedScope ?? defaultScope);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [wholeWord, setWholeWord] = useState(false);
  const [results, setResults] = useState<WorkspaceSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [replaceNote, setReplaceNote] = useState<string | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());
  const searchGenRef = useRef(0);

  const verseEditor = useVerseEditorOptional();
  const effectiveScope = lockedScope ?? scope;

  useEffect(() => {
    if (lockedScope) setScope(lockedScope);
  }, [lockedScope]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const runSearch = useCallback(async () => {
    const q = debouncedQuery;
    if (!q) {
      setResults(null);
      setError(null);
      setLoading(false);
      return;
    }
    const api = getApi();
    if (!api?.search_workspace) {
      setError("Search is not available.");
      return;
    }
    const gen = ++searchGenRef.current;
    setLoading(true);
    setError(null);
    setReplaceNote(null);
    try {
      const data = await api.search_workspace(q, effectiveScope, caseSensitive, wholeWord);
      if (searchGenRef.current !== gen) return;
      setResults(data);
      setCollapsedGroups(new Set());
    } catch (e) {
      if (searchGenRef.current !== gen) return;
      setResults(null);
      setError(e instanceof Error ? e.message : "Search failed");
    } finally {
      if (searchGenRef.current === gen) setLoading(false);
    }
  }, [caseSensitive, debouncedQuery, effectiveScope, wholeWord]);

  useEffect(() => {
    void runSearch();
  }, [runSearch]);

  const summary = useMemo(() => {
    if (replaceNote) return replaceNote;
    if (!debouncedQuery) return null;
    if (loading) return "Searching…";
    if (error) return error;
    if (!results) return null;
    const parts: string[] = [];
    if (effectiveScope !== "chats" && results.stats.file_match_count > 0) {
      parts.push(
        `${results.stats.file_match_count} result${results.stats.file_match_count === 1 ? "" : "s"} in ${results.stats.file_count} file${results.stats.file_count === 1 ? "" : "s"}`,
      );
    }
    if (effectiveScope !== "files" && results.stats.chat_match_count > 0) {
      parts.push(
        `${results.stats.chat_match_count} result${results.stats.chat_match_count === 1 ? "" : "s"} in ${duckyLabel(results.stats.chat_count)}`,
      );
    }
    if (parts.length === 0) return "No results found";
    return parts.join(" · ");
  }, [debouncedQuery, effectiveScope, error, loading, replaceNote, results]);

  const toggleGroup = useCallback((key: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const clearSearch = useCallback(() => {
    setQuery("");
    setDebouncedQuery("");
    setResults(null);
    setError(null);
    setReplaceNote(null);
  }, []);

  const runReplaceAll = useCallback(async () => {
    const q = query.trim();
    if (!q || effectiveScope !== "files") return;
    const api = getApi();
    if (!api?.replace_workspace) {
      setError("Replace is not available.");
      return;
    }
    setReplacing(true);
    setError(null);
    setReplaceNote(null);
    try {
      const result = await api.replace_workspace(q, replaceWith, caseSensitive, wholeWord);
      if (result.stats.file_match_count > 0) {
        setReplaceNote(
          `Replaced ${result.stats.file_match_count} in ${result.stats.file_count} file${result.stats.file_count === 1 ? "" : "s"}`,
        );
      } else {
        setReplaceNote("No matches to replace in files");
      }
      await runSearch();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Replace failed");
    } finally {
      setReplacing(false);
    }
  }, [caseSensitive, effectiveScope, query, replaceWith, runSearch, wholeWord]);

  const openFileMatch = useCallback(
    (path: string, line: number, column: number) => {
      const name = basename(path);
      if (verseEditor) {
        verseEditor.openFileAt(path, name, { activate: true });
      } else {
        onFileSelect(path, name);
      }
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          verseEditor?.requestReveal(path, line, column);
        });
      });
    },
    [onFileSelect, verseEditor],
  );

  const openChatMatch = useCallback(
    (chat: { id: string; name: string }) => {
      onChatSelect(chat);
    },
    [onChatSelect],
  );

  const canReplace = Boolean(query.trim()) && !replacing && effectiveScope === "files";

  return {
    folders,
    query,
    setQuery,
    replaceWith,
    setReplaceWith,
    debouncedQuery,
    scope: effectiveScope,
    setScope,
    lockedScope,
    caseSensitive,
    setCaseSensitive,
    wholeWord,
    setWholeWord,
    results,
    loading,
    replacing,
    error,
    summary,
    collapsedGroups,
    toggleGroup,
    clearSearch,
    runSearch,
    runReplaceAll,
    openFileMatch,
    openChatMatch,
    canReplace,
  };
}

export type SidebarTextSearchState = ReturnType<typeof useSidebarTextSearch>;
