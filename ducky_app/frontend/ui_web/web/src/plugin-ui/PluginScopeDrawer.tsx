import { useEffect, useState, type ReactNode } from "react";
import "./plugin-ui.css";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { getApi } from "../hooks/usePanelApi";
import type {
  PluginScopeFolderUnit,
  PluginScopeHistoryEntry,
  PluginScopeOutgoing,
  PluginScopeResult,
  PluginScopeStatus,
} from "../types/panel";
import {
  breakConfirm,
  clockText,
  conflictText,
  DATA_AT_REST,
  discardConfirm,
  dropConfirm,
  durationText,
  folderNote,
  folderRefusalText,
  folderUnitKey,
  folderUnitLockId,
  folderUnitStateText,
  formatBytes,
  groupName,
  hasFolders,
  historyWho,
  holdsFolderUnit,
  listText,
  notShared,
  outgoingStateText,
  presence,
  presenceText,
  refusedFolders,
  restoreConfirm,
  storageLine,
  unitSizeText,
  type ScopeTab,
} from "./scopeBarText";
import { PresenceAvatar } from "./PresenceAvatar";

/** Rows of a long list shown before "Show all". */
const ROWS_SHOWN = 20;
const HISTORY_PAGE = 20;
/** Who the server names as the author of a plugin's move to the new sync (history #1). */
const IMPORTED_BY = "Imported from team data";

type Props = {
  pluginId: string;
  status: PluginScopeStatus;
  /** Re-read the status after an action (`dataChanged`: the plugin's data changed). */
  onChanged: (dataChanged?: boolean) => Promise<void> | void;
  id?: string;
  /** The tab shown, when the bar picks it (its status opens the tab that explains it). */
  tab?: ScopeTab;
  onTab?: (tab: ScopeTab) => void;
  /** Join a teammate's live group (People). */
  onJoin?: (room: string) => void;
  /** The Team modal's left nav picks the tab: no tab strip of its own. */
  hideTabs?: boolean;
};

export type ScopeTabItem = { id: ScopeTab; label: string; count?: number; alert?: boolean };

/** The tabs a copy has, with what needs looking at in each (the drawer's strip, the Team modal's nav). */
export function scopeTabs(s: PluginScopeStatus, pluginId: string): ScopeTabItem[] {
  const api = getApi();
  const v2 = s.engine ? s.engine === "v2" : s.mode !== undefined || typeof s.head === "number";
  const outgoing = outgoingRows(s);
  const pending = typeof s.pending === "number" ? s.pending : outgoing.filter((row) => row.state === "queued").length;
  const conflicts = s.conflicts ?? [];
  const people = presence(s.people, pluginId);
  return [
    { id: "changes", label: "Changes", count: outgoing.length || pending },
    { id: "incoming", label: "Incoming", count: (s.changes?.incoming ?? 0) + (s.changes?.blocked ?? []).length },
    { id: "conflicts", label: "Conflicts", count: conflicts.length, alert: conflicts.length > 0 },
    ...(s.locks !== undefined ? [{ id: "locks" as const, label: "Locks", count: s.locks.length }] : []),
    ...(v2 && api?.plugin_scope_history ? [{ id: "history" as const, label: "History" }] : []),
    ...(s.people !== undefined ? [{ id: "people" as const, label: "People", count: people.filter((p) => !p.me && p.state !== "away").length }] : []),
    { id: "settings", label: "Settings" },
  ];
}

type History = { entries: PluginScopeHistoryEntry[]; more: boolean };
type Versions = { kind: string; key: string; entries: PluginScopeHistoryEntry[] };

/** "Oct 17, 3:02 PM". */
function whenText(at?: number): string {
  if (!at) return "";
  return new Date(at * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** Changes not sent yet: the engine's list, else the ones a provider held back (refused, blocked). */
function outgoingRows(status: PluginScopeStatus): PluginScopeOutgoing[] {
  return status.changes?.outgoing ?? status.items ?? [];
}

/** One line of a tab: what it is, a second line about it, and its buttons. */
function Row({ lead, name, sub, side, actions, tone }: {
  lead?: ReactNode; name: ReactNode; sub?: ReactNode; side?: ReactNode; actions?: ReactNode; tone?: "warn" | "error";
}) {
  return (
    <div className={`plugin-scope-row${tone ? ` plugin-scope-row--${tone}` : ""}`}>
      {lead ? <span className="plugin-scope-row__lead">{lead}</span> : null}
      <span className="plugin-scope-row__text">
        <span className="plugin-scope-row__name">{name}</span>
        {sub ? <span className="plugin-scope-row__sub">{sub}</span> : null}
      </span>
      {side ? <span className="plugin-scope-row__side">{side}</span> : null}
      {actions ? <span className="plugin-scope-row__actions">{actions}</span> : null}
    </div>
  );
}

/** A tab's first line: what the tab is for, and its own buttons. */
function Intro({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="plugin-scope-intro">
      <span>{children}</span>
      {actions ? <span className="plugin-scope-row__actions">{actions}</span> : null}
    </div>
  );
}

function Btn({ children, onClick, disabled, primary, label, pressed }: {
  children: ReactNode; onClick: () => void; disabled?: boolean; primary?: boolean; label?: string; pressed?: boolean;
}) {
  return (
    <button type="button" className={`plugin-scope-btn plugin-scope-btn--row${primary ? " plugin-scope-btn--primary" : ""}`}
      disabled={disabled} aria-label={label} aria-pressed={pressed} onClick={onClick}>
      {children}
    </button>
  );
}

/**
 * Source control for one plugin's team copy, in tabs: Changes (what goes out, and why not),
 * Incoming (what comes in), Conflicts, Locks, History, People and Settings. Under the scope bar,
 * and on Plugins → a plugin → Data. Anything that changes data for everyone asks first.
 */
export function PluginScopeDrawer({ pluginId, status, onChanged, id, tab: shownTab, onTab, onJoin, hideTabs }: Props) {
  const { confirm } = useConfirmModal();
  const [ownTab, setOwnTab] = useState<ScopeTab>("changes");
  const tab = shownTab ?? ownTab;
  const setTab = onTab ?? setOwnTab;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [compare, setCompare] = useState<{ key: string; text: string } | null>(null);
  const [history, setHistory] = useState<History | null>(null);
  const [openSeq, setOpenSeq] = useState<number | null>(null);
  const [versions, setVersions] = useState<Versions | null>(null);
  const [message, setMessage] = useState("");
  const [allRows, setAllRows] = useState(false);

  // Opening the panel has the space looked at again (heads, presence) once; what that
  // brings arrives as a status refresh. Never per status: each look starts a round.
  useEffect(() => {
    void getApi()?.plugin_scope_activity?.(pluginId).catch(() => undefined);
  }, [pluginId]);

  const s = status;
  const api = getApi();
  const scope = s.scope;
  const label = scope?.label || "this space";
  const plugin = s.pluginLabel || pluginId;
  const state = s.state || "";
  const lost = state === "lost";
  const reset = state === "reset";
  // History, Mode, locks and people are the new sync's; a copy still on the older one has none.
  const v2 = s.engine ? s.engine === "v2" : s.mode !== undefined || typeof s.head === "number";

  /** One action; its error stays in the panel until the next one. True when it worked. */
  const act = async (run: () => Promise<PluginScopeResult | undefined>, dataChanged = false, done = ""): Promise<boolean> => {
    setError("");
    setNote("");
    setBusy(true);
    try {
      const res = await run();
      if (res?.ok === false) {
        setError(res.error || "That didn't work. Try again.");
        return false;
      }
      if (done) setNote(done);
      await onChanged(dataChanged);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setBusy(false);
    }
  };

  // ---------------------------------------------------------------- locks
  const release = (unit: string) => act(async () => api?.plugin_scope_unlock?.(pluginId, unit));
  const ask = (unit: string, holder: string, name: string) =>
    act(async () => api?.plugin_scope_ask?.(pluginId, unit), false, `Asked ${holder} to release ${name}.`);
  const breakLock = async (unit: string, holder: string, name: string) => {
    const ok = await confirm({ ...breakConfirm(holder, name), confirmLabel: "Break lock", danger: true });
    if (ok !== true) return;
    await act(async () => api?.plugin_scope_break?.(pluginId, unit));
  };
  const lockUnit = (unit: string, name: string) => act(async () => api?.plugin_scope_lock?.(pluginId, unit, name));

  // ---------------------------------------------------------------- folder units changed while held
  const keepCopy = (f: PluginScopeFolderUnit) =>
    act(async () => api?.plugin_scope_resolve?.(pluginId, "tree", folderUnitKey(f), "keep_copy"), true,
      `Your version of ${f.name} is kept beside it as a copy; ${f.name} has ${label}'s version again.`);
  const discard = async (f: PluginScopeFolderUnit) => {
    const ok = await confirm({ ...discardConfirm(f.name, label), confirmLabel: "Discard mine", danger: true });
    if (ok !== true) return;
    await act(async () => api?.plugin_scope_resolve?.(pluginId, "tree", folderUnitKey(f), "discard"), true);
  };

  // ---------------------------------------------------------------- changes
  const retry = async (key?: string) => {
    const run = async () => (key ? api?.plugin_scope_retry?.(pluginId, key) : api?.plugin_scope_retry?.(pluginId));
    if (await act(run)) void api?.plugin_scope_sync?.(pluginId, true);
  };
  const drop = async (row: PluginScopeOutgoing) => {
    const ok = await confirm({ ...dropConfirm(label, row.name || row.key), confirmLabel: "Drop my change", danger: true });
    if (ok !== true) return;
    await act(async () => api?.plugin_scope_drop?.(pluginId, row.kind, row.key), true);
  };
  const redownload = (kind: string, key: string) => act(async () => api?.plugin_scope_pull?.(pluginId, kind, key), true);
  const syncNow = () => act(async () => api?.plugin_scope_sync?.(pluginId, true));

  // ---------------------------------------------------------------- conflicts
  const resolve = async (kind: string, key: string, name: string, choice: "mine" | "theirs" | "both", by: string) => {
    if (choice === "theirs") {
      const ok = await confirm({
        title: `Keep ${by}'s version of ${name}?`,
        message: "Your version here is not kept.",
        confirmLabel: "Keep theirs",
        danger: true,
      });
      if (ok !== true) return;
    }
    if (compare?.key === key) setCompare(null);
    await act(async () => api?.plugin_scope_resolve?.(pluginId, kind, key, choice), true);
  };
  const showCompare = async (kind: string, key: string) => {
    if (compare?.key === key) {
      setCompare(null);
      return;
    }
    setError("");
    try {
      const res = await api?.plugin_scope_resolve?.(pluginId, kind, key, "compare");
      if (res?.ok === false) setError(res.error || "Couldn't compare them.");
      else setCompare({ key, text: res?.diff || "No difference to show." });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  // ---------------------------------------------------------------- history
  const loadHistory = async (before?: number) => {
    setError("");
    try {
      const res = await api?.plugin_scope_history?.(pluginId, { before, limit: HISTORY_PAGE });
      if (!res || res.ok === false) {
        setError(res?.error || "Couldn't load the history.");
        return;
      }
      setHistory((prev) => ({
        entries: [...(before && prev ? prev.entries : []), ...(res.entries ?? [])],
        more: Boolean(res.more),
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const loadVersions = async (kind: string, key: string) => {
    setError("");
    try {
      const res = await api?.plugin_scope_history?.(pluginId, { kind, key, limit: HISTORY_PAGE });
      if (!res || res.ok === false) setError(res?.error || "Couldn't load its versions.");
      else setVersions({ kind, key, entries: res.entries ?? [] });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const restore = async (action: "item" | "to" | "undo", seq: number, item?: { kind: string; key: string }) => {
    const question = restoreConfirm(action, label, { plugin, seq, key: item?.key });
    const ok = await confirm({ ...question, confirmLabel: action === "undo" ? "Undo" : "Restore" });
    if (ok !== true) return;
    const request = action === "item" && item ? { items: [{ ...item, seq }] } : action === "undo" ? { undoSeq: seq } : { toSeq: seq };
    let skipped = 0;
    const worked = await act(async () => {
      const res = await api?.plugin_scope_restore?.(pluginId, request);
      skipped = res?.skipped?.length ?? 0;
      return res;
    }, true);
    if (!worked) return;
    setNote(skipped
      ? `Restored. ${skipped} ${skipped === 1 ? "item wasn't" : "items weren't"}: ${skipped === 1 ? "its version is" : "their versions are"} no longer kept.`
      : "Restored.");
    if (history) void loadHistory();
  };
  const head = s.head;
  // The History tab loads itself, and again when a change lands while it's open.
  useEffect(() => {
    if (tab === "history" && v2 && api?.plugin_scope_history) void loadHistory();
  }, [tab, v2, pluginId, head]);

  // ---------------------------------------------------------------- mode
  const setMode = (mode: "auto" | "manual") => act(async () => api?.plugin_scope_mode?.(pluginId, mode));
  const push = async () => {
    if (await act(async () => api?.plugin_scope_push?.(pluginId, message.trim()))) setMessage("");
  };
  const pull = () => act(async () => api?.plugin_scope_pull?.(pluginId), true);

  // ---------------------------------------------------------------- lost access
  const saveUnsent = async () => {
    let saved = 0;
    const worked = await act(async () => {
      const res = await api?.plugin_scope_save_unsent?.(pluginId, "save");
      saved = res?.saved?.length ?? 0;
      return res;
    });
    if (worked) setNote(`Saved ${saved} ${saved === 1 ? "change" : "changes"} to Local.`);
  };
  const discardUnsent = async () => {
    const ok = await confirm({
      title: "Let your unsent changes go?",
      message: `They are never sent, and they leave this PC with ${label}'s copy.`,
      confirmLabel: "Let them go",
      danger: true,
    });
    if (ok !== true) return;
    await act(async () => api?.plugin_scope_save_unsent?.(pluginId, "discard"));
  };

  const storage = storageLine(s.usage);
  const people = presence(s.people, pluginId);
  const groups = s.live?.groups ?? [];
  const locks = s.locks ?? [];
  const outgoing = outgoingRows(s);
  const shownOut = allRows ? outgoing : outgoing.slice(0, ROWS_SHOWN);
  // Retry and Drop my change are for data items; a folder unit the server kept failing has
  // Retry too. Any other held folder unit says what fixes it.
  const fixable = outgoing.filter((row) => row.state === "blocked" || (row.kind !== "tree" && row.state === "refused"));
  const canFix = !reset;
  const changes = s.changes ?? {};
  const incoming = changes.incoming ?? 0;
  const blockedIn = changes.blocked ?? [];
  const conflicts = s.conflicts ?? [];
  const folders = s.folders ?? [];
  const refusals = refusedFolders(s);
  const pending = typeof s.pending === "number" ? s.pending : outgoing.filter((row) => row.state === "queued").length;
  const retryAll = canFix && fixable.length > 1 && Boolean(api?.plugin_scope_retry);
  // A provider lists the newest held changes only; the count is all of them.
  const unlisted = s.changes?.outgoing ? 0 : notShared(s).count - outgoing.length;
  const offerSave = lost && Boolean(api?.plugin_scope_save_unsent) && !s.unsentChosen && s.unsent !== 0;
  const manual = s.mode === "manual";
  const failing = state === "offline" || state === "error";

  const tabs = scopeTabs(s, pluginId);
  const shown = tabs.some((t) => t.id === tab) ? tab : "changes";

  const changesTab = (
    <>
      {offerSave ? (
        <Row tone="warn"
          name={s.unsent
            ? `${s.unsent} ${s.unsent === 1 ? "change" : "changes"} made here never reached ${label}.`
            : `Changes made here may not have reached ${label}.`}
          sub={`Keep them in your Local copy before ${label}'s data leaves this PC.`}
          actions={<>
            <Btn primary disabled={busy} onClick={() => void saveUnsent()}>Save to Local</Btn>
            <Btn disabled={busy} onClick={() => void discardUnsent()}>Let them go</Btn>
          </>} />
      ) : null}
      {!lost ? (
        <>
          <Intro actions={<>
            {retryAll ? <Btn disabled={busy} onClick={() => void retry()}>Retry all</Btn> : null}
            {!manual && !reset && api?.plugin_scope_sync
              ? <Btn disabled={busy} onClick={() => void syncNow()}>{failing ? "Retry" : "Sync now"}</Btn> : null}
          </>}>
            Your changes on this PC. {manual ? "They wait here until you send them." : `Each one goes to ${label} once it's done.`}
          </Intro>
          {failing && s.error ? <Row tone="error" name={state === "offline" ? "Offline" : "Couldn't sync"} sub={s.error} /> : null}
          {manual && api?.plugin_scope_push ? (
            <div className="plugin-scope-row">
              <input type="text" className="plugin-scope-input" aria-label="Message (optional)" placeholder="What you changed (optional)"
                value={message} maxLength={200} onChange={(event) => setMessage(event.target.value)} />
              <span className="plugin-scope-row__actions">
                <Btn primary disabled={busy || reset} onClick={() => void push()}>Send</Btn>
              </span>
            </div>
          ) : null}
          {shownOut.map((row) => {
            const name = row.name || row.key;
            const fix = canFix && fixable.includes(row);
            return (
              <Row key={`${row.kind}:${row.key}`} tone={fix ? "warn" : undefined}
                name={`${name}${row.deleted ? " (deleted here)" : ""}`}
                sub={[row.size ? formatBytes(row.size) : row.files ? unitSizeText(row.files) : "",
                  outgoingStateText(row.state, row.reason, row.attempts)].filter(Boolean).join(" · ")}
                actions={fix ? <>
                  {api?.plugin_scope_retry ? <Btn disabled={busy} label={`Retry ${name}`} onClick={() => void retry(row.key)}>Retry</Btn> : null}
                  {row.kind !== "tree" && api?.plugin_scope_drop
                    ? <Btn disabled={busy} label={`Drop my change to ${name}`} onClick={() => void drop(row)}>Drop</Btn> : null}
                </> : undefined} />
            );
          })}
          {!outgoing.length ? <p className="plugin-scope-empty">{pending ? `${pending} to send.` : "No changes to send."}</p> : null}
          {outgoing.length > ROWS_SHOWN || unlisted > 0 ? (
            <p className="plugin-scope-empty">
              {outgoing.length > ROWS_SHOWN ? (
                <button type="button" className="plugin-scope-link" aria-expanded={allRows} onClick={() => setAllRows(!allRows)}>
                  {allRows ? "Show fewer" : `Show all ${outgoing.length}`}
                </button>
              ) : null}
              {unlisted > 0 ? <span> {unlisted} more not listed</span> : null}
            </p>
          ) : null}
          {folders.length || refusals.length ? (
            <>
              <div className="plugin-scope-subhead">Folders</div>
              {refusals.map((folder) => (
                <Row key={folder.id} tone="warn" name={refusals.length > 1 ? folder.id : "This folder can't be shared"}
                  sub={folderRefusalText(folder.refused || "")} />
              ))}
              {folders.map((f) => {
                const held = f.state === "quarantined";
                const holder = f.elsewhere || f.holder || "the holder";
                const lockId = folderUnitLockId(f);
                const mine = holdsFolderUnit(f, locks);
                const theirs = !mine && (f.state === "locked" || f.state === "locked_by");
                return (
                  <Row key={`${f.folder_id ?? ""}:${f.unit}`} tone={held ? "warn" : undefined} name={f.name}
                    sub={[unitSizeText(f.files, f.bytes), folderUnitStateText(f)].filter(Boolean).join(" · ")}
                    actions={held ? <>
                      {api?.plugin_scope_ask ? <Btn disabled={busy} label={`Ask ${holder} to release ${f.name}`}
                        onClick={() => void ask(lockId, f.elsewhere || f.holder || "them", f.name)}>Ask</Btn> : null}
                      {api?.plugin_scope_resolve ? <>
                        <Btn disabled={busy} label={`Keep mine as a copy: ${f.name}`} onClick={() => void keepCopy(f)}>Keep as copy</Btn>
                        <Btn disabled={busy} label={`Discard mine: ${f.name}`} onClick={() => void discard(f)}>Discard mine</Btn>
                      </> : null}
                    </> : mine && api?.plugin_scope_unlock ? (
                      <Btn disabled={busy} label={`Release ${f.name}`} onClick={() => void release(lockId)}>Release</Btn>
                    ) : !theirs && f.state !== "refused" && api?.plugin_scope_lock ? (
                      <Btn disabled={busy} label={`Lock ${f.name}`} onClick={() => void lockUnit(lockId, f.name)}>Lock</Btn>
                    ) : undefined} />
                );
              })}
            </>
          ) : null}
        </>
      ) : null}
    </>
  );

  const incomingTab = (
    <>
      <Intro actions={api?.plugin_scope_pull ? (
        <Btn primary={Boolean(changes.held && incoming)} disabled={busy || reset} onClick={() => void pull()}>
          {changes.held && incoming ? "Update now" : "Get now"}
        </Btn>
      ) : undefined}>
        Teammates' changes on their way to this PC. {manual ? "In Manual they come in when you get them." : "They come in by themselves."}
      </Intro>
      {incoming ? (
        <Row name={`${incoming} ${incoming === 1 ? "change" : "changes"}${changes.incomingBy?.length ? ` from ${listText(changes.incomingBy)}` : ""}`}
          sub={changes.held ? `They wait until you update: ${plugin} restarts to take them.` : "Coming in now."} />
      ) : !blockedIn.length ? <p className="plugin-scope-empty">Nothing coming in. You have everyone's changes.</p> : null}
      {blockedIn.map((row) => {
        const name = row.name || row.key;
        return (
          <Row key={`${row.kind}:${row.key}`} tone="warn" name={name}
            sub={`${row.error || "Couldn't be applied here"}${row.attempts ? ` (${row.attempts} tries)` : ""}`}
            actions={api?.plugin_scope_pull
              ? <Btn disabled={busy} label={`Download ${name} again`} onClick={() => void redownload(row.kind, row.key)}>Download again</Btn>
              : undefined} />
        );
      })}
    </>
  );

  const conflictsTab = (
    <>
      <Intro>You and a teammate changed the same thing while apart. Pick what stays.</Intro>
      {conflicts.length ? conflicts.map((c) => {
        const name = c.name || c.key;
        const by = c.by || "someone";
        const deleted = c.reason === "deleted";
        return (
          <div key={`${c.kind}:${c.key}`}>
            <Row tone="warn" name={name} sub={`${conflictText(c.reason, c.by)}${c.at ? ` · ${clockText(c.at)}` : ""}`}
              actions={api?.plugin_scope_resolve ? <>
                <Btn disabled={busy} label={`${deleted ? "Restore mine" : "Keep mine"}: ${name}`}
                  onClick={() => void resolve(c.kind, c.key, name, "mine", by)}>{deleted ? "Restore mine" : "Keep mine"}</Btn>
                <Btn disabled={busy} label={`${deleted ? "Accept delete" : "Keep theirs"}: ${name}`}
                  onClick={() => void resolve(c.kind, c.key, name, "theirs", by)}>{deleted ? "Accept delete" : "Keep theirs"}</Btn>
                {c.kind === "tree" ? <Btn disabled={busy} label={`Keep both: ${name}`}
                  onClick={() => void resolve(c.kind, c.key, name, "both", by)}>Keep both</Btn> : null}
                <Btn label={`Compare: ${name}`} pressed={compare?.key === c.key} onClick={() => void showCompare(c.kind, c.key)}>Compare</Btn>
              </> : undefined} />
            {compare?.key === c.key ? <pre className="plugin-scope-diff">{compare.text}</pre> : null}
          </div>
        );
      }) : <p className="plugin-scope-empty">No conflicts.</p>}
    </>
  );

  const locksTab = (
    <>
      <Intro>
        Pressing something locks it for you: teammates on the same screen see it greyed out (live with you at once,
        everyone else within seconds). What you change stays locked to you until 30 s after it's sent.
      </Intro>
      {locks.length ? locks.map((lock) => {
        const name = lock.name || lock.unit;
        // This account on another PC: the buttons ask that PC by its name.
        const holder = lock.elsewhere || lock.holder || "someone";
        // A ducky's lock names the ducky ("AI (your ducky Builder)"), never just its person.
        const who = lock.mine ? (lock.holder || "You") : lock.elsewhere && !lock.holder?.startsWith("AI") ? `You on ${lock.elsewhere}` : holder;
        const lease = lock.leaseLeft != null ? (lock.leaseLeft > 0 ? `${durationText(lock.leaseLeft)} left` : "ran out") : "";
        return (
          <Row key={lock.unit} name={name}
            sub={[`Locked by ${who === "You" ? "you" : who}`, lock.since ? `since ${clockText(lock.since)}` : "", lease].filter(Boolean).join(" · ")}
            actions={lock.mine ? (
              api?.plugin_scope_unlock ? <Btn disabled={busy} label={`Release ${name}`} onClick={() => void release(lock.unit)}>Release</Btn> : undefined
            ) : <>
              {api?.plugin_scope_ask ? <Btn disabled={busy} label={`Ask ${holder} to release ${name}`}
                onClick={() => void ask(lock.unit, holder, name)}>Ask</Btn> : null}
              {api?.plugin_scope_break ? <Btn disabled={busy} label={`Break ${holder}'s lock on ${name}`}
                onClick={() => void breakLock(lock.unit, holder, name)}>Break</Btn> : null}
            </>} />
        );
      }) : <p className="plugin-scope-empty">Nothing is locked.</p>}
    </>
  );

  const historyTab = versions ? (
    <>
      <Intro actions={<Btn onClick={() => setVersions(null)}>Back</Btn>}>Versions of {versions.key}</Intro>
      {versions.entries.map((v) => (
        <Row key={v.seq} lead={<span className="plugin-scope-seq">#{v.seq}</span>} name={historyWho(v)}
          sub={[whenText(v.at), v.deleted ? "Deleted" : v.size ? formatBytes(v.size) : ""].filter(Boolean).join(" · ")}
          actions={api?.plugin_scope_restore ? <Btn disabled={busy} label={`Restore ${versions.key} to #${v.seq}`}
            onClick={() => void restore("item", v.seq, { kind: versions.kind, key: versions.key })}>Restore</Btn> : undefined} />
      ))}
    </>
  ) : (
    <>
      <Intro>Every change sent to {label}, newest first. Undo or restore makes a new change everyone gets.</Intro>
      {history?.entries.length ? history.entries.map((e) => (
        <div key={e.seq}>
          <Row lead={<span className="plugin-scope-seq">#{e.seq}</span>}
            name={e.message || `${e.count ?? e.items?.length ?? 0} ${(e.count ?? e.items?.length ?? 0) === 1 ? "item" : "items"} changed`}
            sub={[historyWho(e), e.device || "", e.via ? `sent by ${e.via}'s live group` : "", whenText(e.at)]
              .filter(Boolean).join(" · ")}
            actions={<>
              {e.items?.length ? <Btn pressed={openSeq === e.seq} onClick={() => setOpenSeq(openSeq === e.seq ? null : e.seq)}>
                {`${e.count ?? e.items.length} ${(e.count ?? e.items.length) === 1 ? "item" : "items"}`}
              </Btn> : null}
              {api?.plugin_scope_restore ? <>
                {/* The move to the new sync can't be undone (the server refuses it too). */}
                {e.seq === 1 && e.by === IMPORTED_BY ? null
                  : <Btn disabled={busy} label={`Undo #${e.seq}`} onClick={() => void restore("undo", e.seq)}>Undo</Btn>}
                <Btn disabled={busy} label={`Restore to #${e.seq}`} onClick={() => void restore("to", e.seq)}>Restore to here</Btn>
              </> : null}
            </>} />
          {openSeq === e.seq ? (
            <div className="plugin-scope-items">
              {(e.items ?? []).map(([kind, key]) => (
                <Row key={`${kind}:${key}`} name={key}
                  actions={<Btn label={`Versions of ${key}`} onClick={() => void loadVersions(kind, key)}>Versions</Btn>} />
              ))}
            </div>
          ) : null}
        </div>
      )) : <p className="plugin-scope-empty">{history ? "No history yet." : "Loading…"}</p>}
      {history?.more ? (
        <p className="plugin-scope-empty">
          <button type="button" className="plugin-scope-link"
            onClick={() => void loadHistory(history.entries[history.entries.length - 1]?.seq)}>
            Older
          </button>
        </p>
      ) : null}
    </>
  );

  const peopleTab = (
    <>
      <Intro>Who's in {label}, what they're doing in {plugin}, and who's live together.</Intro>
      {people.length ? people.map((row) => {
        const group = row.live ? groups.find((g) => g.room === row.live) : undefined;
        const live = row.me && s.live?.on ? groups.find((g) => g.mine) : group;
        const hosting = live && (row.me ? live.hostIsMe : live.hostDevice === row.deviceId);
        const liveText = !live ? "Not live" : hosting ? "Hosting" : `In ${groupName(live)}'s group`;
        return (
          <Row key={row.key} lead={<PresenceAvatar row={row} plugin={plugin} labelled={false} />}
            name={row.me || row.self ? `You (${row.name})` : row.name}
            sub={[row.device, presenceText(row, plugin), row.since ? `since ${clockText(row.since)}` : ""].filter(Boolean).join(" · ")}
            side={<span className={live ? "plugin-scope-live-dot" : undefined}>{liveText}</span>}
            actions={live && !live.mine && !row.me && onJoin && s.live?.supported
              ? <Btn primary disabled={busy} label={`Join ${groupName(live)}`} onClick={() => onJoin(live.room)}>Join</Btn>
              : undefined} />
        );
      }) : <p className="plugin-scope-empty">Nobody else is here right now.</p>}
    </>
  );

  const settingsTab = (
    <>
      <Row name="Copy shown here" sub={`${scope?.kind === "shared" ? `${label}'s team copy` : "Local: only on this PC"}. Change it under Your team in Team settings.`} />
      {v2 && !lost && api?.plugin_scope_mode ? (
        <Row name="Sending"
          sub={manual ? "Changes go out and come in only when you send or get them." : "Changes go out and come in by themselves."}
          actions={<span className="plugin-scope-seg" role="radiogroup" aria-label="Sending">
            <button type="button" role="radio" aria-checked={!manual} className="plugin-scope-btn plugin-scope-btn--row" disabled={busy}
              onClick={() => { if (manual) void setMode("auto"); }}>Auto</button>
            <button type="button" role="radio" aria-checked={manual} className="plugin-scope-btn plugin-scope-btn--row" disabled={busy}
              onClick={() => { if (!manual) void setMode("manual"); }}>Manual</button>
          </span>} />
      ) : null}
      {storage ? (
        <Row name="Team storage" sub={storage.note || undefined}
          side={<span className={`plugin-scope-storage plugin-scope-storage--${storage.level}`}>
            <progress className="plugin-scope-meter" value={storage.pct} max={100} aria-label="Storage used" />
            <span>{storage.text}</span>
          </span>} />
      ) : null}
      {s.email || s.members ? (
        <Row name="Account" sub={[s.email ? `Signed in as ${s.email}` : "", s.members ? `${s.members} member${s.members === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ")} />
      ) : null}
      <p className="plugin-scope-empty">{DATA_AT_REST}{hasFolders(s) ? ` ${folderNote(s)}` : ""}</p>
    </>
  );

  const body: Record<ScopeTab, ReactNode> = {
    changes: changesTab, incoming: incomingTab, conflicts: conflictsTab, locks: locksTab,
    history: historyTab, people: peopleTab, settings: settingsTab,
  };
  return (
    <div className="plugin-scope-panel" id={id} role="region" aria-label={`Source control: ${label}`}>
      {hideTabs ? null : (
        <div className="plugin-scope-tabs" role="tablist" aria-label="Source control">
          {tabs.map((t) => (
            <button key={t.id} type="button" role="tab" aria-selected={shown === t.id}
              className={`plugin-scope-tab${shown === t.id ? " is-active" : ""}`} onClick={() => setTab(t.id)}>
              {t.label}
              {t.count ? <span className={`plugin-scope-count${t.alert ? " is-alert" : ""}`}>{t.count}</span> : null}
            </button>
          ))}
        </div>
      )}
      {error ? <p className="plugin-scope-msg plugin-scope-msg--error" role="alert">{error}</p> : null}
      {note ? <p className="plugin-scope-msg">{note}</p> : null}
      <div className="plugin-scope-panel__body" role={hideTabs ? undefined : "tabpanel"}>{body[shown]}</div>
    </div>
  );
}
