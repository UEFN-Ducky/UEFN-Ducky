import { describe, expect, it } from "vitest";
import type { PluginScopeStatus } from "../types/panel";
import {
  aiText,
  bindingText,
  breakConfirm,
  clockText,
  conflictText,
  discardConfirm,
  dropConfirm,
  durationText,
  firstShareText,
  folderRefusal,
  folderRefusalText,
  folderUnitKey,
  folderUnitLockId,
  folderUnitStateText,
  historyWho,
  holdsFolderUnit,
  initials,
  listText,
  groupName,
  lostText,
  notShared,
  othersHere,
  outgoingStateText,
  personTone,
  phraseTab,
  pickedText,
  presence,
  presenceText,
  refusedWriteText,
  resetConfirm,
  resetText,
  restoreConfirm,
  sentText,
  sharingText,
  statusPhrase,
  storageLine,
  switchConfirm,
  syncText,
  unavailableText,
  unitSizeText,
} from "./scopeBarText";

const GB = 1024 ** 3;
const NOW = 1_000_000_000_000;

/** A shared copy that is up to date, with `extra` on top. */
const shared = (extra: PluginScopeStatus = {}): PluginScopeStatus => ({
  visible: true, state: "ok", pending: 0, head: 482, syncedAt: NOW / 1000 - 120, kindLabel: "Team",
  scope: { kind: "shared", label: "Alpha", spaceId: "s1", readOnly: false }, ...extra,
});
const ANA_LOCK = { unit: "tree:f/1", name: "MEGA_Duel_Stadium", holder: "Ana", since: 0, leaseLeft: 240 };

describe("scope bar text", () => {
  it("warns from 80%, says full at the limit, and names the level in text", () => {
    expect(storageLine({ usedBytes: 34 * 1024 ** 2, limitBytes: 5 * GB })).toMatchObject({
      text: "34 MB of 5 GB",
      level: "ok",
      note: "",
    });
    expect(storageLine({ usedBytes: 4 * GB, limitBytes: 5 * GB })).toMatchObject({ level: "warn", note: "80% used" });
    expect(storageLine({ usedBytes: 5 * GB, limitBytes: 5 * GB })).toMatchObject({ level: "full", note: "Storage full" });
    expect(storageLine({ usedBytes: 1 })).toBeNull();
  });

  it("says when it synced, what is queued, and the switch warning", () => {
    expect(syncText({ syncedAt: 100, pending: 0, state: "ok" }, 105_000)).toBe("Synced 5 s ago");
    expect(syncText({ syncedAt: 100, pending: 3, state: "offline" }, 105_000)).toBe("Offline · 3 changes waiting");
    expect(switchConfirm("BrainRot TCG", { kind: "shared", label: "Alpha Studio" })).toEqual({
      title: "Switch BrainRot TCG to Alpha Studio data?",
      message: "Nothing is copied. BrainRot TCG restarts with Alpha Studio's data.",
    });
    expect(switchConfirm("BrainRot TCG", { kind: "personal", label: "Local" })).toEqual({
      title: "Switch BrainRot TCG to Local data?",
      message: "Nothing is copied. BrainRot TCG restarts with its Local data.",
    });
  });

  it("says a lost space's copy is locked and when it leaves this PC", () => {
    const at = new Date(2026, 9, 15, 12).getTime() / 1000;
    expect(lostText("Alpha Studio", at, "en-US")).toBe(
      "You no longer have access to Alpha Studio. Its data here is locked and leaves this PC on October 15 unless access comes back.",
    );
    expect(lostText("Alpha Studio")).toBe("You no longer have access to Alpha Studio. Its data here is locked until access comes back.");
  });

  it("names why nothing syncs: no provider, unavailable, reset", () => {
    const noProvider = { state: "no_provider", pending: 2, error: "Not connected: Ducky Account isn't running" };
    expect(syncText(noProvider, 0)).toBe("Not connected: Ducky Account isn't running · 2 changes waiting");
    expect(syncText({ state: "no_provider", pending: 1 }, 0)).toBe("Not connected · 1 change waiting");
    expect(syncText({ state: "unavailable", error: "permission denied" }, 0)).toBe("Not available: permission denied");
    expect(syncText({ state: "unavailable" }, 0)).toBe("Not available");
    expect(syncText({ state: "reset", syncedAt: 100 }, 105_000)).toBe("Reset on the server");

    expect(unavailableText("Alpha Studio", "permission denied: uefn-ducky-store.teams")).toBe(
      "Alpha Studio isn't available right now (permission denied: uefn-ducky-store.teams). Read-only until it's back.",
    );
    expect(unavailableText("Alpha Studio")).toBe("Alpha Studio isn't available right now. Read-only until it's back.");
    expect(resetText("Alpha", "team", true)).toBe(
      "Alpha's team data was reset on the server. Your copy here is read-only until you choose what to do with it.",
    );
    expect(resetText("Alpha", "team", false)).toBe("Alpha's team data was reset on the server. Your copy here is read-only.");
  });

  it("lists each change that isn't shared with its reason, and which ones Retry / Drop fix", () => {
    const held = notShared({
      refused: 1, blocked: 2, conflict: 1,
      items: [
        { kind: "asset", key: "art/big.png", state: "refused", reason: "too big for your plan" },
        { kind: "doc", key: "scene_1", state: "blocked", attempts: 5 },
        { kind: "doc", key: "scene_2", state: "blocked", attempts: 1, deleted: true },
        { kind: "doc", key: "deck", state: "conflict" },
        { kind: "doc", key: "arena", state: "locked_out", reason: "locked by Ana" },
      ],
    });
    expect(held.count).toBe(5);
    expect(held.items.map((item) => item.text)).toEqual([
      "art/big.png: too big for your plan",
      "scene_1: couldn't be sent after 5 tries",
      "scene_2 (deleted here): couldn't be sent after 1 try",
      "deck: changed here and by someone else",
      "arena: locked by Ana",
    ]);
    expect(held.items.map((item) => item.fixable)).toEqual([true, true, true, false, false]);
    // The list holds the newest only; the count is the total.
    expect(notShared({ refused: 60, items: [] }).count).toBe(60);
    expect(notShared({}).count).toBe(0);
  });

  it("the switch says what is sent first and what stays; drop and reset ask first", () => {
    expect(switchConfirm("BrainRot TCG", { kind: "personal", label: "Local" }, { label: "Alpha", pending: 3, held: 1 })).toEqual({
      title: "Switch BrainRot TCG to Local data?",
      message:
        "3 changes not sent to Alpha will be sent first. 1 change not shared stays in Alpha's copy on this PC. " +
        "Nothing is copied. BrainRot TCG restarts with its Local data.",
    });
    expect(switchConfirm("BrainRot TCG", { kind: "shared", label: "Beta" }, { label: "Alpha", pending: 1 }).message).toBe(
      "1 change not sent to Alpha will be sent first. Nothing is copied. BrainRot TCG restarts with Beta's data.",
    );
    expect(switchConfirm("BrainRot TCG", { kind: "shared", label: "Beta" }, { label: "Alpha", pending: 0, held: 0 }).message).toBe(
      "Nothing is copied. BrainRot TCG restarts with Beta's data.",
    );
    expect(dropConfirm("Alpha", "scene_1")).toEqual({
      title: "Drop your change to scene_1?",
      message: "It is removed from this PC and never sent to Alpha.",
    });
    // Upload and Clear are for the space's whole copy here: they name all of it.
    expect(resetConfirm("upload", "Alpha", ["BrainRot TCG", "Forge", "Workflows"])).toEqual({
      title: "Upload your copy of Alpha?",
      message: "This PC's whole copy of Alpha's data is sent again, and everyone in Alpha gets it: BrainRot TCG, Forge and Workflows.",
    });
    expect(resetConfirm("clear", "Alpha", ["Workflows"])).toEqual({
      title: "Clear your copy of Alpha?",
      message: "This PC's whole copy of Alpha's data is deleted: Workflows. Alpha's data on the server comes down instead, and nothing is sent.",
    });
    expect(resetConfirm("clear", "Alpha").message).toBe(
      "This PC's whole copy of Alpha's data is deleted. Alpha's data on the server comes down instead, and nothing is sent.",
    );
    expect([listText([]), listText(["A"]), listText(["A", "B"]), listText(["A", "B", "C"])]).toEqual(
      ["", "A", "A and B", "A, B and C"]);
  });
});

describe("the bar's one status phrase", () => {
  const MB = 1024 ** 2;
  const busy = (): PluginScopeStatus => shared({
    conflicts: [{ kind: "doc", key: "deck", by: "Ana" }],
    locks: [ANA_LOCK, { unit: "doc:x", name: "x", holder: "Ben" }, { unit: "doc:y", mine: true }],
    changes: { held: true, incoming: 2, incomingBy: ["Ana"], sentBytes: 412 * MB, totalBytes: 1200 * MB },
    importing: 40,
    pending: 3,
  });
  const say = (s: PluginScopeStatus) => statusPhrase(s, NOW).text;

  it("a state that stops syncing comes before everything else", () => {
    expect(say({ ...busy(), state: "lost" })).toMatch(/^You no longer have access to Alpha\./);
    expect(say({ ...busy(), state: "locked" })).toBe("Waiting for your account's data key. Read-only until you're back online.");
    // The raw reason stays off the bar: the Changes tab shows it.
    expect(say({ ...busy(), state: "error", error: "HTTP 502", nextTry: NOW / 1000 + 40 })).toBe("Couldn't sync · 3 to send · retry in 40 s");
    expect(say({ ...busy(), state: "offline", error: "Network error: <urlopen error [WinError 10060] ...>" })).toBe("Offline · 3 to send");
    expect(say({ ...busy(), state: "no_provider" })).toBe("Not connected: Ducky Account isn't running · 3 to send");
    expect(say({ ...busy(), state: "unavailable", error: "permission denied" })).toBe(
      "Alpha isn't available right now (permission denied). Read-only until it's back.");
    expect(say({ ...busy(), state: "reset" })).toBe("Alpha's team data was reset on the server");
    expect(statusPhrase({ ...busy(), state: "error" }, NOW).tone).toBe("error");
  });

  it("then conflicts, a lock, updates waiting, an import, an upload, the queue, up to date", () => {
    const s = busy();
    expect(say(s)).toBe("1 conflict");
    delete s.conflicts;
    expect(say(s)).toBe("Locked by Ana: MEGA_Duel_Stadium and 1 more (read-only for you)");
    s.locks = [{ unit: "doc:y", mine: true }];
    expect(statusPhrase(s, NOW)).toMatchObject({ text: "2 updates from Ana", action: "update" });
    s.changes = { ...s.changes, held: false };
    expect(say(s)).toBe("Moving Alpha's data to the new sync… 40%");
    delete s.importing;
    expect(say(s)).toBe("Sending 412 of 1,200 MB");
    s.changes = { incoming: 2 };
    expect(say(s)).toBe("3 to send · 2 incoming");
    s.pending = 0;
    s.changes = {};
    s.refused = 1;
    expect(statusPhrase(s, NOW)).toMatchObject({ text: "1 can't be sent", tone: "warn" });
    s.refused = 0;
    expect(say(s)).toBe("Up to date · 2 min ago");
    expect(say({ ...s, state: "waiting" })).toBe("Waiting for Alpha's data…");
    expect(say({ ...s, state: "paused", scope: { ...s.scope!, readOnly: true } })).toBe("Alpha is paused. Read-only.");
    expect(say({ ...s, head: undefined, syncedAt: null })).toBe("Not synced yet");
    expect(say({ ...s, scope: { kind: "personal", label: "Local", spaceId: "", readOnly: false } })).toMatch(/^Only on this PC/);
  });

  it("v1 counts work too: a held change and a conflict count", () => {
    expect(say(shared({ head: undefined, conflict: 2 }))).toBe("2 conflicts");
    expect(say(shared({ head: undefined, pending: 1 }))).toBe("1 to send");
  });

  it("one row per person and PC: this PC first, then who edits, views, the rest", () => {
    const roster = [
      { user: "u5", user_name: "Ed", plugins: { other: { viewing: true } } },
      { user: "u3", user_name: "Cy", plugins: { forge: { viewing: true } } },
      { user: "u2", user_name: "Ben", device: "BEN-PC", plugins: { forge: { editing: [{ unitName: "Arena", actor: "agent", ducky: "Builder" }] } } },
      { user: "u1", user_name: "Ana", device: "ANA-PC", plugins: { forge: { viewing: true, editing: [
        { unitName: "MEGA_Duel_Stadium", actor: "user", since: 200 },
        { unitName: "MEGA_Duel_Stadium", actor: "user", since: 100 },
        { unitName: "Crate", actor: "user", since: 150 },
      ] } } },
      { user: "me", user_name: "Me", me: true, device: "MainPC", plugins: { forge: { viewing: true } } },
    ];
    const rows = presence(roster, "forge");
    expect(rows.map((r) => [r.name, r.state])).toEqual([
      ["Me", "viewing"], ["Ana", "editing"], ["Ben", "editing"], ["Cy", "viewing"], ["Ed", "away"],
    ]);
    // Ana's edits fold into one row, each unit once, since her oldest edit.
    expect(rows[1]).toMatchObject({ device: "ANA-PC", editing: ["MEGA_Duel_Stadium", "Crate"], since: 100 });
    expect(rows.map((r) => presenceText(r, "Forge"))).toEqual([
      "Viewing", "Editing MEGA_Duel_Stadium and Crate", "AI (their ducky Builder) is editing Arena", "Viewing", "Not in Forge",
    ]);
    expect(othersHere(rows).map((r) => r.name)).toEqual(["Ana", "Ben", "Cy"]);
  });

  it("avatars: initials and a colour that is the same for a person on every PC", () => {
    expect(initials("iliya kovachki")).toBe("IK");
    expect(initials("Ana")).toBe("A");
    expect(initials("ana.b@x.com")).toBe("AB");
    expect(initials("")).toBe("?");
    expect(personTone("u1")).toBe(personTone("u1"));
    expect(personTone("u1")).not.toBe(personTone("u2"));
    const tones = new Set(["u1", "u2", "u3", "u4", "ana", "ben", "iliya", "tas"].map(personTone));
    expect([...tones].every((t) => t >= 0 && t < 12)).toBe(true);
    expect(tones.size).toBeGreaterThan(4);
  });

  it("words a refused write, Break, restores, conflicts and outgoing states", () => {
    const at = new Date(2026, 9, 17, 15, 2).getTime() / 1000;
    expect(clockText(at, "en-US")).toBe("3:02 PM");
    expect(refusedWriteText("Forge", { name: "MEGA_Duel_Stadium", holder: "Ana", since: at }, "en-US")).toBe(
      "Forge: MEGA_Duel_Stadium is locked by Ana since 3:02 PM. Your change wasn't saved.");
    expect(refusedWriteText("Forge", { error: "Alpha is paused." })).toBe("Forge: your change wasn't saved. Alpha is paused.");
    expect(breakConfirm("Ana", "MEGA_Duel_Stadium")).toEqual({
      title: "Break Ana's lock on MEGA_Duel_Stadium?",
      message: "Ana is told, and what Ana changed in MEGA_Duel_Stadium and hasn't sent yet becomes a conflict on their PC. " +
        "Nothing of theirs is lost.",
    });
    expect(restoreConfirm("item", "Alpha", { plugin: "Forge", seq: 7, key: "deck" })).toEqual({
      title: "Restore deck to #7?",
      message: "deck goes back to how it was at #7. It is a new change everyone in Alpha gets; the history stays as it is.",
    });
    expect(restoreConfirm("to", "Alpha", { plugin: "Forge", seq: 7 }).title).toBe("Restore Forge to #7?");
    expect(restoreConfirm("undo", "Alpha", { plugin: "Forge", seq: 7 }).title).toBe("Undo change #7?");
    expect(conflictText("deleted", "Ana")).toBe("Ana deleted this; you changed it");
    expect(conflictText(undefined, undefined)).toBe("Changed here and by Someone");
    expect(outgoingStateText("blocked", "file in use", 5)).toBe("Couldn't be sent after 5 tries: file in use");
    expect(outgoingStateText("refused", "too big")).toBe("Refused: too big");
    expect(outgoingStateText("queued")).toBe("Queued");
    expect([durationText(40), durationText(240), durationText(7200)]).toEqual(["40 s", "4 min", "2 h"]);
    expect(sentText(512 * 1024, 900 * 1024)).toBe("512 of 900 KB");
  });
});

describe("declared folders", () => {
  const LOCAL = { kind: "personal" as const, label: "Local", spaceId: "", readOnly: false };
  const URC = "Carried by this island's UEFN revision control";
  const say = (s: PluginScopeStatus) => statusPhrase(s, NOW).text;
  const declared = (...folders: { id: string; refused?: string }[]) => ({ teamFolders: folders });

  it("words each reason a folder can't be shared from this PC, and keeps the host's own words", () => {
    expect(folderRefusalText("urc")).toBe(URC);
    expect(folderRefusalText("onedrive")).toBe(
      "This folder is synced by OneDrive. Move it out of OneDrive to share it with a team.");
    expect(folderRefusalText("network")).toBe(
      "This folder is on a network drive. Team folders work only on this PC's own drives.");
    expect(folderRefusalText("volume", { fs: "FAT32" })).toBe("This folder is on a FAT32 drive. Team folders need an NTFS drive.");
    expect(folderRefusalText("volume")).toBe("This folder is on a non-NTFS drive. Team folders need an NTFS drive.");
    expect(folderRefusalText("case", { names: ["Scene.json", "scene.json"] })).toBe(
      "Scene.json and scene.json differ only by upper/lower case or accents. Rename one so the team can tell them apart.");
    expect(folderRefusalText("This folder is on a exFAT drive. Team folders need an NTFS drive.")).toBe(
      "This folder is on a exFAT drive. Team folders need an NTFS drive.");
    // One folder that can't be shared keeps the plugin off a team.
    expect(folderRefusal(shared(declared({ id: "noproject" }, { id: "design", refused: URC })))).toBe(URC);
    expect(folderRefusal(shared(declared({ id: "design", refused: "urc" })))).toBe(URC);
    expect(folderRefusal(shared(declared({ id: "scenes", refused: "" })))).toBe("");
    expect(folderRefusal(shared())).toBe("");
  });

  it("a switch getting a team's files comes first, even while the plugin still shows Local", () => {
    expect(bindingText("Alpha", 40.4)).toBe("Getting Alpha's files… 40%");
    expect(bindingText("Alpha")).toBe("Getting Alpha's files…");
    expect(sharingText("Alpha")).toBe("Sending your files to Alpha…");
    const local = shared({ scope: LOCAL, ...declared({ id: "scenes" }), state: "binding", binding: { team: "Alpha", pct: 40, at: 1 } });
    expect(statusPhrase(local, NOW)).toMatchObject({ key: "binding", text: "Getting Alpha's files… 40%" });
    expect(say(shared({ state: "error", binding: { team: "", pct: 0 }, conflicts: [{ kind: "doc", key: "deck" }] })))
      .toBe("Getting Alpha's files… 0%");
    expect(say(shared({ binding: null }))).toBe("Up to date · 2 min ago");
  });

  it("on Local, a folder that can't be shared says why; a folder plugin's files aren't called encrypted", () => {
    expect(statusPhrase(shared({ scope: LOCAL, ...declared({ id: "design", refused: URC }) }), NOW)).toMatchObject({
      key: "folder_refused", text: URC, tone: "",
    });
    expect(say(shared({ scope: LOCAL, ...declared({ id: "scenes" }) }))).toBe("Only on this PC");
    expect(say(shared({ scope: LOCAL }))).toBe("Only on this PC · Plugin data on each PC is encrypted for the signed-in account.");
    // On a team copy it is a warning ahead of conflicts.
    expect(statusPhrase(shared({ ...declared({ id: "scenes", refused: "onedrive" }), conflict: 1 }), NOW)).toMatchObject({
      key: "folder_refused", tone: "warn",
      text: "This folder is synced by OneDrive. Move it out of OneDrive to share it with a team.",
    });
  });

  it("units changed while someone else holds them come after conflicts and before locks", () => {
    const held = { folder_id: "scenes", unit: "MEGA_Duel_Stadium", name: "MEGA_Duel_Stadium", state: "quarantined",
      reason: "Changed here while Ana holds it" };
    const s = shared({ folders: [held], locks: [ANA_LOCK], conflicts: [{ kind: "doc", key: "deck" }] });
    expect(say(s)).toBe("1 conflict");
    delete s.conflicts;
    expect(statusPhrase(s, NOW)).toMatchObject({
      key: "quarantined", text: "MEGA_Duel_Stadium: changed here while Ana holds it", tone: "warn",
    });
    s.folders = [held, { ...held, unit: "Arena", name: "Arena" }];
    expect(say(s)).toBe("2 units changed here while someone else holds them");
    s.folders = [];
    expect(say(s)).toBe("Locked by Ana: MEGA_Duel_Stadium (read-only for you)");
  });

  it("updates waiting for Update now name who made them; a unit refused for a name clash counts as not sent", () => {
    expect(statusPhrase(shared({ changes: { held: true, incoming: 2, incomingBy: ["Ana", "Ben"] } }), NOW)).toEqual({
      key: "updates", text: "2 updates from Ana and Ben", tone: "", action: "update",
    });
    expect(say(shared({ changes: { held: true, incoming: 1 } }))).toBe("1 update waiting");
    // Applied by themselves: just incoming.
    expect(say(shared({ changes: { held: false, incoming: 2 } }))).toBe("2 incoming");
    const clash = "Lobby.json and lobby.json differ only by upper/lower case or accents. Rename one so the team can tell them apart.";
    expect(statusPhrase(shared({ folders: [{ folder_id: "scenes", unit: "Lobby", name: "Lobby", state: "refused", reason: clash }] }), NOW))
      .toMatchObject({ key: "queue", text: "1 can't be sent", tone: "warn" });
  });

  it("words each folder unit state, its size, and the first share and Discard mine questions", () => {
    const unit = (state: string, extra = {}) => folderUnitStateText({ unit: "u", name: "Arena", state, ...extra });
    expect(unit("synced")).toBe("Up to date");
    expect(unit("ok")).toBe("Up to date");
    expect(unit("up_to_date")).toBe("Up to date");
    expect(unit("changed")).toBe("Changed here");
    expect(unit("changed_here")).toBe("Changed here");
    expect(unit("incoming")).toBe("Update waiting");
    expect(unit("locked", { holder: "Ana" })).toBe("Locked by Ana");
    expect(unit("locked_by", { holder: "Ana" })).toBe("Locked by Ana");
    expect(unit("locked", { mine: true })).toBe("Locked by you");
    expect(outgoingStateText("quarantined", "Changed here while Ana holds it")).toBe("Changed here while Ana holds it");
    expect(outgoingStateText("quarantined")).toBe("Changed here while someone else holds it");
    expect(outgoingStateText("refetch")).toBe("Getting the team's version again");
    expect(unit("conflict")).toBe("Changed here and by someone else");
    expect(unit("quarantined", { holder: "Ben" })).toBe("Changed here while Ben holds it");
    expect(unit("quarantined", { reason: "Changed here while Ana holds it" })).toBe("Changed here while Ana holds it");
    expect(unit("refused", { reason: "case" })).toMatch(/^Two names differ only by upper\/lower case/);
    expect(unit("refused")).toBe("Can't be shared");
    expect(unitSizeText(12, 226 * 1024 ** 2)).toBe("12 files · 226 MB");
    expect(unitSizeText(1, 0)).toBe("1 file · 0 B");
    expect(unitSizeText()).toBe("");
    const units = [
      { unit: "a", name: "A", bytes: 2 * 1024 ** 2 },
      { unit: "b", name: "B", bytes: 1024 ** 2 },
      { unit: "c", name: "C" },
    ];
    expect(pickedText(units, new Set(["a", "b"]))).toBe("2 of 3 picked · 3 MB");
    expect(pickedText(units, new Set())).toBe("0 of 3 picked");
    expect(firstShareText("Forge", "Alpha")).toEqual({
      title: "Share Forge's files with Alpha?",
      message: "Alpha has none of Forge's files yet. Pick what to copy from your Local copy; the rest stays only on this PC.",
    });
    expect(firstShareText("Forge", "Alpha", true).message).toBe(
      "Alpha has some of Forge's files already. Pick what else to copy from your Local copy; the rest stays only on this PC.",
    );
    expect(discardConfirm("Arena", "Alpha")).toEqual({
      title: "Discard your changes to Arena?",
      message: "Arena goes back to Alpha's version. What you changed here is not kept.",
    });
  });

  it("names a folder unit for a conflict and for its lock, and knows when this PC holds it", () => {
    const arena = { folder_id: "scenes", unit: "Arena", name: "Arena", state: "up_to_date" };
    expect(folderUnitKey(arena)).toBe("scenes/Arena");
    expect(folderUnitKey({ folder_id: "design", unit: "1f0c-guid", name: "design", state: "ok" })).toBe("design/1f0c-guid");
    expect(folderUnitKey({ unit: "tree:scenes/4", name: "Vault", state: "ok" })).toBe("tree:scenes/4");
    expect(folderUnitKey({ unit: "Arena", name: "Arena", state: "ok" })).toBe("Arena");
    // The host's lock id when it gives one, else <folder>/<unit> for the host to map.
    expect(folderUnitLockId({ ...arena, unitId: "tree:scenes/ab12" })).toBe("tree:scenes/ab12");
    expect(folderUnitLockId(arena)).toBe("scenes/Arena");
    expect(holdsFolderUnit({ ...arena, mine: true })).toBe(true);
    expect(holdsFolderUnit({ ...arena, unitId: "tree:scenes/ab12" }, [{ unit: "tree:scenes/ab12", name: "Arena", mine: true }]))
      .toBe(true);
    expect(holdsFolderUnit({ ...arena, unitId: "tree:scenes/ab12" }, [{ unit: "tree:scenes/ab12", name: "Arena", holder: "Ana" }]))
      .toBe(false);
    expect(holdsFolderUnit(arena, [{ unit: "doc:Arena", name: "Arena", mine: true }])).toBe(false);
    expect(holdsFolderUnit({ unit: "tree:scenes/4", name: "Vault", state: "ok" }, [{ unit: "tree:scenes/4", mine: true }])).toBe(true);
  });

  it("one account on two PCs: the other PC holds it, and this PC says so", () => {
    const at = new Date(2026, 9, 19, 15, 2).getTime() / 1000;
    expect(refusedWriteText("Forge", { name: "Crate", holder: "you on MainPC", elsewhere: "MainPC", since: at }, "en-US"))
      .toBe("Forge: Crate is open on your other PC, MainPC since 3:02 PM, so it's locked on this PC. Your change wasn't saved.");
    const s = shared({ locks: [{ unit: "doc:crate", name: "Crate", holder: "you on MainPC", elsewhere: "MainPC" }] });
    expect(statusPhrase(s, NOW).text).toBe("Open on your other PC, MainPC: Crate (read-only on this PC)");
    const rows = presence([
      { name: "tas", user: "u1", device: "Laptop", me: true },
      { name: "tas", user: "u1", device: "MainPC", self: true, plugins: { forge: { editing: [{ unitName: "Crate" }] } } },
    ], "forge");
    expect(presenceText(rows[1], "Forge")).toBe("Editing Crate");
  });

  it("an AI's change says AI, never only the account it ran under", () => {
    expect(historyWho({ by: "tas", actor: "agent", ducky: "Builder" })).toBe("AI (tas's ducky Builder)");
    expect(historyWho({ by: "tas", actor: "agent" })).toBe("AI, for tas");
    expect(historyWho({ by: "tas", actor: "user" })).toBe("tas");
    expect(aiText("your", "Builder")).toBe("AI (your ducky Builder)");
    expect(aiText("their")).toBe("AI");
    const rows = presence([
      { name: "Ana", user: "u2", device: "ANA-PC", plugins: { forge: { editing: [{ unitName: "Arena", actor: "agent", ducky: "Builder" }] } } },
    ], "forge");
    expect(presenceText(rows[0], "Forge")).toBe("AI (their ducky Builder) is editing Arena");
  });
});

describe("the bar's status, tabs and live groups", () => {
  it("the status opens the tab that explains it", () => {
    expect(phraseTab("conflict")).toBe("conflicts");
    expect(phraseTab("quarantined")).toBe("conflicts");
    expect(phraseTab("locked")).toBe("locks");
    expect(phraseTab("updates")).toBe("incoming");
    expect(phraseTab("up_to_date")).toBe("history");
    expect(phraseTab("paused")).toBe("settings");
    expect(phraseTab("not_synced")).toBe("changes");
    expect(phraseTab("queue")).toBe("changes");
  });

  it("a group is named by its host, else by who's left in it", () => {
    expect(groupName({ room: "h:d1", host: "iliya", members: [] })).toBe("iliya");
    expect(groupName({ room: "h:me", host: "You", hostIsMe: true, members: [] })).toBe("You");
    expect(groupName({ room: "h:gone", host: "", members: [{ name: "Mo" }] })).toBe("Mo's group");
  });
});
