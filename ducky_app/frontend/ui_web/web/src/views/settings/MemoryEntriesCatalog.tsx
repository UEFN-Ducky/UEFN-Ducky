import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AppNotice } from "../../components/AppNotice";
import { MdBlockEditor } from "../../components/md-block-editor";
import { MarkdownContent } from "../../components/rich-content/MarkdownContent";
import {
  AgentCatalogCreateModal,
  CatalogDetailHead,
  CatalogListRow,
  CatalogSlideShell,
  CatalogSourceBadge,
  useCatalogSlideNav,
  type CatalogBreadcrumb,
} from "../../components/catalog-slide";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { onApiReady } from "../../hooks/onApiReady";
import { useTimedMessage } from "../../hooks/useTimedMessage";
import { getApi } from "../../hooks/usePanelApi";
import { Icons } from "../../icons/Icons";
import type { SettingsNavLocation } from "../../navigation/settingsHistory";
import {
  useApplySettingsDrill,
  useRecordSettingsLocation,
  useSettingsHistoryBack,
} from "../../navigation/useSettingsHistory";
import type { MemoryEntry, MemoryEntryMeta } from "../../types/panel";

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

function memorySource(author: string | undefined): "custom" | "local" {
  return (author || "").trim() ? "custom" : "local";
}

interface EntryDraft {
  originalName: string;
  name: string;
  description: string;
  author: string;
  content: string;
}

function draftFromEntry(entry: MemoryEntry): EntryDraft {
  return {
    originalName: entry.name,
    name: entry.name,
    description: entry.description || "",
    author: entry.author || "",
    content: entry.content || "",
  };
}

function blankDraft(namePrefill = ""): EntryDraft {
  return { originalName: "", name: namePrefill, description: "", author: "", content: "" };
}

type AuthorFilter = "all" | "shared" | string;

interface MemoryEntriesCatalogProps {
  effectiveRoot: string;
  viewingOtherProject?: boolean;
  reloadToken?: number;
  /** Project picker (and similar) shown in the catalog title row. */
  headerActions?: ReactNode;
}

export function MemoryEntriesCatalog({
  effectiveRoot,
  viewingOtherProject,
  reloadToken = 0,
  headerActions,
}: MemoryEntriesCatalogProps) {
  const { confirm } = useConfirmModal();
  const [entries, setEntries] = useState<MemoryEntryMeta[]>([]);
  const [query, setQuery] = useState("");
  const [authorFilter, setAuthorFilter] = useState<AuthorFilter>("all");
  const [loading, setLoading] = useState(true);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [fullEntry, setFullEntry] = useState<MemoryEntry | null>(null);
  const [focusEntry, setFocusEntry] = useState<MemoryEntry | null>(null);
  const [entryLoading, setEntryLoading] = useState(false);
  const [draft, setDraft] = useState<EntryDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [statusMsg, setStatusMsg] = useTimedMessage();
  const detailScrollRef = useRef<HTMLDivElement>(null);

  const {
    selectedKey,
    setSelectedKey,
    focusId,
    setFocusId,
    detailOpen,
    detailRendered,
    openDetail,
    closeDetail,
    clearFocus,
  } = useCatalogSlideNav();

  const load = useCallback(async () => {
    const api = getApi();
    if (!api?.list_memory_entries) return;
    setLoading(true);
    try {
      const listed = await api.list_memory_entries(effectiveRoot);
      setEntries(listed.entries ?? []);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Failed to load memory");
    } finally {
      setLoading(false);
    }
  }, [effectiveRoot, setStatusMsg]);

  useEffect(() => onApiReady(() => void load()), [load]);
  useEffect(() => {
    if (reloadToken) void load();
  }, [reloadToken, load]);
  useEffect(() => {
    closeDetail();
    void load();
  }, [effectiveRoot, closeDetail, load]);

  const selectedMeta = useMemo(
    () => (selectedKey ? entries.find((e) => e.name === selectedKey) ?? null : null),
    [entries, selectedKey],
  );

  const loadEntry = useCallback(
    async (name: string) => {
      const api = getApi();
      if (!api?.get_memory_entry) return null;
      const res = await api.get_memory_entry(name, effectiveRoot);
      if (!res.ok || !res.entry) {
        setStatusMsg(res.error || "Failed to open entry");
        return null;
      }
      return res.entry;
    },
    [effectiveRoot, setStatusMsg],
  );

  useEffect(() => {
    if (!selectedKey) {
      setFullEntry(null);
      setFocusEntry(null);
      return;
    }
    let cancelled = false;
    setEntryLoading(true);
    void loadEntry(selectedKey)
      .then((entry) => {
        if (!cancelled) setFullEntry(entry);
      })
      .finally(() => {
        if (!cancelled) setEntryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedKey, loadEntry]);

  useEffect(() => {
    if (!focusId) {
      setFocusEntry(null);
      return;
    }
    let cancelled = false;
    setEntryLoading(true);
    void loadEntry(focusId)
      .then((entry) => {
        if (!cancelled) setFocusEntry(entry);
      })
      .finally(() => {
        if (!cancelled) setEntryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [focusId, loadEntry]);

  const authors = useMemo(() => {
    const set = new Set<string>();
    for (const e of entries) {
      const a = (e.author || "").trim();
      if (a) set.add(a);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [entries]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries.filter((e) => {
      const author = (e.author || "").trim();
      if (authorFilter === "shared" && author) return false;
      if (authorFilter !== "all" && authorFilter !== "shared") {
        if (author.toLowerCase() !== authorFilter.toLowerCase()) return false;
      }
      if (!q) return true;
      const hay = [
        e.name,
        e.description,
        e.author,
        ...(e.subs ?? []).flatMap((s) => [s.name, s.description]),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [entries, query, authorFilter]);

  // Memory lives under the LLMs sidebar tab (like Skills/MCPs). History must use
  // tab "LLMs" or SettingsView remaps the publish and this drill never runs.
  const memoryNavLoc = useMemo<SettingsNavLocation>(() => {
    if (!selectedKey) {
      return {
        kind: "settings",
        tab: "LLMs",
        sectionTab: "entries",
        name: "LLMs · entries",
      };
    }
    return {
      kind: "settings",
      tab: "LLMs",
      sectionTab: "entries",
      drill: { type: "memory", entryName: selectedKey },
      name: selectedKey,
    };
  }, [selectedKey]);
  useRecordSettingsLocation(memoryNavLoc);

  const applyMemoryDrill = useCallback(
    (loc: SettingsNavLocation) => {
      if (loc.sectionTab && loc.sectionTab !== "entries") return;
      const entryName = loc.drill?.type === "memory" ? loc.drill.entryName : null;
      if (!entryName) {
        closeDetail();
        return;
      }
      if (!entries.some((e) => e.name === entryName)) {
        closeDetail();
        return;
      }
      setSelectedKey(entryName);
      setFocusId(null);
    },
    [entries, closeDetail, setSelectedKey, setFocusId],
  );
  useApplySettingsDrill("LLMs", applyMemoryDrill);

  const historyCloseDetail = useSettingsHistoryBack(closeDetail);

  const handleDetailBack = () => {
    if (focusId) {
      clearFocus();
      return;
    }
    historyCloseDetail();
  };

  const handleDelete = async (name: string) => {
    if (
      !(await confirm({
        message: `Delete memory entry “${name}”? Duckies will no longer see it.`,
        confirmLabel: "Delete",
        danger: true,
      }))
    ) {
      return;
    }
    const api = getApi();
    if (!api?.delete_memory_entry) return;
    setBusyName(name);
    try {
      const res = await api.delete_memory_entry(name, effectiveRoot);
      if (!res.ok) {
        setStatusMsg(res.error || "Delete failed");
        return;
      }
      if (selectedKey === name || focusId === name) closeDetail();
      setStatusMsg(`Deleted “${name}”`);
      await load();
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setBusyName(null);
    }
  };

  const startEdit = (entry: MemoryEntry) => {
    setDraft(draftFromEntry(entry));
  };

  const saveDraft = async () => {
    if (!draft) return;
    const api = getApi();
    if (!api?.save_memory_entry) return;
    const name = draft.name.trim();
    const content = draft.content.trim();
    if (!name) {
      setStatusMsg("Give the entry a name (e.g. device-naming or coding-standards/error-handling).");
      return;
    }
    if (!content) {
      setStatusMsg("Entry content can’t be empty.");
      return;
    }
    setSaving(true);
    try {
      const res = await api.save_memory_entry(
        name,
        content,
        draft.description.trim(),
        draft.author.trim(),
        effectiveRoot,
      );
      if (!res.ok) {
        setStatusMsg(res.error || "Save failed");
        return;
      }
      const savedName = res.entry?.name || name;
      setDraft(null);
      setStatusMsg(`Saved “${savedName}”`);
      await load();
      if (savedName.includes("/")) {
        const topic = savedName.split("/")[0]!;
        openDetail(topic);
        setFocusId(savedName);
      } else {
        openDetail(savedName);
      }
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleCreateSave = async ({
    name,
    description,
  }: {
    name: string;
    description: string;
    generated: boolean;
  }) => {
    const api = getApi();
    if (!api?.save_memory_entry) throw new Error("Save unavailable — restart the app");
    const n = name.trim();
    if (!n) throw new Error("Give the entry a topic name.");
    const body = description.trim() || "(empty)";
    const res = await api.save_memory_entry(n, body, description.trim(), "", effectiveRoot);
    if (!res.ok) throw new Error(res.error || "Save failed");
    const savedName = res.entry?.name || n;
    await load();
    openDetail(savedName);
    setStatusMsg(`Saved “${savedName}”`);
  };

  const viewEntry = focusId ? focusEntry : fullEntry;
  const viewMeta = focusId
    ? (fullEntry?.subs ?? selectedMeta?.subs ?? []).find((s) => s.name === focusId) ??
      ({ name: focusId, description: focusEntry?.description || "" } as const)
    : selectedMeta;

  const breadcrumbs: CatalogBreadcrumb[] = selectedKey
    ? [
        { id: "root", label: "Memory", onClick: historyCloseDetail },
        {
          id: "topic",
          label: selectedKey,
          current: !focusId,
          onClick: focusId ? clearFocus : undefined,
        },
        ...(focusId
          ? [
              {
                id: "sub",
                label: focusId.split("/")[1] || focusId,
                current: true,
              },
            ]
          : []),
      ]
    : [];

  const selectedBusy = busyName === selectedKey || (focusId != null && busyName === focusId);
  const isNewDraft = draft !== null && draft.originalName === "";
  const editingHere = draft !== null && detailOpen;

  return (
    <CatalogSlideShell
      className="plans-tab memory-tab memory-entries-catalog"
      detailOpen={detailOpen}
      detailRendered={detailRendered}
      detailPlaceholder={<p>Select a memory topic to view it.</p>}
      listAriaLabel="Memory topics"
      notice={statusMsg ? <AppNotice message={statusMsg} className="catalog-slide-notice" /> : null}
      detailScrollRef={detailScrollRef}
      listHeader={
        <>
          <div className="catalog-slide-header">
            <div className="catalog-slide-header-titles">
              <h2 className="catalog-slide-title">Memory</h2>
            </div>
            <div className="catalog-slide-header-actions">
              {headerActions}
              <label className="catalog-slide-search">
                <span className="catalog-slide-search-icon" aria-hidden>
                  <Icons.Search />
                </span>
                <input
                  className="catalog-slide-search-input"
                  type="search"
                  placeholder="Search memory…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
              <button
                type="button"
                className="catalog-slide-action catalog-slide-action--primary"
                onClick={() => setCreateOpen(true)}
                title="New memory entry"
                aria-label="New memory entry"
              >
                <Icons.Plus />
              </button>
              <button
                type="button"
                className="catalog-slide-refresh-btn"
                onClick={() => void load()}
                disabled={loading}
                title="Refresh"
                aria-label="Refresh"
              >
                <Icons.Refresh />
              </button>
            </div>
          </div>
          {viewingOtherProject ? (
            <div className="memory-tab-readonly-hint">Viewing another project’s memory.</div>
          ) : null}
          <div className="memory-tab-author-filters" role="toolbar" aria-label="Filter by author">
            <button
              type="button"
              className={`memory-tab-author-chip${authorFilter === "all" ? " is-active" : ""}`}
              onClick={() => setAuthorFilter("all")}
            >
              All
            </button>
            <button
              type="button"
              className={`memory-tab-author-chip${authorFilter === "shared" ? " is-active" : ""}`}
              onClick={() => setAuthorFilter("shared")}
            >
              Shared
            </button>
            {authors.map((a) => (
              <button
                key={a}
                type="button"
                className={`memory-tab-author-chip${authorFilter === a ? " is-active" : ""}`}
                onClick={() => setAuthorFilter(a)}
              >
                {a}
              </button>
            ))}
          </div>
        </>
      }
      listBody={
        loading ? (
          <div className="catalog-slide-empty">Loading memory…</div>
        ) : filtered.length === 0 ? (
          <div className="catalog-slide-empty">
            {entries.length === 0
              ? "No memory yet. Add an entry or let duckies save facts as they work."
              : "No entries match your filters."}
          </div>
        ) : (
          <ul className="catalog-slide-list">
            {filtered.map((item) => {
              const busy = busyName === item.name;
              const subCount = item.subs?.length ?? 0;
              return (
                <CatalogListRow
                  key={item.name}
                  title={item.name}
                  source={memorySource(item.author)}
                  selected={selectedKey === item.name}
                  disabled={busy}
                  icon={<Icons.Brain />}
                  meta={
                    <>
                      <span>{item.author ? item.author : "shared"}</span>
                      {subCount > 0 ? <span>· {subCount} sub-entries</span> : null}
                      {item.updated ? <span>· {formatMemoryDate(item.updated)}</span> : null}
                    </>
                  }
                  overview={item.description || undefined}
                  onOpen={() => openDetail(item.name)}
                  actions={
                    <button
                      type="button"
                      className="catalog-slide-action catalog-slide-action--danger"
                      title="Delete entry"
                      disabled={busy}
                      onClick={() => void handleDelete(item.name)}
                    >
                      <Icons.Trash />
                    </button>
                  }
                />
              );
            })}
          </ul>
        )
      }
      detailHead={
        selectedKey ? (
          <CatalogDetailHead
            breadcrumbs={breadcrumbs}
            onBack={handleDetailBack}
            backAriaLabel={focusId ? "Back to topic" : "Back to memory list"}
            actions={
              editingHere ? (
                <div className="memory-inplace-actions">
                  <button
                    type="button"
                    className="catalog-slide-action catalog-slide-action--primary"
                    title="Save entry"
                    aria-label="Save entry"
                    disabled={saving}
                    onClick={() => void saveDraft()}
                  >
                    {saving ? "Saving…" : "Save"}
                  </button>
                  <button
                    type="button"
                    className="catalog-slide-action"
                    title="Cancel edit"
                    aria-label="Cancel edit"
                    disabled={saving}
                    onClick={() => setDraft(null)}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    className="catalog-slide-action"
                    title="Edit entry"
                    aria-label="Edit entry"
                    disabled={selectedBusy || !viewEntry}
                    onClick={() => viewEntry && startEdit(viewEntry)}
                  >
                    <Icons.Pencil />
                  </button>
                  {!focusId ? (
                    <button
                      type="button"
                      className="catalog-slide-action"
                      title="Add sub-entry"
                      aria-label="Add sub-entry"
                      disabled={selectedBusy}
                      onClick={() => setDraft(blankDraft(`${selectedKey}/`))}
                    >
                      <Icons.Plus />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="catalog-slide-action catalog-slide-action--danger"
                    title="Delete entry"
                    aria-label="Delete entry"
                    disabled={selectedBusy}
                    onClick={() => void handleDelete(focusId || selectedKey)}
                  >
                    <Icons.Trash />
                  </button>
                </>
              )
            }
          />
        ) : null
      }
      detailBody={
        selectedKey ? (
          entryLoading && !viewEntry && !editingHere ? (
            <div className="catalog-slide-empty">Loading entry…</div>
          ) : editingHere && draft ? (
            <>
              <div className="catalog-slide-detail-kicker">
                {isNewDraft ? "New entry" : focusId || draft.name.includes("/") ? "Sub-entry" : "Topic"}
              </div>
              <div className="memory-tab-edit-meta">
                <label className="memory-tab-field">
                  <span className="memory-tab-field-label">Name</span>
                  <input
                    className="memory-tab-input"
                    type="text"
                    value={draft.name}
                    readOnly={!isNewDraft}
                    placeholder="topic or topic/sub"
                    autoFocus={isNewDraft}
                    onChange={(e) => setDraft((d) => (d ? { ...d, name: e.target.value } : d))}
                  />
                  {isNewDraft ? (
                    <span className="memory-tab-field-hint">
                      Use <code>topic/sub</code> to nest one level.
                    </span>
                  ) : null}
                </label>
                <label className="memory-tab-field">
                  <span className="memory-tab-field-label">Description</span>
                  <input
                    className="memory-tab-input"
                    type="text"
                    value={draft.description}
                    placeholder="When to pull this entry"
                    onChange={(e) => setDraft((d) => (d ? { ...d, description: e.target.value } : d))}
                  />
                </label>
                <label className="memory-tab-field">
                  <span className="memory-tab-field-label">Author (optional)</span>
                  <input
                    className="memory-tab-input"
                    type="text"
                    value={draft.author}
                    placeholder="Ducky name or you"
                    onChange={(e) => setDraft((d) => (d ? { ...d, author: e.target.value } : d))}
                  />
                </label>
              </div>
              <MdBlockEditor
                value={draft.content}
                onChange={(content) => setDraft((d) => (d ? { ...d, content } : d))}
                placeholder="Fact, convention, or solution"
              />
            </>
          ) : viewEntry ? (
            <>
              <div className="catalog-slide-detail-kicker">{focusId ? "Sub-entry" : "Topic"}</div>
              <h2 className="catalog-slide-detail-title">
                {focusId ? focusId.split("/")[1] || focusId : viewEntry.name}
              </h2>
              {viewMeta && "description" in viewMeta && viewMeta.description ? (
                <p className="catalog-slide-detail-overview">{viewMeta.description}</p>
              ) : null}
              <div className="catalog-slide-detail-meta">
                <span>{viewEntry.author ? viewEntry.author : "shared"}</span>
                {viewEntry.updated ? <span>· {formatMemoryDate(viewEntry.updated)}</span> : null}
                <CatalogSourceBadge source={memorySource(viewEntry.author)} />
              </div>
              <div className="memory-tab-reader-body">
                <MarkdownContent text={viewEntry.content} />
              </div>
              {!focusId && (fullEntry?.subs?.length ?? selectedMeta?.subs?.length) ? (
                <div className="memory-tab-reader-subs">
                  <span className="memory-tab-reader-subs-label">Sub-entries</span>
                  <ul className="catalog-slide-list">
                    {(fullEntry?.subs ?? selectedMeta?.subs ?? []).map((sub) => (
                      <CatalogListRow
                        key={sub.name}
                        title={sub.name.split("/")[1] || sub.name}
                        selected={focusId === sub.name}
                        overview={sub.description || undefined}
                        onOpen={() => setFocusId(sub.name)}
                      />
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : (
            <div className="catalog-slide-empty">Could not load this entry.</div>
          )
        ) : null
      }
    >
      <AgentCatalogCreateModal
        open={createOpen}
        title="New memory entry"
        nameLabel="Topic name"
        namePlaceholder="e.g. device-naming"
        descriptionLabel="When to pull"
        descriptionPlaceholder="When should duckies load this memory?"
        generateLabel="Draft"
        emptyLabel="Create empty instead"
        saveLabel="Save entry"
        onClose={() => setCreateOpen(false)}
        onGenerate={async () => ({ checklist: ["Draft ready"] })}
        onSave={handleCreateSave}
      />
    </CatalogSlideShell>
  );
}
