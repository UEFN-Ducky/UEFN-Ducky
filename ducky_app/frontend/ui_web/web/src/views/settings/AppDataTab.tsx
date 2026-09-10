import { useCallback, useEffect, useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { useUiTarget } from "../../ui-targets/registry";
import type { AppDataItem, AppDataKind, AppDataOverview, AppDataProject } from "../../types/panel";
import { GeneralSectionHeader } from "./GeneralSectionHeader";

export type AppDataInnerTab = "all" | "cache" | "projects" | "installed";

const INNER_TABS: { id: AppDataInnerTab; label: string }[] = [
  { id: "all", label: "All" },
  { id: "cache", label: "Cache" },
  { id: "projects", label: "Projects" },
  { id: "installed", label: "Installed" },
];

const KIND_LABEL: Record<AppDataKind, string> = {
  cache: "Cache",
  user: "User data",
  install: "Installed",
  runtime: "Runtime",
  settings: "Settings",
  other: "Other",
};

const DETAILS_CHEVRON = (
  <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" fill="none" strokeWidth="2" aria-hidden>
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

function HardDriveIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <line x1="22" y1="12" x2="2" y2="12" />
      <path d="M5.45 5.11L2 12v6a2 2 0 002 2h16a2 2 0 002-2v-6l-3.45-6.89A2 2 0 0016.76 4H7.24a2 2 0 00-1.79 1.11z" />
      <line x1="6" y1="16" x2="6.01" y2="16" />
      <line x1="10" y1="16" x2="10.01" y2="16" />
    </svg>
  );
}

export function formatAppDataBytes(n: number): string {
  if (!n || n < 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function countsLabel(item: { files: number; dirs: number; is_dir: boolean }): string {
  if (!item.is_dir) return "File";
  const bits: string[] = [];
  if (item.dirs) bits.push(`${item.dirs} folder${item.dirs === 1 ? "" : "s"}`);
  if (item.files) bits.push(`${item.files} file${item.files === 1 ? "" : "s"}`);
  return bits.join(" · ") || "Empty";
}

function stop(e: MouseEvent) {
  e.preventDefault();
  e.stopPropagation();
}

function ActionBtn({
  children,
  danger,
  disabled,
  onClick,
}: {
  children: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`settings-btn appdata-row-btn${danger ? " general-tab-btn-danger" : ""}`}
      disabled={disabled}
      onClick={(e) => {
        stop(e);
        onClick();
      }}
    >
      {children}
    </button>
  );
}

function KindBadge({ kind }: { kind: AppDataKind }) {
  return <span className={`appdata-kind appdata-kind--${kind}`}>{KIND_LABEL[kind]}</span>;
}

function ItemActions({
  item,
  busy,
  onOpen,
  onClear,
  onDelete,
}: {
  item: AppDataItem;
  busy: boolean;
  onOpen: () => void;
  onClear: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="appdata-row-actions">
      <ActionBtn disabled={busy} onClick={onOpen}>
        Open
      </ActionBtn>
      {item.clearable ? (
        <ActionBtn disabled={busy} onClick={onClear}>
          Clear
        </ActionBtn>
      ) : null}
      {item.deletable ? (
        <ActionBtn danger disabled={busy} onClick={onDelete}>
          Delete
        </ActionBtn>
      ) : null}
      {item.protected ? <span className="appdata-protected">Protected</span> : null}
    </div>
  );
}

function AppDataRow({
  item,
  busyKey,
  childrenByRel,
  loadChildren,
  onOpen,
  onClear,
  onDelete,
}: {
  item: AppDataItem;
  busyKey: string | null;
  childrenByRel: Record<string, { items: AppDataItem[]; truncated: boolean; total: number } | "loading">;
  loadChildren: (rel: string) => void;
  onOpen: (rel: string) => void;
  onClear: (item: AppDataItem) => void;
  onDelete: (item: AppDataItem) => void;
}) {
  const busy = busyKey === item.rel;
  if (!item.is_dir) {
    return (
      <div className="appdata-file-row">
        <div className="appdata-file-row-copy">
          <div className="appdata-file-row-title">{item.label}</div>
          <div className="appdata-file-row-meta">
            {formatAppDataBytes(item.bytes)}
            <KindBadge kind={item.kind} />
          </div>
        </div>
        <ItemActions
          item={item}
          busy={busy}
          onOpen={() => onOpen(item.rel)}
          onClear={() => onClear(item)}
          onDelete={() => onDelete(item)}
        />
      </div>
    );
  }

  const kidState = childrenByRel[item.rel];

  return (
    <details
      className="appearance-details appdata-details"
      onToggle={(e) => {
        if ((e.currentTarget as HTMLDetailsElement).open) loadChildren(item.rel);
      }}
    >
      <summary>
        <div className="appdata-summary-copy">
          <div className="appearance-tab-details-title">{item.label}</div>
          <div className="appdata-summary-meta">
            {formatAppDataBytes(item.bytes)}
            <span className="appdata-dot">·</span>
            {countsLabel(item)}
            <KindBadge kind={item.kind} />
          </div>
        </div>
        <div className="appearance-details-summary-actions">
          <ItemActions
            item={item}
            busy={busy}
            onOpen={() => onOpen(item.rel)}
            onClear={() => onClear(item)}
            onDelete={() => onDelete(item)}
          />
          <div className="appearance-tab-details-chevron">{DETAILS_CHEVRON}</div>
        </div>
      </summary>
      <div className="appearance-details-content appdata-details-content">
        <p className="appdata-item-desc">{item.description}</p>
        {kidState === "loading" ? <p className="appdata-empty">Measuring…</p> : null}
        {kidState && kidState !== "loading" ? (
          kidState.items.length === 0 ? (
            <p className="appdata-empty">Empty folder.</p>
          ) : (
            <div className="appdata-children">
              {kidState.items.map((child) => (
                <AppDataRow
                  key={child.rel}
                  item={child}
                  busyKey={busyKey}
                  childrenByRel={childrenByRel}
                  loadChildren={loadChildren}
                  onOpen={onOpen}
                  onClear={onClear}
                  onDelete={onDelete}
                />
              ))}
              {kidState.truncated ? (
                <p className="appdata-empty">
                  Showing {kidState.items.length} of {kidState.total} — open the folder for the rest.
                </p>
              ) : null}
            </div>
          )
        ) : null}
      </div>
    </details>
  );
}

export function AppDataTab() {
  const pageRef = useUiTarget("settings.general.app_data", {
    kind: "settings_field",
    label: "App Data",
    route: "settings.general",
  });
  const { confirm, alert } = useConfirmModal();
  const [inner, setInner] = useState<AppDataInnerTab>("all");
  const [overview, setOverview] = useState<AppDataOverview | null>(null);
  const [projects, setProjects] = useState<AppDataProject[]>([]);
  const [projectBytes, setProjectBytes] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [childrenByRel, setChildrenByRel] = useState<
    Record<string, { items: AppDataItem[]; truncated: boolean; total: number } | "loading">
  >({});

  const reload = useCallback(async () => {
    const api = getApi();
    if (!api?.appdata_overview) return;
    setLoading(true);
    setError("");
    try {
      const [ov, proj] = await Promise.all([api.appdata_overview(), api.appdata_projects()]);
      setOverview(ov);
      setProjects(Array.isArray(proj?.projects) ? proj.projects : []);
      setProjectBytes(typeof proj?.bytes === "number" ? proj.bytes : 0);
      setChildrenByRel({});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read App Data.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => onApiReady(() => void reload()), [reload]);

  const loadChildren = useCallback((rel: string) => {
    const api = getApi();
    if (!api?.appdata_children) return;
    setChildrenByRel((prev) => {
      if (prev[rel] && prev[rel] !== "loading") return prev;
      return { ...prev, [rel]: "loading" };
    });
    void api.appdata_children(rel).then((res) => {
      setChildrenByRel((prev) => ({
        ...prev,
        [rel]: { items: res.items || [], truncated: !!res.truncated, total: res.total || 0 },
      }));
    });
  }, []);

  const openRel = useCallback((rel: string) => {
    const api = getApi();
    if (api?.appdata_open) void api.appdata_open(rel);
    else if (api) void api.open_appdata();
  }, []);

  const runMutate = useCallback(
    async (key: string, work: () => Promise<{ ok?: boolean; error?: string }>) => {
      const api = getApi();
      if (!api) return;
      setBusyKey(key);
      setNote("");
      try {
        const result = await work();
        if (result && result.ok === false) {
          await alert({
            title: "Could not change App Data",
            message:
              result.error === "protected"
                ? "That item is protected — the app needs it."
                : result.error === "refused_root"
                  ? "The App Data root cannot be deleted from here."
                  : result.error || "The folder may be in use.",
          });
        }
        await reload();
      } finally {
        setBusyKey(null);
      }
    },
    [alert, reload],
  );

  const onClear = useCallback(
    async (item: AppDataItem) => {
      if (
        !(await confirm({
          title: `Clear ${item.label}?`,
          message: item.is_dir
            ? `Empty “${item.label}”? Everything inside is deleted. The folder stays.`
            : `Delete the file “${item.label}”?`,
          confirmLabel: "Clear",
          danger: true,
        }))
      ) {
        return;
      }
      await runMutate(item.rel, () => getApi()!.appdata_clear(item.rel));
    },
    [confirm, runMutate],
  );

  const onDelete = useCallback(
    async (item: AppDataItem) => {
      if (
        !(await confirm({
          title: `Delete ${item.label}?`,
          message: `Permanently delete “${item.label}” from App Data?`,
          confirmLabel: "Delete",
          danger: true,
        }))
      ) {
        return;
      }
      await runMutate(item.rel, () => getApi()!.appdata_delete(item.rel));
    },
    [confirm, runMutate],
  );

  const onClearCaches = useCallback(async () => {
    if (
      !(await confirm({
        title: "Clear all caches?",
        message:
          "Empty cache folders (captures, backups, LSP, diagnostics, traces). Chats, plugins, and settings stay.",
        confirmLabel: "Clear caches",
        danger: true,
      }))
    ) {
      return;
    }
    await runMutate("*caches", async () => {
      const result = await getApi()!.appdata_clear_caches();
      setNote(
        result.cleared?.length
          ? `Cleared ${result.cleared.length} cache folder${result.cleared.length === 1 ? "" : "s"}.`
          : "No cache folders to clear.",
      );
      return result;
    });
  }, [confirm, runMutate]);

  const onSweep = useCallback(async () => {
    const api = getApi();
    if (!api?.appdata_sweep) return;
    setBusyKey("*sweep");
    setNote("");
    try {
      const result = await api.appdata_sweep();
      const bits = Object.entries(result || {})
        .filter(([, n]) => typeof n === "number" && n > 0)
        .map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`);
      setNote(bits.length ? `Swept ${bits.join(", ")}.` : "Nothing to sweep.");
      await reload();
    } finally {
      setBusyKey(null);
    }
  }, [reload]);

  const onDeleteProject = useCallback(
    async (project: AppDataProject) => {
      if (
        !(await confirm({
          title: `Delete data for ${project.label}?`,
          message: `Remove chats, workspace, file history, diagnostics, changesets, and memory for “${project.label}”.`,
          confirmLabel: "Delete project data",
          danger: true,
        }))
      ) {
        return;
      }
      await runMutate(project.slug, () => getApi()!.appdata_delete_project(project.slug));
    },
    [confirm, runMutate],
  );

  const visibleItems = useMemo(() => {
    const items = overview?.items || [];
    if (inner === "cache") return items.filter((row) => row.kind === "cache");
    if (inner === "installed") return items.filter((row) => row.kind === "install");
    if (inner === "projects") return [];
    return items;
  }, [overview, inner]);

  const busy = busyKey != null;

  return (
    <div className="general-tab-shell appdata-tab-shell" ref={pageRef}>
      <h2 className="general-tab-page-title">App Data</h2>
      <section className="general-tab-section">
        <GeneralSectionHeader
          icon={<HardDriveIcon />}
          title="Local data"
          description="Everything under this folder — chats, plugins, captures, caches. Clear or delete from here; settings keys stay protected."
        />
        <p className="appdata-tab-path" title={overview?.root}>
          {overview?.root || "%LOCALAPPDATA%\\UEFN-Ducky"}
        </p>
        <div className="appdata-tab-stats">
          <span className="appdata-tab-size">
            {loading && !overview ? "Measuring…" : formatAppDataBytes(overview?.bytes || 0)}
          </span>
          {overview ? (
            <span className="appdata-tab-counts">
              {overview.items.length} items
              {overview.files ? ` · ${overview.files} files` : ""}
            </span>
          ) : null}
        </div>
        <div className="appdata-tab-toolbar log-errors-actions no-drag">
          <button type="button" className="settings-btn" disabled={busy} onClick={() => openRel("")}>
            Open folder
          </button>
          <button type="button" className="settings-btn" disabled={busy || loading} onClick={() => void reload()}>
            Refresh
          </button>
          <button type="button" className="settings-btn" disabled={busy} onClick={() => void onSweep()}>
            {busyKey === "*sweep" ? "Sweeping…" : "Sweep backups"}
          </button>
          {inner === "cache" ? (
            <button
              type="button"
              className="settings-btn general-tab-btn-danger"
              disabled={busy}
              onClick={() => void onClearCaches()}
            >
              Clear all caches
            </button>
          ) : null}
        </div>
        {note ? <p className="appdata-tab-note">{note}</p> : null}
        {error ? <p className="general-tab-section-note general-tab-section-note--error">{error}</p> : null}
      </section>

      <nav className="appdata-inner-tabs" aria-label="App Data sections">
        {INNER_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`settings-view-header-tab${inner === tab.id ? " is-active" : ""}`}
            onClick={() => setInner(tab.id)}
          >
            {tab.label}
            {tab.id === "projects" && projects.length ? (
              <span className="appdata-inner-count">{projects.length}</span>
            ) : null}
          </button>
        ))}
      </nav>

      {inner === "projects" ? (
        <div className="appdata-list">
          {loading && projects.length === 0 ? <p className="appdata-empty">Measuring…</p> : null}
          {!loading && projects.length === 0 ? (
            <p className="appdata-empty">No per-project folders yet.</p>
          ) : null}
          {projects.length ? (
            <p className="appdata-tab-counts appdata-projects-total">
              {formatAppDataBytes(projectBytes)} across {projects.length} project
              {projects.length === 1 ? "" : "s"}
            </p>
          ) : null}
          {projects.map((project) => (
            <details key={project.slug} className="appearance-details appdata-details">
              <summary>
                <div className="appdata-summary-copy">
                  <div className="appearance-tab-details-title">{project.label}</div>
                  <div className="appdata-summary-meta">
                    {formatAppDataBytes(project.bytes)}
                    {project.path ? (
                      <>
                        <span className="appdata-dot">·</span>
                        {project.path}
                      </>
                    ) : null}
                  </div>
                </div>
                <div className="appearance-details-summary-actions">
                  <div className="appdata-row-actions">
                    {project.areas[0] ? (
                      <ActionBtn disabled={busy} onClick={() => openRel(project.areas[0].rel)}>
                        Open
                      </ActionBtn>
                    ) : null}
                    <ActionBtn danger disabled={busy} onClick={() => void onDeleteProject(project)}>
                      Delete
                    </ActionBtn>
                  </div>
                  <div className="appearance-tab-details-chevron">{DETAILS_CHEVRON}</div>
                </div>
              </summary>
              <div className="appearance-details-content appdata-details-content">
                {project.areas.map((area) => (
                  <div key={area.rel} className="appdata-file-row">
                    <div className="appdata-file-row-copy">
                      <div className="appdata-file-row-title">{area.name}</div>
                      <div className="appdata-file-row-meta">{formatAppDataBytes(area.bytes)}</div>
                    </div>
                    <div className="appdata-row-actions">
                      <ActionBtn disabled={busy} onClick={() => openRel(area.rel)}>
                        Open
                      </ActionBtn>
                      <ActionBtn
                        disabled={busy}
                        onClick={() =>
                          void onClear({
                            name: area.name,
                            rel: area.rel,
                            is_dir: true,
                            bytes: area.bytes,
                            files: area.files,
                            dirs: area.dirs,
                            kind: "user",
                            label: area.name,
                            description: "",
                            clearable: true,
                            deletable: true,
                            protected: false,
                          })
                        }
                      >
                        Clear
                      </ActionBtn>
                    </div>
                  </div>
                ))}
              </div>
            </details>
          ))}
        </div>
      ) : (
        <div className="appdata-list">
          {loading && visibleItems.length === 0 ? <p className="appdata-empty">Measuring folder sizes…</p> : null}
          {!loading && visibleItems.length === 0 ? (
            <p className="appdata-empty">
              {inner === "cache"
                ? "No cache folders."
                : inner === "installed"
                  ? "No installed packs in App Data."
                  : "App Data folder is empty."}
            </p>
          ) : null}
          {visibleItems.map((item) => (
            <AppDataRow
              key={item.rel}
              item={item}
              busyKey={busyKey}
              childrenByRel={childrenByRel}
              loadChildren={loadChildren}
              onOpen={openRel}
              onClear={(row) => void onClear(row)}
              onDelete={(row) => void onDelete(row)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
