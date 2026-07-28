import { useCallback, useMemo, useRef, useState } from "react";
import { DropdownPanel } from "../../components/DropdownPanel";
import { Icons as GlobalIcons } from "../../icons/Icons";
import {
  fileKey,
  isFileSelected,
  isPackSelected,
  type PackWithFiles,
  type StudioSelection,
} from "../model/types";
import { fileBasename, fileNestDepth } from "../utils/fileDisplay";
import { isUserFile, packBadgeClass, packOriginBadge } from "../utils/packOrigin";
import { Icons } from "./icons";

type KindFilter = "all" | "shipped" | "store" | "custom" | "plugin";

const KIND_LABELS: Record<KindFilter, string> = {
  all: "All",
  shipped: "Shipped",
  store: "Store",
  custom: "Created",
  plugin: "Plugin",
};

interface StudioSidebarProps {
  packs: PackWithFiles[];
  selection: StudioSelection | null;
  dirtyKeys: Record<string, boolean>;
  busy: boolean;
  onSelectPack: (packId: string) => void;
  onSelectFile: (packId: string, fileId: string) => void;
  onOpenCreateModal: () => void;
  onOpenAddFileModal: (packId: string) => void;
  onDeletePack: (packId: string) => void;
  onImport: () => void;
}

function matchesQuery(pack: PackWithFiles, query: string): boolean {
  const q = query.toLowerCase();
  if (
    pack.label.toLowerCase().includes(q) ||
    pack.description.toLowerCase().includes(q) ||
    pack.id.toLowerCase().includes(q)
  ) {
    return true;
  }
  return pack.files.some(
    (f) =>
      f.title.toLowerCase().includes(q) ||
      f.file.toLowerCase().includes(q) ||
      fileBasename(f.file).toLowerCase().includes(q),
  );
}

function filterFiles(pack: PackWithFiles, query: string) {
  if (!query) return pack.files;
  const q = query.toLowerCase();
  return pack.files.filter(
    (f) =>
      f.title.toLowerCase().includes(q) ||
      f.file.toLowerCase().includes(q) ||
      fileBasename(f.file).toLowerCase().includes(q),
  );
}

export function StudioSidebar({
  packs,
  selection,
  dirtyKeys,
  busy,
  onSelectPack,
  onSelectFile,
  onOpenCreateModal,
  onOpenAddFileModal,
  onDeletePack,
  onImport,
}: StudioSidebarProps) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const filterBtnRef = useRef<HTMLButtonElement>(null);

  const handleDelete = useCallback(
    (packId: string) => {
      if (confirmDeleteId !== packId) {
        setConfirmDeleteId(packId);
        return;
      }
      setConfirmDeleteId(null);
      onDeletePack(packId);
    },
    [confirmDeleteId, onDeletePack],
  );

  const trimmedQuery = searchQuery.trim();

  const visible = useMemo(() => {
    return packs
      .filter((p) => {
        if (kindFilter === "shipped" && p.kind !== "bundled") return false;
        if (kindFilter === "store" && packOriginBadge(p) !== "Store") return false;
        if (kindFilter === "custom" && packOriginBadge(p) !== "Created") return false;
        if (kindFilter === "plugin" && p.kind !== "plugin") return false;
        if (trimmedQuery && !matchesQuery(p, trimmedQuery)) return false;
        return true;
      })
      .map((pack) => ({
        pack,
        files: filterFiles(pack, trimmedQuery),
      }))
      .filter(({ pack, files }) => !trimmedQuery || files.length > 0 || matchesQuery(pack, trimmedQuery));
  }, [packs, kindFilter, trimmedQuery]);

  const isPackExpanded = useCallback(
    (packId: string) => {
      if (trimmedQuery) return true;
      return !collapsed[packId];
    },
    [collapsed, trimmedQuery],
  );

  return (
    <aside className="sps-sidebar">
      <div className="sps-sidebar-toolbar">
        <div className="sps-sidebar-actions">
          <button
            type="button"
            className="icon-btn"
            disabled={busy}
            title="Create a new skill pack"
            aria-label="Create a new skill pack"
            onClick={onOpenCreateModal}
          >
            <Icons.Plus className="sps-icon-sm" />
          </button>
          <button
            type="button"
            className="icon-btn"
            disabled={busy}
            title="Import a .ducky-skill-pack"
            aria-label="Import a .ducky-skill-pack"
            onClick={onImport}
          >
            <Icons.Upload className="sps-icon-sm" />
          </button>
        </div>

        <div className="sps-sidebar-search-row">
          <div className="sps-sidebar-search">
            <GlobalIcons.Search />
            <input
              type="search"
              className="sps-sidebar-search-input"
              placeholder="Search packs & files"
              value={searchQuery}
              spellCheck={false}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
          <button
            ref={filterBtnRef}
            type="button"
            className={`icon-btn sps-filter-btn${kindFilter !== "all" ? " is-active" : ""}`}
            title={`Filter: ${KIND_LABELS[kindFilter]}`}
            aria-label={`Filter: ${KIND_LABELS[kindFilter]}`}
            onClick={() => setFilterOpen((v) => !v)}
          >
            <Icons.Filter className="sps-icon-sm" />
          </button>
          <DropdownPanel
            anchorRef={filterBtnRef}
            open={filterOpen}
            onClose={() => setFilterOpen(false)}
            minWidth={140}
            width={140}
          >
            {(["all", "shipped", "store", "custom", "plugin"] as const).map((k) => (
              <button
                key={k}
                type="button"
                className={`sps-filter-option${kindFilter === k ? " is-selected" : ""}`}
                onClick={() => {
                  setKindFilter(k);
                  setFilterOpen(false);
                }}
              >
                {KIND_LABELS[k]}
              </button>
            ))}
          </DropdownPanel>
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="sps-sidebar-empty">
          {packs.length === 0
            ? "No skill packs found. Create or import one."
            : trimmedQuery
              ? "No packs or files match your search."
              : `No ${kindFilter} packs.`}
        </p>
      ) : null}

      {visible.map(({ pack, files }) => {
        const badge = packOriginBadge(pack);
        const canDelete = badge === "Created";
        const expanded = isPackExpanded(pack.id);
        const packActive = isPackSelected(selection, pack.id);
        return (
          <section key={pack.id} className="sps-pack-section">
            <div className={packActive ? "sps-pack-row is-active" : "sps-pack-row"}>
              <button
                type="button"
                className="sps-pack-chevron-btn"
                title={expanded ? "Collapse pack" : "Expand pack"}
                aria-label={expanded ? "Collapse pack" : "Expand pack"}
                onClick={(e) => {
                  e.stopPropagation();
                  setCollapsed((prev) => ({ ...prev, [pack.id]: !prev[pack.id] }));
                }}
              >
                {expanded ? (
                  <Icons.ChevronDown className="sps-icon-sm sps-pack-chevron" />
                ) : (
                  <Icons.ChevronRight className="sps-icon-sm sps-pack-chevron" />
                )}
              </button>
              <button
                type="button"
                className="sps-pack-row-name"
                title={pack.description}
                onClick={() => onSelectPack(pack.id)}
              >
                <Icons.Folder className="sps-icon-sm sps-icon-indigo" />
                {pack.label}
                <span className={packBadgeClass(badge)}>{badge}</span>
              </button>
              <button
                type="button"
                className="sps-pack-row-add"
                disabled={busy || pack.kind === "plugin"}
                title="Add a reference file to this pack"
                onClick={() => {
                  setCollapsed((prev) => ({ ...prev, [pack.id]: false }));
                  onOpenAddFileModal(pack.id);
                }}
              >
                <Icons.Plus className="sps-icon-sm" />
              </button>
              {canDelete ? (
                <button
                  type="button"
                  className={
                    confirmDeleteId === pack.id
                      ? "sps-pack-row-delete is-confirm"
                      : "sps-pack-row-delete"
                  }
                  disabled={busy}
                  title={
                    confirmDeleteId === pack.id
                      ? "Click again to delete this pack"
                      : "Delete this custom pack"
                  }
                  onClick={() => handleDelete(pack.id)}
                >
                  <Icons.Trash className="sps-icon-sm" />
                </button>
              ) : null}
            </div>

            {expanded ? (
              <div className="sps-pack-files">
                {files.map((f) => {
                  const key = fileKey(pack.id, f.id);
                  const depth = fileNestDepth(f.file);
                  return (
                    <button
                      key={key}
                      type="button"
                      className={
                        isFileSelected(selection, pack.id, f.id)
                          ? "sps-file-item is-active"
                          : "sps-file-item"
                      }
                      data-depth={depth}
                      onClick={() => onSelectFile(pack.id, f.id)}
                    >
                      <span className="sps-file-item-name">
                        <Icons.FileText className="sps-icon-sm" />
                        <span className="sps-file-item-basename">{fileBasename(f.file)}</span>
                        {isUserFile(f) ? (
                          <span className="sps-file-badge is-yours" title="You created this file">
                            Yours
                          </span>
                        ) : null}
                        {dirtyKeys[key] ? (
                          <span className="sps-file-dirty-dot" title="Unsaved changes" />
                        ) : null}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null}
          </section>
        );
      })}
    </aside>
  );
}
