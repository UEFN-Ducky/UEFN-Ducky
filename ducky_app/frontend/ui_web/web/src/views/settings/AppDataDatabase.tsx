import { useCallback, useEffect, useMemo, useState } from "react";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import type { StoreActionResult, StoreOverview, StoreTable, StoreTablePreview } from "../../types/panel";
import { formatAppDataBytes } from "./AppDataTab";
import { GeneralSectionHeader } from "./GeneralSectionHeader";

/**
 * Settings → General → App Data → Database (ADR 0003).
 *
 * One page for the whole store: health + size, every table with live row
 * counts and a masked row preview, snapshots (make / restore / delete), the
 * legacy folder left by an upgrade, and what was imported when. Every action
 * goes through `store_action`, so the page never holds SQL.
 */

const GROUP_ORDER: StoreTable["group"][] = ["chats", "settings", "changes", "plans", "verse", "logs"];

function DatabaseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </svg>
  );
}

function when(ts: number | undefined | null): string {
  if (!ts) return "never";
  const d = new Date(ts * 1000);
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)} min ago`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)} h ago`;
  return d.toLocaleString();
}

function cell(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function TablePreview({ table, refreshKey }: { table: StoreTable; refreshKey: number }) {
  const [page, setPage] = useState<StoreTablePreview | null>(null);
  const [offset, setOffset] = useState(0);
  const limit = 25;

  useEffect(() => {
    const api = getApi();
    if (!api?.store_table_preview) return;
    let live = true;
    void api.store_table_preview(table.name, limit, offset).then((res) => {
      if (live) setPage(res);
    });
    return () => {
      live = false;
    };
  }, [table.name, offset, refreshKey]);

  if (!page) return <p className="appdata-empty">Reading rows…</p>;
  if (!page.ok) return <p className="general-tab-section-note general-tab-section-note--error">{page.error}</p>;
  if (page.total === 0) return <p className="appdata-empty">No rows.</p>;
  const last = Math.min(offset + limit, page.total);
  return (
    <div className="dbtab-preview">
      <div className="dbtab-preview-scroll">
        <table className="dbtab-table">
          <thead>
            <tr>
              {page.columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {page.rows.map((row, i) => (
              <tr key={i}>
                {page.columns.map((c) => (
                  <td key={c} title={cell(row[c])}>
                    {cell(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="dbtab-preview-pager">
        <span className="appdata-tab-counts">
          Rows {offset + 1}–{last} of {page.total}
        </span>
        <button
          type="button"
          className="settings-btn appdata-row-btn"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - limit))}
        >
          Newer
        </button>
        <button
          type="button"
          className="settings-btn appdata-row-btn"
          disabled={last >= page.total}
          onClick={() => setOffset(offset + limit)}
        >
          Older
        </button>
      </div>
    </div>
  );
}

function TableRow({
  table: t,
  busy,
  refreshKey,
  onExport,
  onClear,
}: {
  table: StoreTable;
  busy: boolean;
  refreshKey: number;
  onExport: () => void;
  onClear: () => void;
}) {
  // Rows are only read once the table is opened — 27 tables must not fire 27
  // queries when the tab mounts.
  const [open, setOpen] = useState(false);
  return (
    <details
      className="appearance-details appdata-details dbtab-table-details"
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>
        <div className="appdata-summary-copy">
          <div className="appearance-tab-details-title">
            {t.label} <code className="dbtab-code">{t.name}</code>
          </div>
          <div className="appdata-summary-meta">
            {t.rows < 0 ? "unreadable" : `${t.rows.toLocaleString()} rows`}
            <span className="appdata-dot">·</span>
            {t.description}
          </div>
        </div>
        <div className="appearance-details-summary-actions">
          <div className="appdata-row-actions">
            <button
              type="button"
              className="settings-btn appdata-row-btn"
              disabled={busy || t.rows <= 0}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onExport();
              }}
            >
              Export
            </button>
            {t.clearable ? (
              <button
                type="button"
                className="settings-btn appdata-row-btn general-tab-btn-danger"
                disabled={busy || t.rows <= 0}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onClear();
                }}
              >
                Clear
              </button>
            ) : (
              <span className="appdata-protected">Protected</span>
            )}
          </div>
          <div className="appearance-tab-details-chevron">
            <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" fill="none" strokeWidth="2" aria-hidden>
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </div>
        </div>
      </summary>
      <div className="appearance-details-content appdata-details-content">
        {open ? <TablePreview table={t} refreshKey={refreshKey} /> : null}
      </div>
    </details>
  );
}

export function AppDataDatabase({ onOpenRel }: { onOpenRel: (rel: string) => void }) {
  const { confirm, alert } = useConfirmModal();
  const [overview, setOverview] = useState<StoreOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const reload = useCallback(async () => {
    const api = getApi();
    if (!api?.store_overview) return;
    setLoading(true);
    try {
      setOverview(await api.store_overview());
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setOverview((prev) => ({
        ...(prev || emptyOverview()),
        error: err instanceof Error ? err.message : "Could not read the database.",
      }));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => onApiReady(() => void reload()), [reload]);

  const run = useCallback(
    async (key: string, action: string, arg = "", describe?: (r: StoreActionResult) => string) => {
      const api = getApi();
      if (!api?.store_action) return;
      setBusy(key);
      setNote("");
      try {
        const result = await api.store_action(action, arg);
        if (!result.ok) {
          await alert({ title: "Database action failed", message: result.error || result.result || "Unknown error." });
        } else if (describe) {
          setNote(describe(result));
        }
        await reload();
      } finally {
        setBusy(null);
      }
    },
    [alert, reload],
  );

  const groups = useMemo(() => {
    const byGroup = new Map<StoreTable["group"], StoreTable[]>();
    for (const t of overview?.tables || []) {
      const list = byGroup.get(t.group) || [];
      list.push(t);
      byGroup.set(t.group, list);
    }
    return GROUP_ORDER.filter((g) => byGroup.has(g)).map((g) => ({
      id: g,
      label: byGroup.get(g)![0].group_label,
      tables: byGroup.get(g)!,
      rows: byGroup.get(g)!.reduce((a, t) => a + Math.max(0, t.rows), 0),
    }));
  }, [overview]);

  const totalRows = useMemo(() => (overview?.tables || []).reduce((a, t) => a + Math.max(0, t.rows), 0), [overview]);
  const filesBackends = useMemo(
    () => Object.entries(overview?.backends || {}).filter(([, mode]) => mode === "files").map(([s]) => s),
    [overview],
  );
  const integrityOk = overview?.integrity?.result === "ok";
  const anyBusy = busy != null;

  const onClearTable = useCallback(
    async (t: StoreTable) => {
      if (
        !(await confirm({
          title: `Clear ${t.label}?`,
          message: `Delete all ${t.rows} rows in “${t.label}”. ${t.description}`,
          confirmLabel: "Clear rows",
          danger: true,
        }))
      ) {
        return;
      }
      await run(`clear:${t.name}`, "clear_table", t.name, (r) =>
        `Cleared ${r.removed ?? 0} rows from ${t.label}${r.removed_files ? ` and ${r.removed_files} files` : ""}.`,
      );
    },
    [confirm, run],
  );

  const onRestore = useCallback(
    async (name: string) => {
      if (
        !(await confirm({
          title: "Restore this snapshot?",
          message: `The current database is set aside and “${name}” takes its place the next time UEFN Ducky starts. Anything saved after the snapshot was taken will not be in it.`,
          confirmLabel: "Stage restore",
          danger: true,
        }))
      ) {
        return;
      }
      await run(`restore:${name}`, "restore", name, () => "Restore staged. Restart UEFN Ducky to apply it.");
    },
    [confirm, run],
  );

  const onRetireLegacy = useCallback(async () => {
    if (
      !(await confirm({
        title: "Delete legacy files now?",
        message:
          "These are the pre-database files that were imported on upgrade. The database already holds everything in them; they are only kept as a fallback for three clean starts.",
        confirmLabel: "Delete legacy files",
        danger: true,
      }))
    ) {
      return;
    }
    await run("legacy", "retire_legacy", "", (r) => `Deleted ${r.removed ?? 0} legacy files (${formatAppDataBytes(r.bytes || 0)}).`);
  }, [confirm, run]);

  const ov = overview;
  return (
    <div className="dbtab" data-testid="appdata-database">
      <section className="general-tab-section">
        <GeneralSectionHeader
          icon={<DatabaseIcon />}
          title="ducky.db"
          description="One SQLite file holds chats, settings, keys, the change ledger, plans, memory, logs and caches. Files that belong on disk (plugins, skill packs, captures, the browser profile) stay in the folders beside it."
        />
        <p className="appdata-tab-path" title={ov?.path}>
          {ov?.path || "%LOCALAPPDATA%\\UEFN-Ducky\\ducky.db"}
        </p>
        {ov?.error ? <p className="general-tab-section-note general-tab-section-note--error">{ov.error}</p> : null}
        <div className="dbtab-stats">
          <div className="dbtab-stat">
            <span className="dbtab-stat-value">{loading && !ov ? "…" : formatAppDataBytes(ov?.size_bytes || 0)}</span>
            <span className="dbtab-stat-label">database{ov?.wal_bytes ? ` · ${formatAppDataBytes(ov.wal_bytes)} pending log` : ""}</span>
          </div>
          <div className="dbtab-stat">
            <span className="dbtab-stat-value">{ov ? totalRows.toLocaleString() : "…"}</span>
            <span className="dbtab-stat-label">rows in {ov?.tables.length || 0} tables</span>
          </div>
          <div className="dbtab-stat">
            <span className={`dbtab-stat-value ${ov?.integrity ? (integrityOk ? "is-ok" : "is-bad") : ""}`}>
              {ov?.integrity ? (integrityOk ? "OK" : "Problem") : "Unchecked"}
            </span>
            <span className="dbtab-stat-label">integrity · {when(ov?.integrity?.ts)}</span>
          </div>
          <div className="dbtab-stat">
            <span className="dbtab-stat-value">
              v{ov?.schema_version ?? 0}
              {ov && ov.schema_version !== ov.head_version ? ` / ${ov.head_version}` : ""}
            </span>
            <span className="dbtab-stat-label">
              schema · SQLite {ov?.sqlite_version || "?"} · {ov?.journal_mode || "wal"}
            </span>
          </div>
        </div>
        {ov?.restore_pending ? (
          <p className="dbtab-banner dbtab-banner--warn">
            A snapshot restore is staged. Restart UEFN Ducky to apply it, or{" "}
            <button type="button" className="dbtab-link" disabled={anyBusy} onClick={() => void run("cancel", "cancel_restore")}>
              cancel it
            </button>
            .
          </p>
        ) : null}
        {ov?.last_restore ? <p className="appdata-tab-note">Last automatic restore: {ov.last_restore}</p> : null}
        {filesBackends.length ? (
          <p className="dbtab-banner dbtab-banner--warn">
            Rollback switch active: {filesBackends.join(", ")} still use the old files (DUCKY_STORE_BACKEND).
          </p>
        ) : null}
        <div className="appdata-tab-toolbar log-errors-actions no-drag">
          <button type="button" className="settings-btn" disabled={anyBusy} onClick={() => void run("check", "check", "", (r) => `Integrity check: ${r.result}.`)}>
            {busy === "check" ? "Checking…" : "Check integrity"}
          </button>
          <button type="button" className="settings-btn" disabled={anyBusy} onClick={() => void run("snapshot", "snapshot", "", (r) => `Snapshot ${r.snapshot} written.`)}>
            {busy === "snapshot" ? "Copying…" : "Snapshot now"}
          </button>
          <button
            type="button"
            className="settings-btn"
            disabled={anyBusy}
            onClick={() =>
              void run("vacuum", "vacuum", "", (r) => `Compacted ${formatAppDataBytes(r.before || 0)} → ${formatAppDataBytes(r.after || 0)}.`)
            }
          >
            {busy === "vacuum" ? "Compacting…" : "Compact"}
          </button>
          <button type="button" className="settings-btn" disabled={anyBusy} onClick={() => void run("optimize", "optimize", "", () => "Indexes optimized.")}>
            Optimize indexes
          </button>
          <button type="button" className="settings-btn" disabled={anyBusy} onClick={() => void run("blobs", "sweep_blobs", "", (r) => `Removed ${r.removed ?? 0} unreferenced blobs.`)}>
            Sweep blobs
          </button>
          <button type="button" className="settings-btn" disabled={anyBusy} onClick={() => onOpenRel("")}>
            Open folder
          </button>
          <button type="button" className="settings-btn" disabled={anyBusy || loading} onClick={() => void reload()}>
            Refresh
          </button>
        </div>
        {note ? <p className="appdata-tab-note">{note}</p> : null}
      </section>

      <section className="general-tab-section">
        <h3 className="dbtab-h3">Tables</h3>
        <div className="appdata-list">
          {groups.map((g) => (
            <details key={g.id} className="appearance-details appdata-details" open={g.id === "chats"}>
              <summary>
                <div className="appdata-summary-copy">
                  <div className="appearance-tab-details-title">{g.label}</div>
                  <div className="appdata-summary-meta">
                    {g.rows.toLocaleString()} rows
                    <span className="appdata-dot">·</span>
                    {g.tables.length} tables
                  </div>
                </div>
                <div className="appearance-details-summary-actions">
                  <div className="appearance-tab-details-chevron">
                    <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" fill="none" strokeWidth="2" aria-hidden>
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </div>
                </div>
              </summary>
              <div className="appearance-details-content appdata-details-content">
                <div className="appdata-children">
                  {g.tables.map((t) => (
                    <TableRow
                      key={t.name}
                      table={t}
                      busy={anyBusy}
                      refreshKey={refreshKey}
                      onExport={() =>
                        void run(`export:${t.name}`, "export_table", t.name, (r) => `Exported ${r.rows ?? 0} rows to ${r.path}.`)
                      }
                      onClear={() => void onClearTable(t)}
                    />
                  ))}
                </div>
              </div>
            </details>
          ))}
          {!loading && groups.length === 0 ? <p className="appdata-empty">No tables to show.</p> : null}
        </div>
      </section>

      <section className="general-tab-section">
        <h3 className="dbtab-h3">Snapshots</h3>
        <p className="appdata-item-desc">
          Consistent copies of the whole database. One is taken every day at startup and before every schema upgrade; the newest three are kept.
        </p>
        {ov && ov.snapshots.length === 0 ? <p className="appdata-empty">No snapshots yet.</p> : null}
        {(ov?.snapshots || []).map((s) => (
          <div key={s.name} className="appdata-file-row">
            <div className="appdata-file-row-copy">
              <div className="appdata-file-row-title">{s.name}</div>
              <div className="appdata-file-row-meta">
                {formatAppDataBytes(s.bytes)}
                <span className="appdata-dot">·</span>
                {when(s.ts)}
              </div>
            </div>
            <div className="appdata-row-actions">
              <button type="button" className="settings-btn appdata-row-btn" disabled={anyBusy} onClick={() => void onRestore(s.name)}>
                Restore
              </button>
              <button
                type="button"
                className="settings-btn appdata-row-btn general-tab-btn-danger"
                disabled={anyBusy}
                onClick={() => void run(`del:${s.name}`, "delete_snapshot", s.name, () => `Deleted ${s.name}.`)}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
        {ov?.snapshots.length ? (
          <div className="appdata-tab-toolbar log-errors-actions no-drag">
            <button type="button" className="settings-btn" disabled={anyBusy} onClick={() => onOpenRel("snapshots")}>
              Open snapshots folder
            </button>
          </div>
        ) : null}
      </section>

      <section className="general-tab-section">
        <h3 className="dbtab-h3">Upgrade leftovers</h3>
        {ov?.legacy ? (
          <>
            <p className="appdata-item-desc">
              The files this app used before the database were imported and moved here: {formatAppDataBytes(ov.legacy.bytes)} in{" "}
              {ov.legacy.files} files. They are deleted automatically after {ov.clean_boots_needed} clean starts (
              {Math.min(ov.clean_boots, ov.clean_boots_needed)} of {ov.clean_boots_needed} so far).
            </p>
            <div className="appdata-children">
              {ov.legacy.stores.map((s) => (
                <div key={s.name} className="appdata-file-row">
                  <div className="appdata-file-row-copy">
                    <div className="appdata-file-row-title">{s.name}</div>
                    <div className="appdata-file-row-meta">
                      {formatAppDataBytes(s.bytes)}
                      <span className="appdata-dot">·</span>
                      {s.files} files
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <div className="appdata-tab-toolbar log-errors-actions no-drag">
              <button type="button" className="settings-btn" disabled={anyBusy} onClick={() => onOpenRel("legacy")}>
                Open
              </button>
              <button type="button" className="settings-btn general-tab-btn-danger" disabled={anyBusy} onClick={() => void onRetireLegacy()}>
                {busy === "legacy" ? "Deleting…" : "Delete now"}
              </button>
            </div>
          </>
        ) : (
          <p className="appdata-empty">Nothing left over — every old file has been imported and cleaned up.</p>
        )}
        {ov?.importers.length ? (
          <details className="appearance-details appdata-details dbtab-importers">
            <summary>
              <div className="appdata-summary-copy">
                <div className="appearance-tab-details-title">Import log</div>
                <div className="appdata-summary-meta">{ov.importers.length} stores imported into this database</div>
              </div>
              <div className="appearance-details-summary-actions">
                <div className="appearance-tab-details-chevron">
                  <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" fill="none" strokeWidth="2" aria-hidden>
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </div>
              </div>
            </summary>
            <div className="appearance-details-content appdata-details-content">
              <div className="dbtab-preview-scroll">
                <table className="dbtab-table">
                  <thead>
                    <tr>
                      <th>store</th>
                      <th>when</th>
                      <th>report</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ov.importers.map((imp) => (
                      <tr key={imp.name}>
                        <td>{imp.name}</td>
                        <td>{when(imp.ts)}</td>
                        <td title={cell(imp.report)}>{cell(imp.report)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="appdata-tab-toolbar log-errors-actions no-drag">
                <button type="button" className="settings-btn" disabled={anyBusy} onClick={() => void run("import", "import_now", "", () => "Import pass finished.")}>
                  Run import pass again
                </button>
              </div>
            </div>
          </details>
        ) : null}
      </section>
    </div>
  );
}

function emptyOverview(): StoreOverview {
  return {
    path: "",
    exists: false,
    sqlite_version: "",
    backends: {},
    size_bytes: 0,
    wal_bytes: 0,
    schema_version: 0,
    head_version: 0,
    tables: [],
    snapshots: [],
    legacy: null,
    importers: [],
    integrity: null,
    last_restore: null,
    restore_pending: false,
    clean_boots: 0,
    clean_boots_needed: 3,
    error: "",
  };
}
