import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { subscribeAgentEvents } from "../../hooks/useAgentEventBus";
import { getApi } from "../../hooks/usePanelApi";
import type {
  ChangesetManualRow,
  ChangesetRevertResult,
  ChangesetRunDto,
  ChatTab,
} from "../../types/panel";
import {
  changesetRunSummary,
  runTimeline,
  sortRunsNewestFirst,
  statusLabel,
  type ChangeRow,
} from "../../utils/changesetGrouping";
import { formatSavedAt } from "../../utils/formatSavedAt";
import { ScopedCss, useScopedClass } from "../../utils/scopedCss";
import { prefixOffsets, visibleWindow } from "../../utils/virtualWindow";
import { basename } from "../../verse-editor/utils/isVerseFile";
import { Icons } from "../../icons/Icons";
import { DuckyAvatar } from "../ducky/DuckyAvatars";
import { shortModelLabel } from "../groupMemberHover";
import { Modal } from "../Modal";
import { ToolFileEditDiff } from "../ToolFileEditDiff";
import { ChangeRowView } from "./ChangeRowView";
import { JsonDiffView } from "./JsonDiffView";

/** Every run in the project, not just this chat's. The server clamps at 500. */
const RUN_LIMIT = 500;
const REFRESH_DEBOUNCE_MS = 400;

/** Any write, editor change, refusal, stop or revert redraws the ledger. */
const LIVE_EVENT_TYPES = new Set([
  "tool_done",
  "agent_stopped",
  "files_reverted",
  "file_guard",
  "editor_op",
]);

const RUN_HEADER_HEIGHT = 46;
const CHANGE_ROW_HEIGHT = 30;
const STEP_ROW_HEIGHT = 22;
/** Refusals carry a reason on a second line, clamped to one line so this holds. */
const REASON_ROW_HEIGHT = 18;
const OVERSCAN_PX = 320;

type KindFilter = "all" | "file" | "editor" | "blocked";
type RangeFilter = "all" | "24h" | "7d";

const RANGE_SECONDS: Record<RangeFilter, number> = { all: 0, "24h": 86400, "7d": 604800 };

interface DiffView {
  title: string;
  summary: string;
  before: string;
  after: string;
  /** Editor changes carry JSON; files carry text. */
  json: boolean;
  path: string;
}

interface TimelineItem {
  key: string;
  height: number;
  run: ChangesetRunDto;
  row: ChangeRow | null;
}

export interface ChangesViewProps {
  /** Resolves each run's ducky avatar: runs carry a name and model, not a style. */
  allChats?: ChatTab[];
  onOpenFile?: (path: string, name: string) => void;
}

function runDuckyKey(run: ChangesetRunDto): string {
  return (run.profile_id || run.ducky_name || run.conv_id || "").trim();
}

function runDuckyLabel(run: ChangesetRunDto): string {
  return (run.ducky_name || "").trim() || (run.source === "revert" ? "Revert" : "Ducky");
}

function matchesQuery(row: ChangeRow, needle: string): boolean {
  if (!needle) return true;
  const hay =
    row.kind === "file"
      ? `${row.path} ${row.op}`
      : `${row.slot} ${row.label} ${row.command} ${row.detail} ${row.verb}`;
  return `${hay} ${row.reason}`.toLowerCase().includes(needle);
}

function matchesKind(row: ChangeRow, kind: KindFilter): boolean {
  if (kind === "all") return true;
  if (kind === "blocked") return row.outcome !== "ok";
  if (row.outcome !== "ok") return false;
  return row.kind === kind;
}

/**
 * Project-wide ledger of everything the AI changed: files and editor work
 * together, in the order it happened, attributed to the ducky that did it, with
 * refused attempts kept in place rather than dropped.
 */
export function ChangesView({ allChats, onOpenFile }: ChangesViewProps) {
  const { confirm } = useConfirmModal();
  const [runs, setRuns] = useState<ChangesetRunDto[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyRun, setBusyRun] = useState("");
  const [diff, setDiff] = useState<DiffView | null>(null);
  const [manual, setManual] = useState<ChangesetManualRow[]>([]);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());

  const [duckyFilter, setDuckyFilter] = useState("all");
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [rangeFilter, setRangeFilter] = useState<RangeFilter>("all");
  const [query, setQuery] = useState("");

  const styleByConv = useMemo(() => {
    const map = new Map<string, string>();
    for (const chat of allChats ?? []) {
      if (chat.duckyStyle) map.set(chat.id, chat.duckyStyle);
    }
    return map;
  }, [allChats]);

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api?.list_changesets) {
      setRuns([]);
      setLoading(false);
      return;
    }
    try {
      const rows = await api.list_changesets("", "", RUN_LIMIT);
      setRuns(sortRunsNewestFirst(Array.isArray(rows) ? rows : []));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = subscribeAgentEvents((event) => {
      if (!LIVE_EVENT_TYPES.has(event.type)) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void refresh(), REFRESH_DEBOUNCE_MS);
    });
    return () => {
      if (timer) clearTimeout(timer);
      unsubscribe();
    };
  }, [refresh]);

  const duckies = useMemo(() => {
    const seen = new Map<string, string>();
    for (const run of runs) {
      const key = runDuckyKey(run);
      if (key && !seen.has(key)) seen.set(key, runDuckyLabel(run));
    }
    return [...seen.entries()].map(([id, label]) => ({ id, label }));
  }, [runs]);

  const toggleExpanded = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  /** Flat list of run headers and their rows, so one scroller virtualizes everything. */
  const items = useMemo<TimelineItem[]>(() => {
    const needle = query.trim().toLowerCase();
    const seconds = RANGE_SECONDS[rangeFilter];
    const cutoff = seconds ? Date.now() / 1000 - seconds : 0;
    const out: TimelineItem[] = [];
    for (const run of runs) {
      if (duckyFilter !== "all" && runDuckyKey(run) !== duckyFilter) continue;
      if (cutoff && (run.started || 0) < cutoff) continue;
      const rows = runTimeline(run).filter((row) => matchesKind(row, kindFilter) && matchesQuery(row, needle));
      if (rows.length === 0) continue;
      out.push({ key: `run:${run.run_id}`, height: RUN_HEADER_HEIGHT, run, row: null });
      for (const row of rows) {
        const steps = expanded.has(`${run.run_id}/${row.key}`) ? row.steps.length : 0;
        const reason = row.outcome !== "ok" && row.reason ? REASON_ROW_HEIGHT : 0;
        out.push({
          key: `${run.run_id}/${row.key}`,
          height: CHANGE_ROW_HEIGHT + steps * STEP_ROW_HEIGHT + reason,
          run,
          row,
        });
      }
    }
    return out;
  }, [runs, duckyFilter, kindFilter, rangeFilter, query, expanded]);

  /** Running total before each item, so variable-height rows still virtualize. */
  const offsets = useMemo(() => prefixOffsets(items.map((item) => item.height)), [items]);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [viewport, setViewport] = useState({ top: 0, height: 0 });

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const update = () => {
      const top = el.scrollTop;
      const height = el.clientHeight;
      setViewport((prev) => (prev.top === top && prev.height === height ? prev : { top, height }));
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [items.length]);

  const view = useMemo(
    () => visibleWindow(offsets, items.length, viewport.top, viewport.height || 800, OVERSCAN_PX),
    [items.length, offsets, viewport.top, viewport.height],
  );

  const virtualClass = useScopedClass("changes-virtual");

  const reportRevert = useCallback(
    async (label: string, result: ChangesetRevertResult, retry: () => Promise<ChangesetRevertResult | null>) => {
      if (result.errors.length) setError(result.errors.join("; "));
      setManual(result.manual ?? []);
      if (result.skipped_modified.length) {
        const names = result.skipped_modified.map((s) => basename(s.path)).join(", ");
        const force = await confirm({
          title: "Some files changed since",
          message: `${names} ${result.skipped_modified.length === 1 ? "was" : "were"} edited after this ${label}. Restore ${result.skipped_modified.length === 1 ? "it" : "them"} anyway? Later edits will be lost.`,
          confirmLabel: "Revert anyway",
          danger: true,
        });
        if (force) {
          const forced = await retry();
          if (forced?.errors.length) setError(forced.errors.join("; "));
          if (forced?.manual) setManual(forced.manual);
        }
      }
      await refresh();
    },
    [confirm, refresh],
  );

  const revertRun = useCallback(
    async (run: ChangesetRunDto) => {
      const api = getApi();
      if (!api?.revert_changeset) return;
      const summary = changesetRunSummary(run);
      const parts: string[] = [];
      if (summary.files) parts.push(`${summary.files} file${summary.files === 1 ? "" : "s"}`);
      if (summary.editor) parts.push(`${summary.editor} editor change${summary.editor === 1 ? "" : "s"}`);
      const what = parts.join(" and ") || "the changes";
      const ok = await confirm({
        title: "Revert run",
        message: `Undo ${what} ${runDuckyLabel(run)} made? Files go back to their content before the run and editor changes are put back where they were. The revert is recorded and can itself be reverted.`,
        confirmLabel: "Revert",
        danger: true,
      });
      if (!ok) return;
      setBusyRun(run.run_id);
      setError("");
      try {
        const result = await api.revert_changeset(run.run_id);
        await reportRevert("run", result, () => api.revert_changeset!(run.run_id, true));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyRun("");
      }
    },
    [confirm, reportRevert],
  );

  const revertRow = useCallback(
    async (run: ChangesetRunDto, row: ChangeRow) => {
      const api = getApi();
      if (!api?.revert_changeset_entry) return;
      const what = row.kind === "file" ? basename(row.path) : row.label;
      const ok = await confirm({
        title: row.kind === "file" ? "Revert file" : "Undo editor change",
        message:
          row.kind === "file"
            ? `Restore ${what} to its content before ${runDuckyLabel(run)} touched it?`
            : `Put ${what} back the way it was before ${runDuckyLabel(run)} changed it?`,
        confirmLabel: "Revert",
        danger: true,
      });
      if (!ok) return;
      setBusyRun(run.run_id);
      setError("");
      try {
        const result = await api.revert_changeset_entry(run.run_id, row.lastSeq);
        await reportRevert("change", result, () => api.revert_changeset_entry!(run.run_id, row.lastSeq, true));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyRun("");
      }
    },
    [confirm, reportRevert],
  );

  const openDiff = useCallback(async (run: ChangesetRunDto, row: ChangeRow) => {
    const api = getApi();
    if (!api?.get_changeset_entry_contents) return;
    try {
      const first = await api.get_changeset_entry_contents(run.run_id, row.firstSeq);
      const last =
        row.lastSeq === row.firstSeq ? first : await api.get_changeset_entry_contents(run.run_id, row.lastSeq);
      setDiff({
        title: row.kind === "file" ? basename(row.path) : row.label,
        summary: row.kind === "editor" ? row.detail : "",
        before: first.before ?? "",
        after: last.after ?? "",
        json: row.kind === "editor",
        path: row.kind === "file" ? row.path : row.slot,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const copyManual = useCallback(() => {
    const text = manual
      .map((row) => `${row.label || row.target} — ${row.command}${row.reason ? ` (${row.reason})` : ""}`)
      .join("\n");
    void navigator.clipboard?.writeText(text);
  }, [manual]);

  const visible = items.slice(view.start, view.end);

  return (
    <div className="changes-view">
      <ScopedCss
        selector={`.${virtualClass}`}
        rules={{
          "--changes-pad-top": `${view.padTop}px`,
          "--changes-pad-bottom": `${view.padBottom}px`,
        }}
      />
      <header className="changes-toolbar">
        <div className="changes-toolbar-title">
          <Icons.Clock />
          <span>Changes</span>
          <span className="changes-toolbar-count">
            {items.filter((i) => i.row).length} change{items.filter((i) => i.row).length === 1 ? "" : "s"}
          </span>
        </div>
        <div className="changes-filters">
          <select
            className="changes-filter"
            value={duckyFilter}
            onChange={(e) => setDuckyFilter(e.target.value)}
            aria-label="Filter by ducky"
          >
            <option value="all">All duckies</option>
            {duckies.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
              </option>
            ))}
          </select>
          <select
            className="changes-filter"
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value as KindFilter)}
            aria-label="Filter by kind"
          >
            <option value="all">Everything</option>
            <option value="file">Files</option>
            <option value="editor">Editor</option>
            <option value="blocked">Blocked</option>
          </select>
          <select
            className="changes-filter"
            value={rangeFilter}
            onChange={(e) => setRangeFilter(e.target.value as RangeFilter)}
            aria-label="Filter by time"
          >
            <option value="all">All time</option>
            <option value="24h">Last 24 hours</option>
            <option value="7d">Last 7 days</option>
          </select>
          <input
            className="changes-search"
            type="search"
            placeholder="Search paths and targets"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search changes"
          />
          <button type="button" className="changeset-btn" onClick={() => void refresh()}>
            Refresh
          </button>
        </div>
      </header>

      {error ? <div className="changeset-error">{error}</div> : null}

      {manual.length > 0 ? (
        <section className="changes-manual">
          <div className="changes-manual-head">
            <strong>Remove these by hand</strong>
            <span>Ducky could not work out how to undo them.</span>
            <button type="button" className="changeset-btn" onClick={copyManual}>
              Copy
            </button>
            <button type="button" className="changeset-btn" onClick={() => setManual([])}>
              Dismiss
            </button>
          </div>
          <ul>
            {manual.map((row) => (
              <li key={`${row.seq}:${row.path}`}>
                <span className="changes-manual-label">{row.label || row.target || row.path}</span>
                <span className="changes-manual-cmd">{row.command}</span>
                {row.reason ? <span className="changes-manual-reason">{row.reason}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="changes-scroll" ref={scrollRef}>
        {loading ? (
          <p className="changes-empty">Loading changes…</p>
        ) : items.length === 0 ? (
          <p className="changes-empty">
            {runs.length === 0
              ? "Nothing has been changed in this project yet. Every file a ducky writes and every actor, device or asset it touches shows up here."
              : "No changes match these filters."}
          </p>
        ) : (
          <div className={`changes-virtual ${virtualClass}`}>
            {visible.map((item) => {
              if (!item.row) {
                const run = item.run;
                const summary = changesetRunSummary(run);
                const busy = busyRun === run.run_id;
                const canRevert = !summary.running && !summary.reverted && !busy && run.source !== "revert";
                const meta = [shortModelLabel(run.model || ""), formatSavedAt(Math.floor(run.started))]
                  .filter(Boolean)
                  .join(" · ");
                const lane = run.lane?.write_allowed ?? [];
                return (
                  <div key={item.key} className="changes-run-head">
                    <DuckyAvatar styleId={styleByConv.get(run.conv_id)} size={20} />
                    <span className="changes-run-name">{runDuckyLabel(run)}</span>
                    <span className="changes-run-meta" title={run.run_id}>
                      {meta}
                    </span>
                    <span className={`changeset-status changeset-status--${run.status}`}>
                      {statusLabel(run.status)}
                    </span>
                    {lane.length > 0 ? (
                      <span className="changeset-badge changeset-badge--lane" title={lane.join("\n")}>
                        lane: {lane[0]}
                        {lane.length > 1 ? ` +${lane.length - 1}` : ""}
                      </span>
                    ) : null}
                    {summary.blocked > 0 ? (
                      <span className="changeset-badge changeset-badge--blocked">
                        {summary.blocked} blocked
                      </span>
                    ) : null}
                    <button
                      type="button"
                      className="changeset-btn"
                      disabled={!canRevert}
                      title={
                        run.source === "revert"
                          ? "This is itself a revert"
                          : summary.running
                            ? "Wait for the run to finish"
                            : "Undo everything this run changed"
                      }
                      onClick={() => void revertRun(run)}
                    >
                      Revert run
                    </button>
                  </div>
                );
              }
              return (
                <ChangeRowView
                  key={item.key}
                  run={item.run}
                  row={item.row}
                  busy={busyRun === item.run.run_id}
                  expanded={expanded.has(item.key)}
                  onToggleExpanded={() => toggleExpanded(item.key)}
                  onOpenFile={onOpenFile}
                  onDiff={() => void openDiff(item.run, item.row!)}
                  onRevert={() => void revertRow(item.run, item.row!)}
                />
              );
            })}
          </div>
        )}
      </div>

      {diff ? (
        <Modal open onClose={() => setDiff(null)} title={diff.title} width={780} zIndex={100020}>
          <div className="changeset-diff-modal">
            {diff.json ? (
              <JsonDiffView before={diff.before} after={diff.after} summary={diff.summary} />
            ) : (
              <ToolFileEditDiff
                edit={{
                  path: diff.path,
                  before: diff.before,
                  after: diff.after,
                  linesAdded: 0,
                  linesRemoved: 0,
                  kind: "write",
                }}
                onOpenFile={onOpenFile}
                defaultExpanded
              />
            )}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
