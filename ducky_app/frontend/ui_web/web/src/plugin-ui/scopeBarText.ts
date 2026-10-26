/** Scope bar wording. Pure so the 80% / full rules have one test. */
import type {
  PluginFolderShareUnit,
  PluginLiveGroup,
  PluginLivePeer,
  PluginScopeFolderUnit,
  PluginScopeLock,
  PluginScopePerson,
  PluginScopeStatus,
  PluginScopeUsage,
} from "../types/panel";

const UNITS = ["B", "KB", "MB", "GB", "TB"];

export function formatBytes(n: number): string {
  let v = Math.max(0, n || 0);
  let i = 0;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 || v >= 10 ? Math.round(v) : v.toFixed(1).replace(/\.0$/, "")} ${UNITS[i]}`;
}

export type StorageLine = { text: string; pct: number; level: "ok" | "warn" | "full"; note: string };

/** "34 MB of 5 GB" + level: warn from 80%, full at the limit. The note carries the
 * level as text so it never rests on colour alone. */
export function storageLine(usage: PluginScopeUsage | undefined): StorageLine | null {
  const used = usage?.usedBytes ?? 0;
  const limit = usage?.limitBytes ?? 0;
  if (!limit) return null;
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const level = used >= limit ? "full" : used * 10 >= limit * 8 ? "warn" : "ok";
  const note = level === "full" ? "Storage full" : level === "warn" ? `${pct}% used` : "";
  return { text: `${formatBytes(used)} of ${formatBytes(limit)}`, pct, level, note };
}

export function agoText(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Sync part of a shared bar: synced time, queued changes, or why it isn't syncing. */
export function syncText(status: PluginScopeStatus, nowMs: number): string {
  const pending = status.pending ?? 0;
  const waiting = pending ? ` · ${plural(pending, "change")} waiting` : "";
  // No provider: changes still queue here, and the reason is the whole phrase.
  if (status.state === "no_provider") return `${status.error || "Not connected"}${waiting}`;
  if (status.state === "offline") return `Offline${waiting}`;
  if (status.state === "error") return `Not synced${waiting}`;
  if (status.state === "unavailable") return status.error ? `Not available: ${status.error}` : "Not available";
  if (status.state === "reset") return "Reset on the server";
  const synced = status.syncedAt ? `Synced ${agoText(nowMs / 1000 - status.syncedAt)}` : "Not synced yet";
  return `${synced}${waiting}`;
}

/** Help text (same words as the web). */
export const DATA_AT_REST = "Plugin data on each PC is encrypted for the signed-in account.";

/** A shared copy the provider can't use now: read-only, with the reason. */
export function unavailableText(label: string, reason?: string): string {
  return `${label} isn't available right now${reason ? ` (${reason})` : ""}. Read-only until it's back.`;
}

/** A shared copy whose data was reset on the server: read-only until the user picks
 * Upload my copy or Clear my copy (`canChoose` false when this build offers neither). */
export function resetText(label: string, kind: string, canChoose: boolean): string {
  const what = `${label}'s ${kind} data was reset on the server.`;
  return canChoose
    ? `${what} Your copy here is read-only until you choose what to do with it.`
    : `${what} Your copy here is read-only.`;
}

export type HeldItem = {
  kind: string;
  key: string;
  state: string;
  /** "<key>: <reason>", one line. */
  text: string;
  /** Retry and Drop my change apply (refused or blocked). */
  fixable: boolean;
};

const HELD_REASONS: Record<string, string> = {
  refused: "the server refused it",
  conflict: "changed here and by someone else",
  locked_out: "locked by someone else",
};

/** Changes that aren't going out, each with its reason. `count` is the total; the list
 * may hold fewer (the status lists the newest only). */
export function notShared(status: PluginScopeStatus): { count: number; items: HeldItem[] } {
  const items = (status.items ?? []).map((item): HeldItem => {
    const tries = item.attempts ? ` after ${item.attempts} ${item.attempts === 1 ? "try" : "tries"}` : "";
    const reason = item.reason || HELD_REASONS[item.state] || (item.state === "blocked" ? `couldn't be sent${tries}` : "not sent");
    return {
      kind: item.kind,
      key: item.key,
      state: item.state,
      text: `${item.key}${item.deleted ? " (deleted here)" : ""}: ${reason}`,
      fixable: item.state === "refused" || item.state === "blocked",
    };
  });
  const counted = (status.refused ?? 0) + (status.blocked ?? 0) + (status.conflict ?? 0);
  return { count: Math.max(counted, items.length), items };
}

/** A shared copy whose access was lost: locked here, and when it leaves this PC. */
export function lostText(label: string, deleteAt?: number | null, locale?: string): string {
  const when = deleteAt
    ? new Date(deleteAt * 1000).toLocaleDateString(locale, { month: "long", day: "numeric" })
    : "";
  return when
    ? `You no longer have access to ${label}. Its data here is locked and leaves this PC on ${when} unless access comes back.`
    : `You no longer have access to ${label}. Its data here is locked until access comes back.`;
}

/** Title case of a provider's kind label ("space" → "Space"); "Space" when there is none. */
export function kindTitle(kindLabel?: string): string {
  const k = (kindLabel || "space").trim();
  return k.charAt(0).toUpperCase() + k.slice(1);
}

/** The question before a plugin's data switches: nothing is copied, and the plugin
 * restarts on the other copy. Leaving a shared copy (`from`), its changes still to send go
 * first, and the ones that aren't going out (`held`) stay in that copy on this PC. */
export function switchConfirm(
  plugin: string,
  to: { kind: string; label: string },
  from?: { label: string; pending?: number; held?: number },
): { title: string; message: string } {
  const shared = to.kind === "shared";
  const pending = from?.pending ?? 0;
  const held = from?.held ?? 0;
  const before = [
    pending ? `${plural(pending, "change")} not sent to ${from?.label} will be sent first.` : "",
    held ? `${plural(held, "change")} not shared stay${held === 1 ? "s" : ""} in ${from?.label}'s copy on this PC.` : "",
  ];
  return {
    title: `Switch ${plugin} to ${shared ? to.label : "Local"} data?`,
    message: [...before, `Nothing is copied. ${plugin} restarts with ${shared ? `${to.label}'s` : "its Local"} data.`]
      .filter(Boolean)
      .join(" "),
  };
}

/** The question before Drop my change: this PC's change goes, and is never sent. */
export function dropConfirm(label: string, key: string): { title: string; message: string } {
  return {
    title: `Drop your change to ${key}?`,
    message: `It is removed from this PC and never sent to ${label}.`,
  };
}

/** "A", "A and B", "A, B and C". */
export function listText(names: string[]): string {
  if (names.length < 2) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The question before Upload my copy / Clear my copy, after a reset on the server. Either
 * one is for this PC's whole copy of the space (`contents`: every plugin's data and its
 * workflows), so it names all of it. */
export function resetConfirm(
  action: "upload" | "clear",
  label: string,
  contents: string[] = [],
): { title: string; message: string } {
  const what = contents.length ? `: ${listText(contents)}` : "";
  return action === "upload"
    ? {
        title: `Upload your copy of ${label}?`,
        message: `This PC's whole copy of ${label}'s data is sent again, and everyone in ${label} gets it${what}.`,
      }
    : {
        title: `Clear your copy of ${label}?`,
        message: `This PC's whole copy of ${label}'s data is deleted${what}. ${label}'s data on the server comes down instead, and nothing is sent.`,
      };
}

// --------------------------------------------------------------------------- the one-line bar

export type PhraseTone = "" | "warn" | "error";

/** The bar's one status phrase. `action`: the phrase carries a button (Update now). */
export type StatusPhrase = { key: string; text: string; tone: PhraseTone; action?: "update" };

// --------------------------------------------------------------------------- declared folders

/** Folder files are kept as the plugin writes them: the first share and the help say so. */
export const FOLDER_NOTE =
  "This plugin's files are stored as it writes them on each member's PC; on the server and in transit they are encrypted.";

/** Why a folder can't be shared with a team from this PC, by reason (`urc`, `onedrive`,
 * `network`, `volume`, `case`); a reason already in words is its own text. */
export function folderRefusalText(reason: string, detail: { fs?: string; names?: string[] } = {}): string {
  if (reason === "urc") return "Carried by this island's UEFN revision control";
  if (reason === "onedrive") return "This folder is synced by OneDrive. Move it out of OneDrive to share it with a team.";
  if (reason === "network") return "This folder is on a network drive. Team folders work only on this PC's own drives.";
  if (reason === "volume") return `This folder is on a ${detail.fs || "non-NTFS"} drive. Team folders need an NTFS drive.`;
  if (reason === "case") {
    const names = detail.names?.length ? detail.names.join(" and ") : "Two names";
    return `${names} differ only by upper/lower case or accents. Rename one so the team can tell them apart.`;
  }
  return reason;
}

/** The plugin's declared folders that can't be shared from this PC, each with why. */
export const refusedFolders = (status: PluginScopeStatus) =>
  (status.teamFolders ?? []).filter((folder) => folder.refused);

/** The first declared folder that can't be shared from this PC, in words ("" when all can):
 * one such folder keeps the whole plugin off a team. */
export function folderRefusal(status: PluginScopeStatus): string {
  const refused = refusedFolders(status)[0]?.refused;
  return refused ? folderRefusalText(refused) : "";
}

/** The plugin keeps declared folders (their files are plaintext on each PC). */
export const hasFolders = (status: PluginScopeStatus) =>
  Boolean(status.teamFolders?.length || status.folders?.length);

/** What the help says of a folder plugin's files on each PC. */
export const folderNote = (status: PluginScopeStatus) => status.folderNote || FOLDER_NOTE;

/** "Getting Alpha's files… 40%": a switch to a team brings its folder files first. */
export function bindingText(team: string, pct?: number | null): string {
  return `Getting ${team}'s files…${typeof pct === "number" ? ` ${Math.round(pct)}%` : ""}`;
}

/** A first share sending the picked Local units into the team before the switch. */
export const sharingText = (team: string) => `Sending your files to ${team}…`;

/** "12 files · 226 MB" ("" when the counts aren't known). */
export function unitSizeText(files?: number, bytes?: number): string {
  if (files === undefined && bytes === undefined) return "";
  return [files !== undefined ? plural(files, "file") : "", bytes !== undefined ? formatBytes(bytes) : ""]
    .filter(Boolean)
    .join(" · ");
}

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

/** A folder unit's state, for the drawer's Folder units table. */
export function folderUnitStateText(unit: PluginScopeFolderUnit): string {
  if (unit.state === "changed" || unit.state === "changed_here") return "Changed here";
  if (unit.state === "locked" || unit.state === "locked_by") return `Locked by ${unit.mine ? "you" : unit.holder || "someone"}`;
  if (unit.state === "incoming") return "Update waiting";
  if (unit.state === "conflict") return "Changed here and by someone else";
  if (unit.state === "quarantined") {
    return unit.reason || `Changed here while ${unit.holder || "someone else"} holds it`;
  }
  if (unit.state === "refused") return unit.reason ? folderRefusalText(unit.reason) : "Can't be shared";
  return "Up to date";
}

/** Units changed here while someone else holds them: never sent until the person chooses. */
export const quarantinedUnits = (status: PluginScopeStatus) =>
  (status.folders ?? []).filter((unit) => unit.state === "quarantined");

/** A folder unit's key for a conflict or a held change: `<folder>/<unit>` (a unit already
 * named as a lock, `tree:…`, is its own). */
export function folderUnitKey(unit: PluginScopeFolderUnit): string {
  if (unit.unit.startsWith("tree:") || !unit.folder_id) return unit.unit;
  return `${unit.folder_id}/${unit.unit}`;
}

/** What lock calls name a folder unit by: its lock id from the host, else `<folder>/<unit>`,
 * which the host maps to it. */
export const folderUnitLockId = (unit: PluginScopeFolderUnit) => unit.unitId || folderUnitKey(unit);

/** This PC holds the unit's lock: the row says so, or a live lock of ours has its id. */
export function holdsFolderUnit(unit: PluginScopeFolderUnit, locks: PluginScopeLock[] = []): boolean {
  const id = folderUnitLockId(unit);
  return Boolean(unit.mine) || locks.some((lock) => lock.mine && lock.unit === id);
}

/** The question before a first share: the team has none of the plugin's files yet, or
 * (`partial`, "Copy Local into") some of them already. */
export function firstShareText(plugin: string, team: string, partial = false): { title: string; message: string } {
  return {
    title: `Share ${plugin}'s files with ${team}?`,
    message: partial
      ? `${team} has some of ${plugin}'s files already. Pick what else to copy from your Local copy; the rest stays only on this PC.`
      : `${team} has none of ${plugin}'s files yet. Pick what to copy from your Local copy; the rest stays only on this PC.`,
  };
}

/** "2 of 3 picked · 228 MB". */
export function pickedText(units: PluginFolderShareUnit[], picked: Set<string>): string {
  const chosen = units.filter((unit) => picked.has(unit.unit));
  const bytes = chosen.reduce((sum, unit) => sum + (unit.bytes ?? 0), 0);
  return `${chosen.length} of ${units.length} picked${chosen.length ? ` · ${formatBytes(bytes)}` : ""}`;
}

/** The question before Discard mine: this PC's change to the unit goes, the team's comes back. */
export function discardConfirm(name: string, label: string): { title: string; message: string } {
  return {
    title: `Discard your changes to ${name}?`,
    message: `${name} goes back to ${label}'s version. What you changed here is not kept.`,
  };
}

/** "40 s", "3 min", "2 h". */
export function durationText(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

/** "3:02 PM": a time of day (epoch seconds). */
export function clockText(epochSeconds: number, locale?: string): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" });
}

const MB = 1024 ** 2;

/** "412 of 1,200 MB": both in the total's unit. */
export function sentText(sent: number, total: number): string {
  const unit = total >= MB ? MB : 1024;
  const n = (v: number) => Math.round(Math.max(0, v) / unit).toLocaleString("en-US");
  return `${n(sent)} of ${n(total)} ${unit === MB ? "MB" : "KB"}`;
}

/** Locks held by someone else. */
export const foreignLocks = (status: PluginScopeStatus) => (status.locks ?? []).filter((lock) => !lock.mine);

/** Changes not sent yet: the status's count, else the queued and sending rows. */
function toSend(status: PluginScopeStatus): number {
  if (typeof status.pending === "number") return status.pending;
  return (status.changes?.outgoing ?? []).filter((c) => c.state === "queued" || c.state === "sending").length;
}

/**
 * The one phrase the bar shows, highest priority first: a switch getting a team's files, why
 * nothing syncs, a reset, a folder that can't be shared, conflicts, units changed here while
 * someone else holds them, a lock, updates waiting for Update now, an import, an upload, what
 * is queued, waiting, paused, up to date.
 */
export function statusPhrase(status: PluginScopeStatus, nowMs: number): StatusPhrase {
  const scope = status.scope;
  const label = scope?.label || "This space";
  const kind = (status.kindLabel || "space").toLowerCase();
  const state = status.state || "";
  const pending = toSend(status);
  const queued = pending ? ` · ${pending} to send` : "";
  if (status.binding) {
    return { key: "binding", text: bindingText(status.binding.team || label, status.binding.pct), tone: "" };
  }
  const refusal = folderRefusal(status);
  if (scope?.kind !== "shared") {
    if (refusal) return { key: "folder_refused", text: refusal, tone: "" };
    // Folder files aren't sealed on the PC: only api.data's are.
    return { key: "local", text: hasFolders(status) ? "Only on this PC" : `Only on this PC · ${DATA_AT_REST}`, tone: "" };
  }
  if (state === "lost") return { key: "lost", text: lostText(label, status.deleteAt), tone: "error" };
  if (state === "locked") {
    return { key: "key", text: "Waiting for your account's data key. Read-only until you're back online.", tone: "warn" };
  }
  if (state === "offline" || state === "error") {
    // The reason (often a raw network error) is the Changes tab's, never the bar's.
    const wait = status.nextTry ? status.nextTry - nowMs / 1000 : 0;
    const retry = wait > 0 ? ` · retry in ${durationText(wait)}` : "";
    return { key: "not_synced", text: `${state === "offline" ? "Offline" : "Couldn't sync"}${queued}${retry}`, tone: "error" };
  }
  if (state === "no_provider") {
    return { key: "no_provider", text: `${status.error || "Not connected: Ducky Account isn't running"}${queued}`, tone: "error" };
  }
  if (state === "unavailable") return { key: "unavailable", text: unavailableText(label, status.error), tone: "error" };
  if (state === "reset") return { key: "reset", text: `${label}'s ${kind} data was reset on the server`, tone: "warn" };
  if (refusal) return { key: "folder_refused", text: refusal, tone: "warn" };
  const conflicts = status.conflicts?.length || status.conflict || 0;
  if (conflicts) return { key: "conflict", text: plural(conflicts, "conflict"), tone: "warn" };
  const quarantined = quarantinedUnits(status);
  if (quarantined.length) {
    const first = quarantined[0];
    return {
      key: "quarantined",
      text: quarantined.length > 1
        ? `${quarantined.length} units changed here while someone else holds them`
        : `${first.name}: ${lowerFirst(folderUnitStateText(first))}`,
      tone: "warn",
    };
  }
  const locked = foreignLocks(status);
  if (locked.length) {
    const first = locked[0];
    const more = locked.length > 1 ? ` and ${locked.length - 1} more` : "";
    return {
      key: "locked",
      text: first.elsewhere
        ? `Open on your other PC, ${first.elsewhere}: ${first.name || first.unit}${more} (read-only on this PC)`
        : `Locked by ${first.holder || "someone"}: ${first.name || first.unit}${more} (read-only for you)`,
      tone: "warn",
    };
  }
  const changes = status.changes ?? {};
  const incoming = changes.incoming ?? 0;
  if (changes.held && incoming) {
    const by = changes.incomingBy?.length ? ` from ${listText(changes.incomingBy)}` : " waiting";
    return { key: "updates", text: `${plural(incoming, "update")}${by}`, tone: "", action: "update" };
  }
  if (state === "importing" || typeof status.importing === "number") {
    return { key: "importing", text: `Moving ${label}'s data to the new sync… ${Math.round(status.importing ?? 0)}%`, tone: "" };
  }
  if (changes.totalBytes) {
    return { key: "sending", text: `Sending ${sentText(changes.sentBytes ?? 0, changes.totalBytes)}`, tone: "" };
  }
  // Folder units refused (a name clash) don't go out either.
  const held = notShared(status).count + (status.folders ?? []).filter((unit) => unit.state === "refused").length;
  const parts = [
    pending ? `${pending} to send` : "",
    incoming ? `${incoming} incoming` : "",
    held ? `${held} can't be sent` : "",
  ].filter(Boolean);
  if (parts.length) return { key: "queue", text: parts.join(" · "), tone: held ? "warn" : "" };
  if (state === "waiting") return { key: "waiting", text: `Waiting for ${label}'s data…`, tone: "" };
  if (scope.readOnly || state === "paused") {
    return { key: "paused", text: status.pausedText || `${label} is paused. Read-only.`, tone: "warn" };
  }
  if (!status.syncedAt && !status.head) return { key: "up_to_date", text: "Not synced yet", tone: "" };
  const when = status.syncedAt ? agoText(nowMs / 1000 - status.syncedAt) : "";
  return { key: "up_to_date", text: ["Up to date", when].filter(Boolean).join(" · "), tone: "" };
}

/** The Source control panel's tabs. */
export type ScopeTab = "changes" | "incoming" | "conflicts" | "locks" | "history" | "people" | "settings";

/** The tab the bar's status opens: where what it says is explained and acted on. */
export function phraseTab(key: string): ScopeTab {
  if (key === "conflict" || key === "quarantined") return "conflicts";
  if (key === "locked") return "locks";
  if (key === "updates") return "incoming";
  if (key === "up_to_date") return "history";
  if (key === "local" || key === "folder_refused" || key === "paused" || key === "key") return "settings";
  return "changes";
}

/** A group's name in the dropdown: its host, else (the host left) its first member. */
export function groupName(group: PluginLiveGroup): string {
  if (group.hostIsMe) return "You";
  return group.host || `${group.members[0]?.name || "A teammate"}'s group`;
}

/** editing: they (or one of their duckies) changed something here in the last 2 minutes;
 * viewing: the plugin is open on their PC; away: in the space, not in this plugin. */
export type PresenceState = "editing" | "viewing" | "away";

/** One person on one PC, as the bar's avatars and the drawer's People table show them. */
export type Presence = {
  key: string;
  /** The person (their user id, else name): their avatar's colour comes from it. */
  id: string;
  name: string;
  device: string;
  /** This PC. */
  me: boolean;
  /** This account on another PC: "You on MainPC". */
  self: boolean;
  state: PresenceState;
  /** What they edit here themselves, by name, each once. */
  editing: string[];
  /** What their duckies edit here. */
  duckies: { ducky: string; unitName: string }[];
  /** Epoch seconds: since when they edit (their oldest edit still going), else 0. */
  since: number;
  /** Their PC's device id, and the live room they are in for this plugin ("" when not live). */
  deviceId: string;
  live: string;
};

const STATE_ORDER: Record<PresenceState, number> = { editing: 0, viewing: 1, away: 2 };

/** The people in a space, one row per person and PC (every edit of theirs folded in): this
 * PC first, then whoever edits, views, then the rest, by name. A roster row with a
 * `plugins` map says what each does in `pluginId`; an older activity row says it itself. */
export function presence(people: PluginScopePerson[] | undefined, pluginId: string): Presence[] {
  const rows = new Map<string, Presence>();
  for (const p of people ?? []) {
    const name = p.name || p.user_name || p.user || "Someone";
    const device = p.device || "";
    const key = `${p.me ? "me" : p.user || name}:${device}`;
    const row: Presence = rows.get(key)
      ?? { key, id: p.user || name, name, device, me: Boolean(p.me), self: Boolean(p.self) && !p.me, state: "away", editing: [], duckies: [], since: 0,
        deviceId: p.deviceId || "", live: "" };
    rows.set(key, row);
    const here = p.plugins ? p.plugins[pluginId] : undefined;
    if (here?.live) row.live = here.live;
    const edits = p.plugins
      ? here?.editing ?? []
      : p.doing === "editing" ? [{ unitName: p.unitName, actor: p.actor, ducky: p.ducky, since: p.since }] : [];
    const viewing = p.plugins ? Boolean(here?.viewing) : p.doing === "viewing";
    for (const e of edits) {
      const unit = e.unitName || "";
      if (e.actor === "agent") {
        if (!row.duckies.some((d) => d.ducky === (e.ducky || "") && d.unitName === unit)) {
          row.duckies.push({ ducky: e.ducky || "", unitName: unit });
        }
      } else if (unit && !row.editing.includes(unit)) {
        row.editing.push(unit);
      }
      const since = e.since ?? 0;
      if (since && (!row.since || since < row.since)) row.since = since;
    }
    if (edits.length) row.state = "editing";
    else if (viewing && row.state === "away") row.state = "viewing";
  }
  return [...rows.values()].sort((a, b) =>
    Number(b.me) - Number(a.me) || STATE_ORDER[a.state] - STATE_ORDER[b.state] || a.name.localeCompare(b.name));
}

/** The others in this plugin now (viewing or editing): who the bar's avatars show. */
export const othersHere = (rows: Presence[]) => rows.filter((row) => !row.me && row.state !== "away");

/** "Untitled island 1", "Untitled island 1 and Trailer 1", "Untitled island 1 +2". */
function unitsText(units: string[]): string {
  if (units.length <= 2) return listText(units);
  return `${units[0]} +${units.length - 1}`;
}

/** An AI's work always says AI, even under the person's own account: "AI (your ducky
 * Builder)", "AI (Ana's ducky Builder)", or "AI" for an outside AI with no ducky's name. */
export function aiText(owner: string, ducky?: string): string {
  return ducky ? `AI (${owner} ducky ${ducky})` : "AI";
}

/** Who made a change in history: an AI's change says AI ("AI (tas's ducky Builder)",
 * "AI, for tas"), never only the account it ran under. */
export function historyWho(entry: { by?: string; actor?: string; ducky?: string }): string {
  const by = entry.by || "Someone";
  if (entry.actor !== "agent") return by;
  return entry.ducky ? `AI (${by}'s ducky ${entry.ducky})` : `AI, for ${by}`;
}

/** What one person does here, for the drawer and an avatar's label: "Editing Untitled
 * island 1", "Their ducky Builder is editing Arena", "Viewing", "Not in Forge". */
export function presenceText(row: Presence, plugin = "this plugin"): string {
  const parts: string[] = [];
  if (row.editing.length) parts.push(`Editing ${unitsText(row.editing)}`);
  else if (row.state === "editing" && !row.duckies.length) parts.push("Editing");
  for (const d of row.duckies) {
    const who = aiText(row.me || row.self ? "your" : "their", d.ducky);
    parts.push(d.unitName ? `${who} is editing ${d.unitName}` : `${who} is editing`);
  }
  if (parts.length) return parts.join(" · ");
  return row.state === "viewing" ? "Viewing" : `Not in ${plugin}`;
}

/** "IK" for "iliya kovachki", "A" for "Ana" or "ana@x.com". */
export function initials(name: string): string {
  const words = (name.split("@")[0] || name).split(/[\s._-]+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[words.length - 1][0] : (words[0] || "?")[0];
  return letters.toUpperCase();
}

/** Avatar colours (`plugin-scope-avatar--tone-N`). */
export const AVATAR_TONES = 12;

/** A steady colour (0 to AVATAR_TONES - 1) per person, so their avatar keeps it on every PC. */
export function personTone(key: string): number {
  let h = 0;
  for (let i = 0; i < key.length; i += 1) h = (h * 31 + key.charCodeAt(i)) % 7919;
  return h % AVATAR_TONES;
}

/** A refused write, for the alerts system: "Forge: MEGA_Duel_Stadium is locked by Ana since 3:02 PM.
 * Your change wasn't saved." Without a holder, the refusal's own reason. */
export function refusedWriteText(
  plugin: string,
  write: { name?: string; holder?: string; elsewhere?: string; since?: number; error?: string },
  locale?: string,
): string {
  if (!write.holder) return `${plugin}: your change wasn't saved.${write.error ? ` ${write.error}` : ""}`;
  const since = write.since ? ` since ${clockText(write.since, locale)}` : "";
  // The same account on another PC: still one PC at a time.
  if (write.elsewhere) {
    return `${plugin}: ${write.name || "it"} is open on your other PC, ${write.elsewhere}${since}, so it's locked on this PC. Your change wasn't saved.`;
  }
  return `${plugin}: ${write.name || "it"} is locked by ${write.holder}${since}. Your change wasn't saved.`;
}

/** The question before Break: the holder's unsent edits become a conflict. */
export function breakConfirm(holder: string, name: string): { title: string; message: string } {
  return {
    title: `Break ${holder}'s lock on ${name}?`,
    message:
      `${holder} is told, and what ${holder} changed in ${name} and hasn't sent yet becomes a conflict ` +
      "on their PC. Nothing of theirs is lost.",
  };
}

/** The question before a restore: always a new change everyone gets, never a rewrite. */
export function restoreConfirm(
  action: "item" | "to" | "undo",
  label: string,
  what: { plugin: string; seq: number; key?: string },
): { title: string; message: string } {
  const after = `It is a new change everyone in ${label} gets; the history stays as it is.`;
  if (action === "item") {
    return { title: `Restore ${what.key} to #${what.seq}?`, message: `${what.key} goes back to how it was at #${what.seq}. ${after}` };
  }
  if (action === "undo") {
    return { title: `Undo change #${what.seq}?`, message: `Each item it changed goes back to how it was before #${what.seq}. ${after}` };
  }
  return {
    title: `Restore ${what.plugin} to #${what.seq}?`,
    message: `Every item goes back to how it was at #${what.seq}, and items made after it are deleted. ${after}`,
  };
}

/** What a conflict is about, for the drawer. */
export function conflictText(reason: string | undefined, by: string | undefined): string {
  const who = by || "Someone";
  if (reason === "deleted") return `${who} deleted this; you changed it`;
  if (reason === "mine_deleted") return `You deleted this; ${who} changed it`;
  if (reason === "lock_broken") return `Your lock was taken by ${who}; your unsent change is kept`;
  return `Changed here and by ${who}`;
}

/** One outgoing change's state, for the drawer. */
export function outgoingStateText(state: string, reason?: string, attempts?: number): string {
  if (state === "sending") return "Sending";
  if (state === "refused") return `Refused: ${reason || "the server refused it"}`;
  if (state === "blocked") {
    const tries = attempts ? ` after ${attempts} ${attempts === 1 ? "try" : "tries"}` : "";
    return `Couldn't be sent${tries}${reason ? `: ${reason}` : ""}`;
  }
  if (state === "locked_out") return reason || "Locked by someone else";
  if (state === "conflict") return "Changed here and by someone else";
  if (state === "quarantined") return reason || "Changed here while someone else holds it";
  if (state === "refetch") return "Getting the team's version again";
  return "Queued";
}

/** A live teammate's name on the bar and in the drawer. */
export const livePeerName = (peer: PluginLivePeer) => peer.name || peer.label || "A teammate";

