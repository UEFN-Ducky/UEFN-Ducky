import { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense, type MouseEvent, type ReactNode } from "react";

import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { subscribeAgentEvents } from "../../hooks/useAgentEventBus";
import { getApi } from "../../hooks/usePanelApi";
import { useRunningAgents } from "../../hooks/useRunningAgents";
import type {
  ChangesetBlockedBy,
  ChangesetManualRow,
  ChangesetRevertResult,
  ChangesetRunDto,
  ChatTab,
} from "../../types/panel";
import {
  changesetRunSummary,
  clusterRowsByProgram,
  formatSmartRevertBrief,
  programLabel,
  programOfRow,
  revertHost,
  revertFailureMessage,
  rowRedoTarget,
  runAgentLive,
  runDisplayStatus,
  runRedoTargetId,
  runTimeline,
  sortChangeRows,
  sortRuns,
  sortRunsNewestFirst,
  type ChangeRow,
  type ChangeSortDir,
  type ChangeSortKey,
} from "../../utils/changesetGrouping";
import { requestChangesFocus, subscribeChangesFocus, takeChangesFocus } from "../../utils/changesFocus";
import { getAskAiHandlers } from "../../contexts/askAiHandlersRef";
import { formatSavedAt } from "../../utils/formatSavedAt";
import { ScopedCss, useScopedClass } from "../../utils/scopedCss";
import { contextMenuSeparator } from "../../utils/sidebarContextMenuItems";
import { prefixOffsets, visibleWindow } from "../../utils/virtualWindow";
import { basename } from "../../verse-editor/utils/isVerseFile";
import { Icons } from "../../icons/Icons";
import { DuckyAvatar } from "../ducky/DuckyAvatars";
import { shortModelLabel } from "../groupMemberHover";
import { ChoiceDropdown } from "../ChoiceDropdown";
import { ContextMenu, useContextMenuState, type ContextMenuItem } from "../ContextMenu";
import { Modal } from "../Modal";
import { ChangeRowView } from "./ChangeRowView";
import { JsonDiffView } from "./JsonDiffView";

/** Every run in the project, not just this chat's. The server clamps at 500. */
const RUN_LIMIT = 500;
const REFRESH_DEBOUNCE_MS = 400;
const ToolFileEditDiff = lazy(() =>
  import("../ToolFileEditDiff").then((m) => ({ default: m.ToolFileEditDiff })),
);

/** Any write, editor change, refusal, stop or revert redraws the ledger. */
const LIVE_EVENT_TYPES = new Set([
  "tool_done",
  "agent_stopped",
  "files_reverted",
  "file_guard",
  "editor_op",
  "changeset_row",
]);

const RUN_HEADER_HEIGHT = 46;
const CHANGE_ROW_HEIGHT = 30;
const STEP_ROW_HEIGHT = 22;
const PROGRAM_HEADER_HEIGHT = 22;
const OVERSCAN_PX = 320;

type KindFilter = "all" | "file" | "editor" | "blocked";

const KIND_OPTIONS = [
  { value: "all", label: "Everything" },
  { value: "file", label: "Files" },
  { value: "editor", label: "Editor" },
  { value: "blocked", label: "Blocked" },
];

interface DiffSnapshot {
  seq: number;
  before: string;
  after: string;
  summary: string;
  reverted: boolean;
}

interface DiffView {
  title: string;
  json: boolean;
  path: string;
  snapshots: DiffSnapshot[];
  /** -1 = net across every write; 0..n-1 = that write. */
  cursor: number;
  run: ChangesetRunDto;
  row: ChangeRow;
}

function diffPair(diff: DiffView): { before: string; after: string; summary: string } {
  const snaps = diff.snapshots;
  if (!snaps.length) return { before: "", after: "", summary: "" };
  if (diff.cursor < 0) {
    return {
      before: snaps[0].before,
      after: snaps[snaps.length - 1].after,
      summary: snaps[snaps.length - 1].summary,
    };
  }
  const snap = snaps[diff.cursor] ?? snaps[0];
  return { before: snap.before, after: snap.after, summary: snap.summary };
}

interface TimelineItem {
  key: string;
  height: number;
  run: ChangesetRunDto;
  row: ChangeRow | null;
  program?: string;
}

type MenuTarget =
  | { kind: "run"; run: ChangesetRunDto }
  | { kind: "row"; run: ChangesetRunDto; row: ChangeRow };

export interface ChangesViewProps {
  /** Resolves each run's ducky avatar: runs carry a name and model, not a style. */
  allChats?: ChatTab[];
  onOpenFile?: (path: string, name: string) => void;
  /** Open the chat tab that made this run. */
  onOpenChat?: (chat: ChatTab) => void;
  /** Limit the ledger to one chat. Empty = whole project. */
  convId?: string;
  /** Limit the ledger to every member of this group hub. */
  groupId?: string;
  /** Limit to every current chat of this library ducky type. */
  profileId?: string;
  profileName?: string;
  /** Hide the ducky dropdown (the list is already one chat). */
  hideDuckyFilter?: boolean;
  /** Render diffs inside this node instead of over the whole app. */
  modalContainer?: HTMLElement | null;
}

function runDuckyKey(run: ChangesetRunDto): string {
  return (run.profile_id || run.ducky_name || run.conv_id || "").trim();
}

function runMatchesProfile(
  run: ChangesetRunDto,
  profileId: string,
  profileName: string,
  chats?: ChatTab[],
): boolean {
  const pid = profileId.trim();
  const name = profileName.trim().toLowerCase();
  if (!pid && !name) return true;
  if (pid && (run.profile_id || "").trim() === pid) return true;
  const chat = runChat(run, chats);
  if (pid && (chat?.profileId || "").trim() === pid) return true;
  if (name && (run.ducky_name || "").trim().toLowerCase() === name) return true;
  const chatName = (chat?.duckyName || "").trim().toLowerCase();
  return Boolean(name && chatName && chatName === name);
}

function runChat(run: ChangesetRunDto, chats?: ChatTab[]): ChatTab | undefined {
  const id = (run.conv_id || "").trim();
  return id ? chats?.find((c) => c.id === id) : undefined;
}

function runDuckyLabel(run: ChangesetRunDto, chats?: ChatTab[]): string {
  // The tab title is what the user named this agent. The run's ducky_name is the
  // library profile (Verse Coder) and often disagrees with the tab (Animation Engineer).
  const fromTab = (runChat(run, chats)?.name || "").trim();
  return fromTab || (run.ducky_name || "").trim() || (run.source === "revert" ? "Revert" : "Ducky");
}

function chatWasDeleted(run: ChangesetRunDto, chats?: ChatTab[]): boolean {
  if (!chats?.length) return false;
  const id = (run.conv_id || "").trim();
  return Boolean(id) && !chats.some((c) => c.id === id);
}

function matchesQuery(row: ChangeRow, needle: string): boolean {
  if (!needle) return true;
  const hay =
    row.kind === "file"
      ? `${row.path} ${row.op}`
      : `${row.slot} ${row.label} ${row.command} ${row.detail} ${row.verb}`;
  return `${hay} ${row.reason}`.toLowerCase().includes(needle);
}

function matchesRunQuery(run: ChangesetRunDto, needle: string, chats?: ChatTab[]): boolean {
  if (!needle) return false;
  const hay = `${runDuckyLabel(run, chats)} ${run.ducky_name || ""} ${run.model || ""} ${run.status} ${run.source || ""}`;
  return hay.toLowerCase().includes(needle);
}

function matchesKind(row: ChangeRow, kind: KindFilter): boolean {
  if (kind === "all") return true;
  if (kind === "blocked") return row.outcome !== "ok";
  if (row.outcome !== "ok") return false;
  return row.kind === kind;
}

function matchesProgram(row: ChangeRow, program: string): boolean {
  if (program === "all") return true;
  return programOfRow(row) === program;
}

function rangeIds(ordered: string[], from: string, to: string): string[] {
  const a = ordered.indexOf(from);
  const b = ordered.indexOf(to);
  if (b < 0) return from ? [from] : [];
  if (a < 0) return [to];
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  return ordered.slice(lo, hi + 1);
}

function sortMark(active: boolean, dir: ChangeSortDir): string {
  if (!active) return "";
  return dir === "asc" ? " ↑" : " ↓";
}

function IconBtn({
  title,
  hint,
  disabled,
  pressed,
  onClick,
  children,
}: {
  title: string;
  hint?: string;
  disabled?: boolean;
  pressed?: boolean;
  onClick: (e: MouseEvent<HTMLButtonElement>) => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className="changeset-btn changeset-btn--icon"
      title={hint || title}
      aria-label={title}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

/**
 * Project-wide ledger of everything the AI changed: files and editor work
 * together, in the order it happened, attributed to the ducky that did it, with
 * refused attempts kept in place rather than dropped.
 */
export function ChangesView({
  allChats,
  onOpenFile,
  onOpenChat,
  convId = "",
  groupId = "",
  profileId = "",
  profileName = "",
  hideDuckyFilter = false,
  modalContainer = null,
}: ChangesViewProps) {
  const { confirm } = useConfirmModal();
  const runningIds = useRunningAgents();
  const [runs, setRuns] = useState<ChangesetRunDto[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyRun, setBusyRun] = useState("");
  const [diff, setDiff] = useState<DiffView | null>(null);
  /** Blocked/failed row opened for its reason + attempts. */
  const [detail, setDetail] = useState<{ run: ChangesetRunDto; row: ChangeRow } | null>(null);
  const [manual, setManual] = useState<ChangesetManualRow[]>([]);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());

  const [duckyFilter, setDuckyFilter] = useState("all");
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [programFilter, setProgramFilter] = useState("all");
  const [sortKey, setSortKey] = useState<ChangeSortKey>("time");
  const [sortDir, setSortDir] = useState<ChangeSortDir>("asc");
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [selectAnchor, setSelectAnchor] = useState("");
  const [blocker, setBlocker] = useState<ChangesetBlockedBy | null>(null);
  const [smartPicker, setSmartPicker] = useState<{
    run: ChangesetRunDto;
    row: ChangeRow;
    laterWriter?: { name: string; runId: string; seq: number } | null;
  } | null>(null);
  const [focusHint, setFocusHint] = useState<{ runId: string; seq: number } | null>(null);
  const [focusKey, setFocusKey] = useState("");
  const [forcedOpen, setForcedOpen] = useState<ReadonlySet<string>>(new Set());
  const { menu, open: openMenu, close: closeMenu } = useContextMenuState<MenuTarget>();

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
      const rows = await api.list_changesets(convId, groupId, RUN_LIMIT, showArchived);
      setRuns(sortRunsNewestFirst(Array.isArray(rows) ? rows : []));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [convId, groupId, showArchived]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (searchOpen) searchRef.current?.focus();
  }, [searchOpen]);

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
      if (key && !seen.has(key)) seen.set(key, runDuckyLabel(run, allChats));
    }
    return [...seen.entries()].map(([id, label]) => ({ id, label }));
  }, [allChats, runs]);

  const toggleSort = useCallback((key: ChangeSortKey) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
        return prev;
      }
      setSortDir("asc");
      return key;
    });
  }, []);

  const toggleExpanded = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  /** Runs the user flipped away from the default (newest open, the rest folded). */
  const [toggledRuns, setToggledRuns] = useState<ReadonlySet<string>>(new Set());
  const toggleRun = useCallback((runId: string) => {
    setToggledRuns((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
  }, []);

  /** Flat list of run headers and their rows, so one scroller virtualizes everything.
   *  Each run is an accordion: a folded run contributes only its header. */
  const { items, openRuns } = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const out: TimelineItem[] = [];
    const open = new Set<string>();
    let shown = 0;
    const ordered = sortRuns(runs, sortKey, sortDir, (r) => runDuckyLabel(r, allChats).toLowerCase());
    for (const run of ordered) {
      if (!runMatchesProfile(run, profileId, profileName, allChats)) continue;
      if (duckyFilter !== "all" && runDuckyKey(run) !== duckyFilter) continue;
      const runHit = matchesRunQuery(run, needle, allChats);
      const rows = sortChangeRows(
        runTimeline(run).filter(
          (row) =>
            matchesKind(row, kindFilter) &&
            matchesProgram(row, programFilter) &&
            (runHit || matchesQuery(row, needle)),
        ),
        sortKey,
        sortDir,
      );
      if (rows.length === 0) continue;
      out.push({ key: `run:${run.run_id}`, height: RUN_HEADER_HEIGHT, run, row: null });
      const isOpen = forcedOpen.has(run.run_id) || (shown === 0) !== toggledRuns.has(run.run_id);
      shown += 1;
      if (!isOpen) continue;
      open.add(run.run_id);
      const groupByProgram = sortKey === "time";
      const grouped = groupByProgram ? clusterRowsByProgram(rows) : rows;
      let lastProgram = "";
      const multiProgram = groupByProgram && new Set(grouped.map(programOfRow)).size > 1;
      for (const row of grouped) {
        const program = programOfRow(row);
        if (multiProgram && program !== lastProgram) {
          lastProgram = program;
          out.push({
            key: `${run.run_id}/program:${program}`,
            height: PROGRAM_HEADER_HEIGHT,
            run,
            row: null,
            program,
          });
        }
        const steps = expanded.has(`${run.run_id}/${row.key}`) ? row.steps.length : 0;
        out.push({
          key: `${run.run_id}/${row.key}`,
          height: CHANGE_ROW_HEIGHT + steps * STEP_ROW_HEIGHT,
          run,
          row,
        });
      }
    }
    return { items: out, openRuns: open };
  }, [allChats, runs, duckyFilter, kindFilter, programFilter, profileId, profileName, query, sortKey, sortDir, expanded, toggledRuns, forcedOpen]);

  const programs = useMemo(() => {
    const seen = new Set<string>();
    for (const run of runs) {
      for (const row of runTimeline(run)) seen.add(programOfRow(row));
    }
    return [...seen];
  }, [runs]);

  const runOrder = useMemo(
    () => items.filter((item) => !item.row && !item.program).map((item) => item.run.run_id),
    [items],
  );

  useEffect(() => {
    setSelected((prev) => {
      if (!prev.size) return prev;
      const next = new Set([...prev].filter((id) => runOrder.includes(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [runOrder]);

  const applyRunSelect = useCallback(
    (runId: string, opts: { ctrl: boolean; shift: boolean }) => {
      setSelected((prev) => {
        if (opts.shift) {
          const from = selectAnchor && runOrder.includes(selectAnchor) ? selectAnchor : runId;
          return new Set(rangeIds(runOrder, from, runId));
        }
        if (opts.ctrl) {
          const next = new Set(prev);
          if (next.has(runId)) next.delete(runId);
          else next.add(runId);
          return next;
        }
        return new Set([runId]);
      });
      if (!opts.shift) setSelectAnchor(runId);
    },
    [runOrder, selectAnchor],
  );

  const onRunHeadClick = useCallback(
    (e: MouseEvent<HTMLDivElement>, runId: string) => {
      const ctrl = e.ctrlKey || e.metaKey;
      const shift = e.shiftKey;
      if (ctrl || shift) {
        e.preventDefault();
        applyRunSelect(runId, { ctrl, shift });
        return;
      }
      if (selected.size > 1) {
        toggleRun(runId);
        return;
      }
      setSelected(new Set());
      setSelectAnchor(runId);
      toggleRun(runId);
    },
    [applyRunSelect, selected.size, toggleRun],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(new Set());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
      let next = result;
      setManual(result.manual ?? []);
      if ((result.blocked_by ?? []).length) {
        setBlocker(result.blocked_by![0]);
        setError("");
        await refresh();
        return;
      }
      if (result.skipped_modified.length) {
        const names = result.skipped_modified.map((s) => basename(s.path)).join(", ");
        const force = await confirm({
          title: "Not reverted",
          message: `${names} ${result.skipped_modified.length === 1 ? "was" : "were"} edited after this ${label}. Restore ${result.skipped_modified.length === 1 ? "it" : "them"} anyway? Later edits will be lost.`,
          confirmLabel: "Revert anyway",
          danger: true,
        });
        if (force) {
          const forced = await retry();
          if (forced) {
            next = forced;
            if (forced.manual) setManual(forced.manual);
          }
        }
      }
      await refresh();
      setError(revertFailureMessage(next));
    },
    [confirm, refresh],
  );

  const applyFocus = useCallback((runId: string, seq: number) => {
    setForcedOpen(new Set([runId]));
    setFocusHint({ runId, seq });
  }, []);

  useEffect(() => {
    if (!focusHint) return;
    const item = items.find(
      (i) => i.run.run_id === focusHint.runId && i.row?.seqs.includes(focusHint.seq),
    );
    if (!item) return;
    setExpanded((prev) => new Set(prev).add(item.key));
    setFocusKey(item.key);
  }, [focusHint, items]);

  useEffect(() => {
    const consume = () => {
      const next = takeChangesFocus();
      if (!next) return;
      applyFocus(next.runId, next.seq);
    };
    consume();
    return subscribeChangesFocus(consume);
  }, [applyFocus, runs]);

  useEffect(() => {
    if (!focusKey) return;
    const el = scrollRef.current?.querySelector(`[data-focus-seq]`);
    const match = scrollRef.current?.querySelector(
      `.changes-row--focus`,
    ) as HTMLElement | null;
    match?.scrollIntoView({ block: "center" });
    void el;
  }, [focusKey, items]);

  const isLive = useCallback(
    (run: ChangesetRunDto) => runAgentLive(run, runningIds),
    [runningIds],
  );

  const stopAgent = useCallback(async (run: ChangesetRunDto) => {
    const api = getApi();
    if (!api?.cancel_agent || !run.conv_id) return;
    try {
      await api.cancel_agent(run.conv_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const refuseLiveRevert = useCallback((run: ChangesetRunDto) => {
    if (!isLive(run)) return false;
    setError("Stop the agent first — this run is still going.");
    return true;
  }, [isLive]);

  const openSmartRevert = useCallback((run: ChangesetRunDto, row: ChangeRow) => {
    if (refuseLiveRevert(run)) return;
    setSmartPicker({ run, row });
  }, [refuseLiveRevert]);

  const pickSmartRevert = useCallback(
    (chatId: string | "new") => {
      if (!smartPicker) return;
      const { run, row } = smartPicker;
      const later = blocker
        ? { name: blocker.other_ducky, runId: blocker.other_run_id, seq: blocker.other_seq }
        : null;
      const brief = formatSmartRevertBrief({
        program: programOfRow(row),
        path: row.kind === "file" ? row.path : row.slot,
        label: row.kind === "file" ? row.path : row.label,
        command: row.kind === "editor" ? row.command : row.op,
        reason: row.kind === "editor" ? row.reason : "",
        laterWriter: later,
      });
      const handlers = getAskAiHandlers();
      const payload = { text: "", filePath: "", startLine: 1, endLine: 1, draft: brief };
      if (chatId === "new") handlers?.onAskNew?.(payload);
      else handlers?.onAsk(chatId, payload);
      setSmartPicker(null);
      void run;
    },
    [blocker, smartPicker],
  );

  const revertRun = useCallback(
    async (run: ChangesetRunDto, isRedo = run.source === "revert") => {
      if (refuseLiveRevert(run)) return;
      const api = getApi();
      if (!api?.revert_changeset) return;
      const summary = changesetRunSummary(run);
      const parts: string[] = [];
      if (summary.files) parts.push(`${summary.files} file${summary.files === 1 ? "" : "s"}`);
      if (summary.editor) parts.push(`${summary.editor} editor change${summary.editor === 1 ? "" : "s"}`);
      const what = parts.join(" and ") || "the changes";
      const ok = await confirm({
        title: isRedo ? "Bring back" : "Revert run",
        message: isRedo
          ? `Restore ${what} this revert undid? Files and editor changes go back to how they were after the original run.`
          : `Undo ${what} ${runDuckyLabel(run, allChats)} made? Files go back to their content before the run and editor changes are put back where they were. The revert is recorded — Redo brings them back.`,
        confirmLabel: isRedo ? "Redo" : "Revert",
        danger: !isRedo,
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
    [allChats, confirm, refuseLiveRevert, reportRevert],
  );

  const archiveRuns = useCallback(
    async (scope: { runId?: string }, archived: boolean) => {
      const api = getApi();
      if (!api?.archive_changesets) return;
      const scopedChat = Boolean(convId || groupId);
      const ok = await confirm({
        title: archived
          ? scope.runId
            ? "Archive this run"
            : scopedChat
              ? "Archive this chat's history"
              : "Archive all"
          : "Unarchive",
        message: archived
          ? scope.runId
            ? "Move this run to Archived? The island stays as it is. Delete is only from Archived."
            : "Move these runs to Archived? The island stays as it is. Open Archived to delete them later."
          : "Put this run back on the active list?",
        confirmLabel: archived ? "Archive" : "Unarchive",
        danger: false,
      });
      if (!ok) return;
      setError("");
      try {
        await api.archive_changesets(convId, groupId, scope.runId || "", archived);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [confirm, convId, groupId, refresh],
  );

  const selectedIds = useMemo(
    () => runOrder.filter((id) => selected.has(id)),
    [runOrder, selected],
  );
  const selecting = selectedIds.length > 1;

  const toggleRunCheck = useCallback(
    (runId: string) => applyRunSelect(runId, { ctrl: true, shift: false }),
    [applyRunSelect],
  );

  const setAllSelected = useCallback(
    (on: boolean) => {
      setSelected(on ? new Set(runOrder) : new Set());
      if (on && runOrder[0]) setSelectAnchor(runOrder[0]);
    },
    [runOrder],
  );

  const archiveSelected = useCallback(async () => {
    const ids = selectedIds;
    if (!ids.length) return;
    const api = getApi();
    if (!api?.archive_changesets) return;
    const archived = !showArchived;
    const ok = await confirm({
      title: archived ? `Archive ${ids.length} runs` : `Unarchive ${ids.length} runs`,
      message: archived
        ? "Move these runs to Archived? The island stays as it is."
        : "Put these runs back on the active list?",
      confirmLabel: archived ? "Archive" : "Unarchive",
      danger: false,
    });
    if (!ok) return;
    setError("");
    try {
      for (const runId of ids) {
        await api.archive_changesets(convId, groupId, runId, archived);
      }
      setSelected(new Set());
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [confirm, convId, groupId, refresh, selectedIds, showArchived]);

  const deleteSelected = useCallback(async () => {
    const ids = selectedIds;
    if (!ids.length) return;
    const api = getApi();
    if (!api?.clear_changesets) return;
    const ok = await confirm({
      title: `Delete ${ids.length} runs`,
      message: "Permanently remove these logs? The island stays as it is.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    setError("");
    try {
      for (const runId of ids) {
        await api.clear_changesets(convId, groupId, runId, true);
      }
      setSelected(new Set());
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [confirm, convId, groupId, refresh, selectedIds]);

  /** Remove a blocked/failed row (all its attempts) from the ledger. Nothing on the island changes. */
  const deleteRow = useCallback(
    async (run: ChangesetRunDto, row: ChangeRow) => {
      const api = getApi();
      if (!api?.delete_changeset_entries) return;
      setError("");
      try {
        await api.delete_changeset_entries(run.run_id, row.steps.map((s) => s.seq));
        setDetail((d) => (d && d.row.key === row.key && d.run.run_id === run.run_id ? null : d));
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [refresh],
  );

  const revertSelected = useCallback(async () => {
    const api = getApi();
    if (!api?.revert_changeset) return;
    const targets = selectedIds
      .map((id) => runs.find((r) => r.run_id === id))
      .filter((run): run is ChangesetRunDto => {
        if (!run || run.archived) return false;
        const summary = changesetRunSummary(run);
        return !summary.reverted && !isLive(run);
      });
    if (!targets.length) return;
    const ok = await confirm({
      title: `Revert ${targets.length} runs`,
      message: "Undo everything these runs changed. Redo brings them back.",
      confirmLabel: "Revert",
      danger: true,
    });
    if (!ok) return;
    setError("");
    try {
      for (const run of targets) {
        setBusyRun(run.run_id);
        const result = await api.revert_changeset(run.run_id);
        await reportRevert("run", result, () => api.revert_changeset!(run.run_id, true));
      }
      setSelected(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyRun("");
    }
  }, [confirm, isLive, reportRevert, runs, selectedIds]);

  const deleteArchived = useCallback(
    async (scope: { runId?: string }) => {
      const api = getApi();
      if (!api?.clear_changesets) return;
      const ok = await confirm({
        title: scope.runId ? "Delete this run" : "Delete all archived",
        message: scope.runId
          ? "Permanently remove this log? The island stays as it is."
          : "Permanently remove every archived run? The island stays as it is.",
        confirmLabel: "Delete",
        danger: true,
      });
      if (!ok) return;
      setError("");
      try {
        await api.clear_changesets(convId, groupId, scope.runId || "", true);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [confirm, convId, groupId, refresh],
  );

  const revertRow = useCallback(
    async (run: ChangesetRunDto, row: ChangeRow, isRedo = run.source === "revert") => {
      if (refuseLiveRevert(run)) return;
      const api = getApi();
      if (!api?.revert_changeset_entry) return;
      const what = row.kind === "file" ? basename(row.path) : row.label;
      const ok = await confirm({
        title: isRedo ? "Bring back" : row.kind === "file" ? "Revert file" : "Undo editor change",
        message: isRedo
          ? `Restore ${what} to how it was after the original change?`
          : row.kind === "file"
            ? `Restore ${what} to its content before ${runDuckyLabel(run, allChats)} touched it? Redo brings it back if that was a mistake.`
            : `Put ${what} back the way it was before ${runDuckyLabel(run, allChats)} changed it? Redo brings it back if that was a mistake.`,
        confirmLabel: isRedo ? "Redo" : "Revert",
        danger: !isRedo,
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
    [allChats, confirm, refuseLiveRevert, reportRevert],
  );

  const revertStep = useCallback(
    async (run: ChangesetRunDto, row: ChangeRow, seq: number) => {
      if (refuseLiveRevert(run)) return;
      const api = getApi();
      if (!api?.revert_changeset_entry) return;
      const idx = row.seqs.indexOf(seq);
      const n = idx >= 0 ? idx + 1 : seq;
      const what = row.kind === "file" ? basename(row.path) : row.label;
      const ok = await confirm({
        title: "Revert this write",
        message: `Undo write ${n} of ${row.seqs.length} on ${what}? Later writes on this item stay. Redo brings this write back.`,
        confirmLabel: "Revert",
        danger: true,
      });
      if (!ok) return;
      setBusyRun(run.run_id);
      setError("");
      try {
        const result = await api.revert_changeset_entry(run.run_id, seq, false, true);
        await reportRevert("change", result, () => api.revert_changeset_entry!(run.run_id, seq, true, true));
        if (result.ok && !(result.blocked_by ?? []).length) setDiff(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyRun("");
      }
    },
    [confirm, refuseLiveRevert, reportRevert],
  );

  const redoRun = useCallback(
    async (run: ChangesetRunDto) => {
      const host = revertHost(runs, runRedoTargetId(run, runs));
      if (!host) return;
      await revertRun(host, true);
    },
    [revertRun, runs],
  );

  const redoRow = useCallback(
    async (row: ChangeRow) => {
      const target = rowRedoTarget(row, runs);
      if (!target) return;
      const host = revertHost(runs, target.runId);
      if (!host) return;
      if (target.seq) {
        await revertRow(host, { ...row, lastSeq: target.seq }, true);
        return;
      }
      await revertRun(host, true);
    },
    [revertRow, revertRun, runs],
  );

  const openDiff = useCallback(async (run: ChangesetRunDto, row: ChangeRow, seq?: number) => {
    const api = getApi();
    if (!api?.get_changeset_entry_contents) return;
    try {
      const seqs = row.seqs.length ? row.seqs : [row.firstSeq];
      const snapshots: DiffSnapshot[] = await Promise.all(
        seqs.map(async (s) => {
          const body = await api.get_changeset_entry_contents!(run.run_id, s);
          const step = row.steps.find((x) => x.seq === s);
          return {
            seq: s,
            before: body.before ?? "",
            after: body.after ?? "",
            summary: row.kind === "editor" ? row.detail : (step?.summary ?? ""),
            reverted: Boolean(step?.reverted),
          };
        }),
      );
      setDiff({
        title: row.kind === "file" ? basename(row.path) : row.label,
        json: row.kind === "editor",
        path: row.kind === "file" ? row.path : row.slot,
        snapshots,
        cursor: seq == null ? (seqs.length > 1 ? -1 : 0) : Math.max(0, seqs.indexOf(seq)),
        run,
        row,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const openBlockerChat = useCallback(() => {
    if (!blocker) return;
    requestChangesFocus(blocker.other_run_id, blocker.other_seq);
    applyFocus(blocker.other_run_id, blocker.other_seq);
    if (blocker.same_run) {
      const item = items.find(
        (i) => i.run.run_id === blocker.other_run_id && i.row?.seqs.includes(blocker.other_seq),
      );
      if (item?.row) void openDiff(item.run, item.row, blocker.other_seq);
    } else {
      const chat = allChats?.find((c) => c.id === blocker.other_conv_id);
      if (chat && onOpenChat) onOpenChat(chat);
    }
    setBlocker(null);
  }, [allChats, applyFocus, blocker, items, onOpenChat, openDiff]);

  const copyManual = useCallback(() => {
    const text = manual
      .map((row) => `${row.label || row.target} — ${row.command}${row.reason ? ` (${row.reason})` : ""}`)
      .join("\n");
    void navigator.clipboard?.writeText(text);
  }, [manual]);

  const visible = items.slice(view.start, view.end);
  const pair = diff ? diffPair(diff) : null;

  const menuItems = (target: MenuTarget): ContextMenuItem[] => {
    if (target.kind === "run") {
      const run = target.run;
      const summary = changesetRunSummary(run);
      const busy = Boolean(busyRun);
      const isOpen = openRuns.has(run.run_id);
      const live = isLive(run);
      const canRevert = !summary.reverted && !busy && !run.archived && !live;
      const isRedo = run.source === "revert";
      const redoId = isRedo ? "" : runRedoTargetId(run, runs);
      const out: ContextMenuItem[] = [
        {
          id: "toggle",
          label: isOpen ? "Collapse" : "Expand",
          onClick: () => toggleRun(run.run_id),
        },
        contextMenuSeparator("run-sep"),
      ];
      const multi = selected.has(run.run_id) && selectedIds.length > 1;
      if (multi) {
        const n = selectedIds.length;
        const anyBusy = runs.some(
          (r) => selected.has(r.run_id) && (busyRun === r.run_id || changesetRunSummary(r).running),
        );
        out.push(
          showArchived
            ? {
                id: "unarchive-all",
                label: `Unarchive all ${n}`,
                disabled: anyBusy,
                onClick: () => void archiveSelected(),
              }
            : {
                id: "archive-all",
                label: `Archive all ${n}`,
                disabled: anyBusy,
                onClick: () => void archiveSelected(),
              },
          {
            id: "delete-all",
            label: `Delete all ${n}`,
            danger: true,
            disabled: anyBusy,
            onClick: () => void deleteSelected(),
          },
        );
        return out;
      }
      if (live) {
        out.push({
          id: "stop",
          label: "Stop agent",
          danger: true,
          onClick: () => void stopAgent(run),
        });
      }
      if (canRevert) {
        out.push({
          id: "revert",
          label: isRedo ? "Redo (bring back)" : "Revert run",
          danger: !isRedo,
          onClick: () => void revertRun(run, isRedo),
        });
      }
      if (redoId) {
        out.push({
          id: "redo",
          label: "Redo (bring back)",
          onClick: () => void redoRun(run),
        });
      }
      out.push(
        showArchived
          ? {
              id: "unarchive",
              label: "Unarchive",
              disabled: summary.running || busy,
              onClick: () => void archiveRuns({ runId: run.run_id }, false),
            }
          : {
              id: "archive",
              label: "Archive",
              disabled: summary.running || busy,
              onClick: () => void archiveRuns({ runId: run.run_id }, true),
            },
      );
      out.push({
        id: "delete",
        label: "Delete",
        danger: true,
        disabled: summary.running || busy,
        onClick: () => void deleteArchived({ runId: run.run_id }),
      });
      return out;
    }
    const { run, row } = target;
    const blocked = row.outcome !== "ok";
    const busy = Boolean(busyRun);
    const canRevert =
      !blocked &&
      !row.reverted &&
      !busy &&
      !run.archived &&
      !isLive(run) &&
      !(row.kind === "editor" && row.revertable === "none");
    const canDiff = !blocked && row.hasDiff && !row.reverted;
    const canRedo = Boolean(rowRedoTarget(row, runs));
    const out: ContextMenuItem[] = [];
    if (canDiff) {
      out.push({
        id: "diff",
        label: row.kind === "file" ? "Diff" : "Details",
        onClick: () => void openDiff(run, row),
      });
    } else if (blocked) {
      out.push({
        id: "details",
        label: "Details",
        onClick: () => setDetail({ run, row }),
      });
    }
    if (row.kind === "file" && onOpenFile) {
      out.push({
        id: "open",
        label: "Open file",
        onClick: () => onOpenFile(row.path, basename(row.path)),
      });
    }
    if (row.steps.length > 1) {
      out.push({
        id: "steps",
        label: expanded.has(`${run.run_id}/${row.key}`) ? "Hide steps" : "Show steps",
        onClick: () => toggleExpanded(`${run.run_id}/${row.key}`),
      });
    }
    if (out.length) out.push(contextMenuSeparator("row-sep"));
    if (canRevert) {
      out.push({
        id: "revert",
        label: run.source === "revert" ? "Redo (bring back)" : "Revert",
        danger: run.source !== "revert",
        onClick: () => void revertRow(run, row, run.source === "revert"),
      });
    }
    if (canRedo) {
      out.push({
        id: "redo",
        label: "Redo (bring back)",
        onClick: () => void redoRow(row),
      });
    }
    if (blocked) {
      out.push({
        id: "delete",
        label: "Delete",
        danger: true,
        onClick: () => void deleteRow(run, row),
      });
    }
    return out;
  };

  return (
    <div className={`changes-view${selecting ? " changes-view--selecting" : ""}`}>
      <ScopedCss
        selector={`.${virtualClass}`}
        rules={{
          "--changes-pad-top": `${view.padTop}px`,
          "--changes-pad-bottom": `${view.padBottom}px`,
        }}
      />
      <header className={`changes-toolbar${searchOpen ? " is-searching" : ""}`}>
        {searchOpen ? (
          <>
            <Icons.Search />
            <input
              ref={searchRef}
              className="changes-search"
              type="search"
              placeholder="Search paths and targets"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  if (!query) setSearchOpen(false);
                  else setQuery("");
                }
              }}
              aria-label="Search ledger"
            />
            <IconBtn title="Close search" onClick={() => setSearchOpen(false)}>
              <Icons.Close />
            </IconBtn>
          </>
        ) : (
          <>
            <div className="changes-toolbar-title">
              <Icons.Clock />
              <span>Ledger</span>
            </div>
            <div className="changes-filters">
              {hideDuckyFilter || profileId || profileName ? null : (
                <ChoiceDropdown
                  size="compact"
                  trigger={<Icons.Users />}
                  aria-label="Filter by ducky"
                  value={duckyFilter}
                  onChange={setDuckyFilter}
                  options={[
                    { value: "all", label: "Everyone" },
                    ...duckies.map((d) => ({ value: d.id, label: d.label })),
                  ]}
                  triggerClassName={duckyFilter !== "all" ? "is-active" : undefined}
                  minWidth={180}
                />
              )}
              <ChoiceDropdown
                size="compact"
                trigger={<Icons.Filter />}
                aria-label="Filter by kind"
                value={kindFilter}
                onChange={(value) => setKindFilter(value as KindFilter)}
                options={KIND_OPTIONS}
                triggerClassName={kindFilter !== "all" ? "is-active" : undefined}
                minWidth={160}
              />
              {programs.length > 1 ? (
                <ChoiceDropdown
                  size="compact"
                  trigger={<Icons.Sliders />}
                  aria-label="Filter by program"
                  value={programFilter}
                  onChange={setProgramFilter}
                  options={[
                    { value: "all", label: "All programs" },
                    ...programs.map((p) => ({ value: p, label: programLabel(p) })),
                  ]}
                  triggerClassName={programFilter !== "all" ? "is-active" : undefined}
                  minWidth={160}
                />
              ) : null}
              <IconBtn
                title="Search ledger"
                pressed={Boolean(query)}
                onClick={() => setSearchOpen(true)}
              >
                <Icons.Search />
              </IconBtn>
              <span className="changes-run-actions">
                <IconBtn
                  title={showArchived ? "Hide archived" : "Show archived"}
                  pressed={showArchived}
                  onClick={() => setShowArchived((v) => !v)}
                >
                  <Icons.Inbox />
                </IconBtn>
                <IconBtn title="Refresh" onClick={() => void refresh()}>
                  <Icons.Refresh />
                </IconBtn>
                {runs.length > 0 && !showArchived ? (
                  <IconBtn title="Archive all" onClick={() => void archiveRuns({}, true)}>
                    <Icons.Archive />
                  </IconBtn>
                ) : null}
                {runs.length > 0 && showArchived ? (
                  <IconBtn title="Delete all" onClick={() => void deleteArchived({})}>
                    <Icons.Trash />
                  </IconBtn>
                ) : null}
              </span>
            </div>
          </>
        )}
      </header>

      {selectedIds.length > 0 ? (
        <div className="changes-selection-bar">
          <span className="changes-selection-count">
            {selectedIds.length} selected
          </span>
          <span className="changes-run-actions">
            {!showArchived ? (
              <IconBtn title="Revert selected" onClick={() => void revertSelected()}>
                <Icons.Undo />
              </IconBtn>
            ) : null}
            <IconBtn
              title={showArchived ? "Unarchive selected" : "Archive selected"}
              onClick={() => void archiveSelected()}
            >
              <Icons.Archive />
            </IconBtn>
            {showArchived ? (
              <IconBtn title="Delete selected" onClick={() => void deleteSelected()}>
                <Icons.Trash />
              </IconBtn>
            ) : null}
          </span>
        </div>
      ) : null}

      {error ? <div className="changeset-error">{error}</div> : null}

      {manual.length > 0 ? (
        <section className="changes-manual">
          <div className="changes-manual-head">
            <strong>Ducky could not undo these</strong>
            <span>Plugin commands with no known inverse. They stay in the history until you undo them in UEFN.</span>
            <span className="changes-run-actions">
              <IconBtn title="Copy" onClick={copyManual}>
                <Icons.Copy />
              </IconBtn>
              <IconBtn title="Dismiss" onClick={() => setManual([])}>
                <Icons.Close />
              </IconBtn>
            </span>
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
        {items.length ? (
          <div className="changes-colhead" role="row">
            {selecting ? (
              <input
                type="checkbox"
                className="changes-run-check"
                checked={selectedIds.length === runOrder.length}
                ref={(el) => {
                  if (el) el.indeterminate = selectedIds.length > 0 && selectedIds.length < runOrder.length;
                }}
                aria-label="Select all runs"
                onChange={(e) => setAllSelected(e.target.checked)}
              />
            ) : null}
            <button
              type="button"
              className="changes-colhead-btn changes-colhead-time"
              aria-sort={sortKey === "time" ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
              onClick={() => toggleSort("time")}
            >
              Time{sortMark(sortKey === "time", sortDir)}
            </button>
            <button
              type="button"
              className="changes-colhead-btn changes-colhead-model"
              aria-sort={sortKey === "model" ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
              onClick={() => toggleSort("model")}
            >
              Model{sortMark(sortKey === "model", sortDir)}
            </button>
            <button
              type="button"
              className="changes-colhead-btn changes-colhead-kind"
              aria-sort={sortKey === "kind" ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
              onClick={() => toggleSort("kind")}
            >
              Kind{sortMark(sortKey === "kind", sortDir)}
            </button>
            <button
              type="button"
              className="changes-colhead-btn changes-colhead-name"
              aria-sort={sortKey === "name" ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
              onClick={() => toggleSort("name")}
            >
              Name{sortMark(sortKey === "name", sortDir)}
            </button>
          </div>
        ) : null}
        {loading ? (
          <p className="changes-empty">Loading ledger…</p>
        ) : items.length === 0 ? (
          <p className="changes-empty">
            {runs.length === 0
              ? showArchived
                ? "Nothing archived."
                : convId || groupId
                ? "This chat has not changed anything yet."
                : profileId || profileName
                ? "This ducky type has not changed anything yet."
                : "Nothing has been changed in this project yet. Every file a ducky writes and every actor, device or asset it touches shows up here."
              : "No changes match these filters."}
          </p>
        ) : (
          <div className={`changes-virtual ${virtualClass}`}>
            {visible.map((item) => {
              if (item.program) {
                return (
                  <div key={item.key} className="changes-program-head">
                    {programLabel(item.program)}
                  </div>
                );
              }
              if (!item.row) {
                const run = item.run;
                const summary = changesetRunSummary(run);
                const thisReverting = busyRun === run.run_id;
                const busy = Boolean(busyRun);
                const live = isLive(run);
                const canRevert = !summary.reverted && !busy && !run.archived && !live;
                const isRedo = run.source === "revert";
                const redoId = isRedo ? "" : runRedoTargetId(run, runs);
                const meta = [shortModelLabel(run.model || ""), formatSavedAt(Math.floor(run.started))]
                  .filter(Boolean)
                  .join(" · ");
                const lane = run.lane?.write_allowed ?? [];
                const isOpen = openRuns.has(run.run_id);
                const changeCount = summary.files + summary.editor + summary.blocked;
                const who = runDuckyLabel(run, allChats);
                const chat = runChat(run, allChats);
                const canOpenChat = Boolean(chat && onOpenChat);
                return (
                  <div
                    key={item.key}
                    className={`changes-run-head ${isOpen ? "changes-run-head--open" : "changes-run-head--folded"}${selected.has(run.run_id) ? " changes-run-head--selected" : ""}${thisReverting ? " changes-run-head--busy" : ""}`}
                    role="button"
                    tabIndex={thisReverting ? -1 : 0}
                    aria-expanded={isOpen}
                    aria-selected={selected.has(run.run_id)}
                    aria-busy={thisReverting || undefined}
                    aria-label={`${who} run, ${isOpen ? "collapse" : "expand"}`}
                    onClick={(e) => {
                      if (thisReverting) return;
                      onRunHeadClick(e, run.run_id);
                    }}
                    onContextMenu={(e) => {
                      if (thisReverting) return;
                      openMenu(e, { kind: "run", run });
                    }}
                    onKeyDown={(e) => {
                      if (thisReverting) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        toggleRun(run.run_id);
                      }
                    }}
                  >
                    {selecting ? (
                      <input
                        type="checkbox"
                        className="changes-run-check"
                        checked={selected.has(run.run_id)}
                        aria-label={`Select ${who}`}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => {
                          e.stopPropagation();
                          toggleRunCheck(run.run_id);
                        }}
                      />
                    ) : null}
                    <span className="changes-run-chevron" aria-hidden="true">
                      {isOpen ? <Icons.ChevronDown /> : <Icons.ChevronRight />}
                    </span>
                    {canOpenChat ? (
                      <button
                        type="button"
                        className="changes-run-open"
                        title={`Open ${who}`}
                        aria-label={`Open ${who}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenChat!(chat!);
                        }}
                      >
                        <DuckyAvatar styleId={styleByConv.get(run.conv_id)} size={20} />
                        <span className="changes-run-name">{who}</span>
                      </button>
                    ) : (
                      <>
                        <DuckyAvatar styleId={styleByConv.get(run.conv_id)} size={20} />
                        <span className="changes-run-name">{who}</span>
                      </>
                    )}
                    {chatWasDeleted(run, allChats) ? (
                      <span className="changeset-badge" title="This chat was deleted. The work on the island is still here.">
                        Deleted chat
                      </span>
                    ) : null}
                    <span className="changes-run-meta" title={run.run_id}>
                      {meta}
                      {meta ? " · " : ""}
                      {changeCount} change{changeCount === 1 ? "" : "s"}
                    </span>
                    <span className={`changeset-status changeset-status--${run.archived ? "archived" : run.status}`}>
                      {runDisplayStatus(run, summary)}
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
                    {thisReverting ? <span className="changeset-badge">Reverting…</span> : null}
                    <span className="changes-run-actions" onClick={(e) => e.stopPropagation()}>
                      {live ? (
                        <IconBtn
                          title="Stop agent"
                          hint="Stop the agent first — then you can revert"
                          onClick={() => void stopAgent(run)}
                        >
                          <Icons.Stop />
                        </IconBtn>
                      ) : null}
                      {canRevert ? (
                        <IconBtn
                          title={isRedo ? "Redo" : "Revert run"}
                          disabled={busy}
                          onClick={() => void revertRun(run, isRedo)}
                        >
                          {isRedo ? <Icons.Redo /> : <Icons.Undo />}
                        </IconBtn>
                      ) : null}
                      {redoId ? (
                        <IconBtn
                          title="Redo"
                          disabled={busy}
                          onClick={() => void redoRun(run)}
                        >
                          <Icons.Redo />
                        </IconBtn>
                      ) : null}
                      <IconBtn
                        title={showArchived ? "Unarchive" : "Archive"}
                        disabled={summary.running || busy}
                        onClick={() => void archiveRuns({ runId: run.run_id }, !showArchived)}
                      >
                        <Icons.Archive />
                      </IconBtn>
                      {showArchived ? (
                        <IconBtn
                          title="Delete"
                          disabled={summary.running || busy}
                          onClick={() => void deleteArchived({ runId: run.run_id })}
                        >
                          <Icons.Trash />
                        </IconBtn>
                      ) : null}
                    </span>
                  </div>
                );
              }
              return (
                <ChangeRowView
                  key={item.key}
                  run={item.run}
                  row={item.row}
                  busy={Boolean(busyRun)}
                  reverting={busyRun === item.run.run_id}
                  agentLive={isLive(item.run)}
                  expanded={expanded.has(item.key)}
                  redo={item.run.source === "revert"}
                  canRedo={Boolean(rowRedoTarget(item.row, runs))}
                  onToggleExpanded={() => toggleExpanded(item.key)}
                  onOpenFile={onOpenFile}
                  onDiff={(seq) => void openDiff(item.run, item.row!, seq)}
                  onDetails={() => setDetail({ run: item.run, row: item.row! })}
                  onRevert={() => void revertRow(item.run, item.row!, item.run.source === "revert")}
                  onRevertStep={(seq) => void revertStep(item.run, item.row!, seq)}
                  onRedo={() => void redoRow(item.row!)}
                  onSmartRevert={isLive(item.run) ? undefined : () => openSmartRevert(item.run, item.row!)}
                  highlighted={focusKey === item.key}
                  onContextMenu={(e) => openMenu(e, { kind: "row", run: item.run, row: item.row! })}
                />
              );
            })}
          </div>
        )}
      </div>

      {menu ? <ContextMenu x={menu.x} y={menu.y} items={menuItems(menu.data)} onClose={closeMenu} /> : null}

      {blocker ? (
        <Modal
          open
          onClose={() => setBlocker(null)}
          title={blocker.same_run ? "Revert that write first" : "Revert their change first"}
          width={420}
          zIndex={modalContainer ? 40 : 100020}
          container={modalContainer}
        >
          <p>
            {blocker.same_run
              ? "A later write on this item is still in place. Revert that write first."
              : `${allChats?.find((c) => c.id === blocker.other_conv_id)?.name || blocker.other_ducky || "Another duck"} changed this after. Revert their change first.`}
          </p>
          <div className="changes-run-actions">
            <button type="button" className="changeset-btn" onClick={openBlockerChat}>
              {blocker.same_run
                ? "Show that write"
                : `Open ${allChats?.find((c) => c.id === blocker.other_conv_id)?.name || blocker.other_ducky || "them"}`}
            </button>
          </div>
        </Modal>
      ) : null}

      {smartPicker ? (
        <Modal
          open
          onClose={() => setSmartPicker(null)}
          title="Smart Revert"
          width={420}
          zIndex={modalContainer ? 40 : 100020}
          container={modalContainer}
        >
          <p>Pick a chat to unwind this item, or spawn a new one.</p>
          <ul className="changes-smart-list">
            {(allChats ?? []).map((chat) => (
              <li key={chat.id}>
                <button type="button" className="changeset-btn" onClick={() => pickSmartRevert(chat.id)}>
                  {chat.name}
                </button>
              </li>
            ))}
            <li>
              <button type="button" className="changeset-btn" onClick={() => pickSmartRevert("new")}>
                New…
              </button>
            </li>
          </ul>
        </Modal>
      ) : null}

      {diff ? (
        <Modal
          open
          onClose={() => setDiff(null)}
          title={diff.title}
          width={880}
          zIndex={modalContainer ? 40 : 100020}
          container={modalContainer}
        >
          <div className="changeset-diff-modal">
            {diff.snapshots.length > 1 ? (
              <div className="changeset-diff-steps">
                <button
                  type="button"
                  className="changeset-btn"
                  disabled={diff.cursor < 0}
                  aria-label="Previous write"
                  onClick={() =>
                    setDiff({ ...diff, cursor: diff.cursor <= 0 ? -1 : diff.cursor - 1 })
                  }
                >
                  <Icons.ChevronLeft />
                </button>
                <button
                  type="button"
                  className="changeset-btn"
                  aria-pressed={diff.cursor < 0}
                  onClick={() => setDiff({ ...diff, cursor: -1 })}
                >
                  All
                </button>
                {diff.snapshots.map((_, i) => (
                  <button
                    key={diff.snapshots[i].seq}
                    type="button"
                    className="changeset-btn"
                    aria-pressed={diff.cursor === i}
                    onClick={() => setDiff({ ...diff, cursor: i })}
                  >
                    {i + 1}
                  </button>
                ))}
                <button
                  type="button"
                  className="changeset-btn"
                  disabled={diff.cursor >= diff.snapshots.length - 1}
                  aria-label="Next write"
                  onClick={() =>
                    setDiff({ ...diff, cursor: diff.cursor < 0 ? 0 : diff.cursor + 1 })
                  }
                >
                  <Icons.ChevronRight />
                </button>
                <span className="changeset-diff-steps-label">
                  {diff.cursor < 0
                    ? `All ${diff.snapshots.length} writes`
                    : `Write ${diff.cursor + 1} of ${diff.snapshots.length}`}
                </span>
              </div>
            ) : null}
            {pair && diff.json ? (
              <JsonDiffView before={pair.before} after={pair.after} summary={pair.summary} />
            ) : pair ? (
              <Suspense fallback={<p className="changes-empty">Loading diff…</p>}>
                <ToolFileEditDiff
                  key={`${diff.path}:${diff.cursor}`}
                  edit={{
                    path: diff.path,
                    before: pair.before,
                    after: pair.after,
                    linesAdded: 0,
                    linesRemoved: 0,
                    kind: "write",
                  }}
                  onOpenFile={onOpenFile}
                  defaultExpanded
                />
              </Suspense>
            ) : null}
            {diff.row.outcome === "ok" && !diff.row.reverted && !diff.run.archived ? (
              <div className="changeset-diff-actions">
                {isLive(diff.run) ? (
                  <button
                    type="button"
                    className="changeset-btn changeset-btn--danger"
                    onClick={() => void stopAgent(diff.run)}
                  >
                    Stop agent to revert
                  </button>
                ) : diff.cursor < 0 ? (
                  <button
                    type="button"
                    className="changeset-btn changeset-btn--danger"
                    disabled={Boolean(busyRun)}
                    onClick={() => void revertRow(diff.run, diff.row)}
                  >
                    Revert all writes
                  </button>
                ) : (
                  <button
                    type="button"
                    className="changeset-btn changeset-btn--danger"
                    disabled={Boolean(busyRun) || Boolean(diff.snapshots[diff.cursor]?.reverted)}
                    onClick={() => void revertStep(diff.run, diff.row, diff.snapshots[diff.cursor].seq)}
                  >
                    Revert this write
                  </button>
                )}
              </div>
            ) : null}
          </div>
        </Modal>
      ) : null}

      {detail ? (
        <Modal
          open
          onClose={() => setDetail(null)}
          title={`${detail.row.outcome === "blocked" ? "BLOCKED" : "FAILED"} — ${detail.row.kind === "file" ? detail.row.path : detail.row.label}`}
          width={560}
          zIndex={modalContainer ? 40 : 100020}
          container={modalContainer}
        >
          <div className="changeset-detail-modal">
            <p className="changeset-detail-reason">{detail.row.reason || "No details recorded."}</p>
            {detail.row.steps.length > 1 ? (
              <ul className="changeset-detail-steps">
                {detail.row.steps.map((step) => (
                  <li key={step.seq}>
                    <span>{step.tool}</span>
                    {step.summary ? <span> — {step.summary}</span> : null}
                  </li>
                ))}
              </ul>
            ) : null}
            <button
              type="button"
              className="changeset-btn changeset-btn--danger"
              onClick={() => void deleteRow(detail.run, detail.row)}
            >
              Delete
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
