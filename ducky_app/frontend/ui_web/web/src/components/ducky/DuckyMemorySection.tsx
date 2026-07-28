import { useCallback, useEffect, useMemo, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { Icons } from "../../icons/Icons";
import { requestOpenSettings } from "../../navigation/openSettingsTab";
import type { MemoryEntry, MemoryEntryMeta } from "../../types/panel";
import { MarkdownContent } from "../rich-content/MarkdownContent";

function formatMemoryDate(raw: string): string {
  const s = (raw || "").trim();
  if (!s) return "";
  const t = Date.parse(s);
  if (Number.isNaN(t)) return s;
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(t));
  } catch {
    return s;
  }
}

/**
 * Project-memory entries for this ducky (by author name), with expand-to-read.
 * Falls back to all project entries when none match the ducky name.
 */
export function DuckyMemorySection({ duckyName }: { duckyName: string }) {
  const [entries, setEntries] = useState<MemoryEntryMeta[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [openEntry, setOpenEntry] = useState<MemoryEntry | null>(null);
  const [busyName, setBusyName] = useState<string | null>(null);

  const name = duckyName.trim().toLowerCase();

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const api = getApi();
      if (!api?.list_memory_entries) {
        if (!cancelled) setLoaded(true);
        return;
      }
      try {
        const res = await api.list_memory_entries();
        if (cancelled) return;
        setEntries(res.entries ?? []);
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleEntry = useCallback(
    async (entryName: string) => {
      if (openEntry?.name === entryName) {
        setOpenEntry(null);
        return;
      }
      const api = getApi();
      if (!api?.get_memory_entry) return;
      setBusyName(entryName);
      try {
        const res = await api.get_memory_entry(entryName);
        if (res.ok && res.entry) setOpenEntry(res.entry);
      } finally {
        setBusyName(null);
      }
    },
    [openEntry],
  );

  const { rows, scope } = useMemo(() => {
    if (!entries.length) return { rows: [] as MemoryEntryMeta[], scope: "empty" as const };
    const mine = name
      ? entries.filter((e) => (e.author || "").trim().toLowerCase() === name)
      : [];
    if (mine.length) return { rows: mine, scope: "author" as const };
    return { rows: entries, scope: "project" as const };
  }, [entries, name]);

  if (!loaded) {
    return <p className="ducky-profile-tab-hint">Loading memory…</p>;
  }

  return (
    <section className="ducky-memory-section">
      <div className="ducky-memory-head">
        <span className="ducky-editor-prompt-card-icon" aria-hidden>
          <Icons.BookOpen />
        </span>
        <span className="ducky-editor-prompt-card-title">Memory</span>
        <button
          type="button"
          className="ducky-memory-open-all"
          onClick={() => requestOpenSettings("Memory")}
        >
          Open project memory
        </button>
      </div>
      {rows.length === 0 ? (
        <p className="ducky-memory-empty">
          No memory entries yet. When this ducky saves with{" "}
          <code>project_memory_save</code>, they show up here with date — press a row to read.
        </p>
      ) : (
        <>
          {scope === "project" && name ? (
            <p className="ducky-memory-empty">
              Nothing authored by <strong>{duckyName.trim()}</strong> — showing all project memory.
            </p>
          ) : null}
          <ul className="ducky-memory-list">
            {rows.map((e) => {
              const isOpen = openEntry?.name === e.name;
              const when = formatMemoryDate(e.updated);
              return (
                <li key={e.name} className="ducky-memory-item">
                  <button
                    type="button"
                    className={`ducky-memory-row${isOpen ? " is-open" : ""}`}
                    onClick={() => void toggleEntry(e.name)}
                    disabled={busyName === e.name}
                  >
                    <span className="ducky-memory-row-top">
                      <span className="ducky-memory-row-name">{e.name}</span>
                      {when ? <span className="ducky-memory-row-date">{when}</span> : null}
                    </span>
                    <span className="ducky-memory-row-desc">
                      {e.description || (e.author ? `by ${e.author}` : "Memory entry")}
                    </span>
                    <span className="ducky-memory-row-hint">
                      {busyName === e.name
                        ? "Loading…"
                        : isOpen
                          ? "Press to close"
                          : "Press to view"}
                    </span>
                  </button>
                  {isOpen && openEntry ? (
                    <div className="ducky-memory-body">
                      <MarkdownContent text={openEntry.content} />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
