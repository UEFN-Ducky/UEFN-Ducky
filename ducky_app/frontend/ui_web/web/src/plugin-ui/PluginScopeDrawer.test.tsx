// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import type { PluginScopeStatus } from "../types/panel";
import { PluginScopeDrawer } from "./PluginScopeDrawer";

const api = vi.hoisted(() => ({
  plugin_scope_activity: vi.fn() as ReturnType<typeof vi.fn> | undefined,
  plugin_scope_sync: vi.fn(),
  plugin_scope_retry: vi.fn(),
  plugin_scope_drop: vi.fn(),
  plugin_scope_lock: vi.fn(),
  plugin_scope_unlock: vi.fn(),
  plugin_scope_break: vi.fn(),
  plugin_scope_ask: vi.fn(),
  plugin_scope_resolve: vi.fn(),
  plugin_scope_restore: vi.fn(),
  plugin_scope_history: vi.fn(),
  plugin_scope_mode: vi.fn(),
  plugin_scope_push: vi.fn(),
  plugin_scope_pull: vi.fn(),
  plugin_scope_save_unsent: vi.fn(),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));

const activity = api.plugin_scope_activity;
const onChanged = vi.fn();
beforeEach(() => {
  for (const fn of Object.values(api)) fn?.mockResolvedValue({ ok: true });
  api.plugin_scope_activity?.mockResolvedValue({});
  api.plugin_scope_history.mockResolvedValue({ ok: true, more: false, entries: [] });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  api.plugin_scope_activity = activity;
});

const status = (extra: PluginScopeStatus = {}): PluginScopeStatus => ({
  ok: true, visible: true, pluginLabel: "Forge", state: "ok", pending: 0, head: 12, mode: "auto",
  people: [], locks: [], ...extra,
  scope: { kind: "shared", label: "Alpha", spaceId: "s1", readOnly: false, ...extra.scope },
});

const drawer = (s: PluginScopeStatus, onJoin?: (room: string) => void) =>
  render(
    <ConfirmModalProvider>
      <PluginScopeDrawer pluginId="forge" status={s} onChanged={onChanged} onJoin={onJoin} />
    </ConfirmModalProvider>,
  );

/** Show one of the tabs. */
const go = (tab: RegExp | string) => fireEvent.click(screen.getByRole("tab", { name: tab }));
/** The rows of the tab shown: each row's name and its second line. */
const rows = () => [...document.querySelectorAll(".plugin-scope-row")].map((r) =>
  [r.querySelector(".plugin-scope-row__name")?.textContent ?? "", r.querySelector(".plugin-scope-row__sub")?.textContent ?? ""]);

const NOTE = "This plugin's files are stored as it writes them on each member's PC; on the server and in transit they are encrypted.";
const URC = "Carried by this island's UEFN revision control";

/** The confirm modal's main button ("Break lock (B)"). */
const confirmButton = (label: string) => screen.getByRole("button", { name: new RegExp(`^${label} \\(`) });

describe("source control tabs", () => {
  it("shows seven tabs with counts; a copy on the older sync has no Locks, History or People", () => {
    const view = drawer(status({
      changes: { outgoing: [{ kind: "doc", key: "deck", state: "queued" }], incoming: 2 },
      conflicts: [{ kind: "doc", key: "pack", by: "Ben" }],
      locks: [{ unit: "doc:x", name: "x", holder: "Ana" }],
    }));
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(
      ["Changes1", "Incoming2", "Conflicts1", "Locks1", "History", "People", "Settings"]);
    expect(screen.getByRole("tab", { name: "Changes1" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: /^Conflicts/ }).querySelector(".is-alert")).toBeTruthy();
    view.unmount();
    drawer(status({ engine: "v1", head: 0, people: undefined, locks: undefined }));
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Changes", "Incoming", "Conflicts", "Settings"]);
  });

  it("the bar picks the tab: it shows the one asked for and says when another is picked", () => {
    const picked = vi.fn();
    render(
      <ConfirmModalProvider>
        <PluginScopeDrawer pluginId="forge" status={status()} onChanged={onChanged} tab="locks" onTab={picked} />
      </ConfirmModalProvider>,
    );
    expect(screen.getByText("Nothing is locked.")).toBeTruthy();
    go("Settings");
    expect(picked).toHaveBeenCalledWith("settings");
  });

  it("People: you first, then who edits, views, the rest; who's hosting or in a group, with Join", async () => {
    const join = vi.fn();
    drawer(status({
      live: { supported: true, on: false, groups: [{ room: "h:d1", host: "Ana", hostDevice: "d1",
        members: [{ name: "Ana", device: "d1" }, { name: "Cy", device: "d3" }] }] },
      people: [
        { user_name: "Dee", device: "DEE-PC", deviceId: "d4", plugins: { other: { viewing: true } } },
        { user_name: "Cy", device: "CY-PC", deviceId: "d3", plugins: { forge: { viewing: true, live: "h:d1" } } },
        { user_name: "Ana", device: "ANA-PC", deviceId: "d1", plugins: { forge: { live: "h:d1", editing: [
          { unitName: "MEGA_Duel_Stadium", actor: "user" }, { unitName: "Crate", actor: "user" },
        ] } } },
        { user_name: "Ben", device: "BEN-PC", deviceId: "d2", plugins: { forge: { editing: [{ unitName: "Arena", actor: "agent", ducky: "Builder" }] } } },
        { user_name: "Me", me: true, device: "MainPC", deviceId: "d0", plugins: { forge: { viewing: true } } },
      ],
    }), join);
    go(/^People/);
    expect(rows()).toEqual([
      ["You (Me)", "MainPC · Viewing"],
      ["Ana", "ANA-PC · Editing MEGA_Duel_Stadium and Crate"],
      ["Ben", "BEN-PC · AI (their ducky Builder) is editing Arena"],
      ["Cy", "CY-PC · Viewing"],
      ["Dee", "DEE-PC · Not in Forge"],
    ]);
    const live = [...document.querySelectorAll(".plugin-scope-row__side")].map((s) => s.textContent);
    expect(live).toEqual(["Not live", "Hosting", "Not live", "In Ana's group", "Not live"]);
    fireEvent.click(screen.getAllByRole("button", { name: "Join Ana" })[0]);
    expect(join).toHaveBeenCalledWith("h:d1");
    await waitFor(() => expect(api.plugin_scope_activity).toHaveBeenCalledWith("forge"));
  });

  it("has the space looked at once when it opens, never on each new status (each look is a round)", async () => {
    const view = drawer(status());
    go(/^Locks/);
    expect(screen.getByText("Nothing is locked.")).toBeTruthy();
    await waitFor(() => expect(api.plugin_scope_activity).toHaveBeenCalledTimes(1));
    // The round's news comes back as a fresher status from the bar.
    view.rerender(
      <ConfirmModalProvider>
        <PluginScopeDrawer pluginId="forge" status={status({ locks: [{ unit: "doc:deck", name: "deck", holder: "Ana" }] })}
          onChanged={onChanged} />
      </ConfirmModalProvider>,
    );
    expect(screen.getByRole("button", { name: "Ask Ana to release deck" })).toBeTruthy();
    expect(api.plugin_scope_activity).toHaveBeenCalledTimes(1);

    // Without the call the panel still shows the status.
    view.unmount();
    api.plugin_scope_activity = undefined;
    drawer(status());
    go(/^Locks/);
    expect(screen.getByText("Nothing is locked.")).toBeTruthy();
  });

  it("locks: Release mine, Ask to release, and Break only after its confirm", async () => {
    drawer(status({
      locks: [
        { unit: "tree:scenes/ab12", name: "MEGA_Duel_Stadium", holder: "Ana", leaseLeft: 240 },
        { unit: "doc:pack", name: "pack", mine: true, leaseLeft: 0 },
      ],
    }));
    go(/^Locks/);
    expect(rows()).toEqual([
      ["MEGA_Duel_Stadium", "Locked by Ana · 4 min left"],
      ["pack", "Locked by you · ran out"],
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Release pack" }));
    await waitFor(() => expect(api.plugin_scope_unlock).toHaveBeenCalledWith("forge", "doc:pack"));
    // Each action holds every button until it's done; a click before that is (rightly) ignored.
    const ask = screen.getByRole("button", { name: "Ask Ana to release MEGA_Duel_Stadium" }) as HTMLButtonElement;
    await waitFor(() => expect(ask.disabled).toBe(false));

    fireEvent.click(ask);
    await waitFor(() => expect(api.plugin_scope_ask).toHaveBeenCalledWith("forge", "tree:scenes/ab12"));
    expect(await screen.findByText("Asked Ana to release MEGA_Duel_Stadium.")).toBeTruthy();
    const breakBtn = screen.getByRole("button", { name: "Break Ana's lock on MEGA_Duel_Stadium" }) as HTMLButtonElement;
    await waitFor(() => expect(breakBtn.disabled).toBe(false));

    fireEvent.click(screen.getByRole("button", { name: "Break Ana's lock on MEGA_Duel_Stadium" }));
    expect(await screen.findByText("Break Ana's lock on MEGA_Duel_Stadium?")).toBeTruthy();
    expect(screen.getByText(
      "Ana is told, and what Ana changed in MEGA_Duel_Stadium and hasn't sent yet becomes a conflict on their PC. Nothing of theirs is lost.",
    )).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Cancel/ }));
    await waitFor(() => expect(screen.queryByText("Break Ana's lock on MEGA_Duel_Stadium?")).toBeNull());
    expect(api.plugin_scope_break).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Break Ana's lock on MEGA_Duel_Stadium" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Break lock \(/ }));
    await waitFor(() => expect(api.plugin_scope_break).toHaveBeenCalledWith("forge", "tree:scenes/ab12"));
    expect(onChanged).toHaveBeenCalled();
  });

  it("a refused lock action says why in the panel", async () => {
    api.plugin_scope_break.mockResolvedValue({ ok: false, error: "Only owners and admins can break a live lock." });
    drawer(status({ locks: [{ unit: "doc:deck", name: "deck", holder: "Ana" }] }));
    go(/^Locks/);
    fireEvent.click(screen.getByRole("button", { name: "Break Ana's lock on deck" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Break lock \(/ }));
    expect((await screen.findByRole("alert")).textContent).toBe("Only owners and admins can break a live lock.");
  });

  it("changes: each one's state, Retry sends again, Retry all, Drop asks first; Sync now forces a round", async () => {
    drawer(status({
      changes: {
        outgoing: [
          { kind: "asset", key: "art/big.png", state: "refused", reason: "too big for your plan", size: 3 * 1024 ** 2 },
          { kind: "doc", key: "scene_1", state: "blocked", attempts: 5, reason: "file in use" },
          { kind: "doc", key: "deck", state: "locked_out", reason: "Locked by Ana" },
          { kind: "doc", key: "pack", state: "queued" },
        ],
      },
    }));
    expect(rows()).toEqual([
      ["art/big.png", "3 MB · Refused: too big for your plan"],
      ["scene_1", "Couldn't be sent after 5 tries: file in use"],
      ["deck", "Locked by Ana"],
      ["pack", "Queued"],
    ]);
    // Locked out and queued changes have no Retry or Drop.
    expect(screen.queryByRole("button", { name: "Retry deck" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Drop my change to pack" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry scene_1" }));
    await waitFor(() => expect(api.plugin_scope_retry).toHaveBeenCalledWith("forge", "scene_1"));
    await waitFor(() => expect(api.plugin_scope_sync).toHaveBeenCalledWith("forge", true));

    fireEvent.click(screen.getByRole("button", { name: "Retry all" }));
    await waitFor(() => expect(api.plugin_scope_retry).toHaveBeenLastCalledWith("forge"));

    fireEvent.click(screen.getByRole("button", { name: "Drop my change to art/big.png" }));
    expect(await screen.findByText("Drop your change to art/big.png?")).toBeTruthy();
    expect(api.plugin_scope_drop).not.toHaveBeenCalled();
    fireEvent.click(confirmButton("Drop my change"));
    await waitFor(() => expect(api.plugin_scope_drop).toHaveBeenCalledWith("forge", "asset", "art/big.png"));
    expect(onChanged).toHaveBeenLastCalledWith(true);

    api.plugin_scope_sync.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(api.plugin_scope_sync).toHaveBeenCalledWith("forge", true));
  });

  it("a round that failed: the reason is in Changes, once, with Retry", async () => {
    drawer(status({ state: "offline", error: "Network error: <urlopen error [WinError 10060] ...>" }));
    expect(rows()[0]).toEqual(["Offline", "Network error: <urlopen error [WinError 10060] ...>"]);
    expect(screen.getAllByText(/WinError 10060/)).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(api.plugin_scope_sync).toHaveBeenCalledWith("forge", true));
  });

  it("changes a provider held back (no engine list) show with their reasons", () => {
    drawer(status({
      head: undefined, mode: undefined, people: undefined, locks: undefined, refused: 3,
      items: [{ kind: "doc", key: "a", state: "refused", reason: "storage full" }],
    }));
    expect(rows()).toEqual([["a", "Refused: storage full"]]);
    expect(screen.getByText(/2 more not listed/)).toBeTruthy();
    // No engine: no People, Locks or History, and no Sending choice.
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Changes1", "Incoming", "Conflicts", "Settings"]);
    go("Settings");
    expect(screen.queryByRole("radiogroup", { name: "Sending" })).toBeNull();
  });

  it("incoming: who it's from, held ones wait for Update now, blocked ones Download again", async () => {
    const view = drawer(status({
      changes: { incoming: 2, incomingBy: ["Ana", "Ben"], held: true,
        blocked: [{ kind: "asset", key: "art/sky.png", error: "a chunk failed its check", attempts: 2 }] },
    }));
    go(/^Incoming/);
    expect(rows()).toEqual([
      ["2 changes from Ana and Ben", "They wait until you update: Forge restarts to take them."],
      ["art/sky.png", "a chunk failed its check (2 tries)"],
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Update now" }));
    await waitFor(() => expect(api.plugin_scope_pull).toHaveBeenCalledWith("forge"));
    expect(onChanged).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "Download art/sky.png again" }));
    await waitFor(() => expect(api.plugin_scope_pull).toHaveBeenCalledWith("forge", "asset", "art/sky.png"));
    view.unmount();
    // Changes that apply by themselves: Get now brings them sooner.
    drawer(status({ changes: { incoming: 1, incomingBy: ["Ana"], held: false } }));
    go(/^Incoming/);
    expect(rows()).toEqual([["1 change from Ana", "Coming in now."]]);
    expect(screen.queryByRole("button", { name: "Update now" })).toBeNull();
    expect(screen.getByRole("button", { name: "Get now" })).toBeTruthy();
  });

  it("conflicts: Keep mine, Keep theirs after a confirm, Keep both for folders, Compare", async () => {
    api.plugin_scope_resolve.mockImplementation(async (_p: string, _k: string, _key: string, choice: string) =>
      choice === "compare" ? { ok: true, diff: "- hp: 10\n+ hp: 12" } : { ok: true });
    drawer(status({
      conflicts: [
        { kind: "doc", key: "deck", by: "Ana", reason: "edit" },
        { kind: "doc", key: "pack", by: "Ben", reason: "deleted" },
        { kind: "tree", key: "tree:scenes/ab12", name: "MEGA_Duel_Stadium", by: "Ana" },
      ],
    }));
    go(/^Conflicts/);
    expect(screen.getAllByText(/^Changed here and by Ana/).length).toBe(2);
    expect(screen.getByText(/^Ben deleted this; you changed it/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Keep both: deck" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Keep mine: deck" }));
    await waitFor(() => expect(api.plugin_scope_resolve).toHaveBeenCalledWith("forge", "doc", "deck", "mine"));
    expect(onChanged).toHaveBeenLastCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "Accept delete: pack" }));
    expect(await screen.findByText("Keep Ben's version of pack?")).toBeTruthy();
    fireEvent.click(confirmButton("Keep theirs"));
    await waitFor(() => expect(api.plugin_scope_resolve).toHaveBeenCalledWith("forge", "doc", "pack", "theirs"));
    expect(screen.getByRole("button", { name: "Restore mine: pack" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Keep both: MEGA_Duel_Stadium" }));
    await waitFor(() => expect(api.plugin_scope_resolve).toHaveBeenCalledWith("forge", "tree", "tree:scenes/ab12", "both"));

    fireEvent.click(screen.getByRole("button", { name: "Compare: deck" }));
    await waitFor(() => expect(api.plugin_scope_resolve).toHaveBeenCalledWith("forge", "doc", "deck", "compare"));
    expect((await screen.findByText(/- hp: 10/)).textContent).toBe("- hp: 10\n+ hp: 12");
    expect(screen.getByRole("button", { name: "Compare: deck" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("history loads by itself: Undo, Restore to here and an item's Restore, each confirmed in a modal", async () => {
    api.plugin_scope_history.mockImplementation(async (_p: string, req: { key?: string; before?: number }) => req?.key
      ? { ok: true, entries: [{ seq: 9, by: "Ana", at: 1_800_000_000, kind: "doc", key: "deck", size: 2048 }] }
      : req?.before ? { ok: true, more: false, entries: [{ seq: 10, by: "Cy", count: 1 }] }
      : { ok: true, more: true, entries: [
        { seq: 12, by: "Ben", actor: "agent", ducky: "Builder", device: "BEN-PC", message: "Balance pass", count: 2,
          items: [["doc", "deck"], ["doc", "pack"]] },
        { seq: 11, by: "Ana", count: 1 },
      ] });
    api.plugin_scope_restore.mockResolvedValue({ ok: true, seq: 13, skipped: [["doc", "pack"]] });
    drawer(status());
    go("History");
    await waitFor(() => expect(api.plugin_scope_history).toHaveBeenCalledWith("forge", { before: undefined, limit: 20 }));
    expect(await screen.findByText("Balance pass")).toBeTruthy();
    expect(rows()).toEqual([
      ["Balance pass", "AI (Ben's ducky Builder) · BEN-PC"],
      ["1 item changed", "Ana"],
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Undo #12" }));
    expect(await screen.findByText("Undo change #12?")).toBeTruthy();
    expect(screen.getByText(/It is a new change everyone in Alpha gets; the history stays as it is\.$/)).toBeTruthy();
    expect(api.plugin_scope_restore).not.toHaveBeenCalled();
    fireEvent.click(confirmButton("Undo"));
    await waitFor(() => expect(api.plugin_scope_restore).toHaveBeenCalledWith("forge", { undoSeq: 12 }));
    expect(await screen.findByText("Restored. 1 item wasn't: its version is no longer kept.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Restore to #11" }));
    expect(await screen.findByText("Restore Forge to #11?")).toBeTruthy();
    fireEvent.click(confirmButton("Restore"));
    await waitFor(() => expect(api.plugin_scope_restore).toHaveBeenLastCalledWith("forge", { toSeq: 11 }));

    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    await waitFor(() => expect(api.plugin_scope_history).toHaveBeenCalledWith("forge", { before: 11, limit: 20 }));

    fireEvent.click(screen.getByRole("button", { name: "2 items" }));
    fireEvent.click(screen.getByRole("button", { name: "Versions of deck" }));
    await waitFor(() => expect(api.plugin_scope_history).toHaveBeenCalledWith("forge", { kind: "doc", key: "deck", limit: 20 }));
    expect(await screen.findByText("Versions of deck")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restore deck to #9" }));
    expect(await screen.findByText("Restore deck to #9?")).toBeTruthy();
    fireEvent.click(confirmButton("Restore"));
    await waitFor(() => expect(api.plugin_scope_restore).toHaveBeenLastCalledWith("forge", { items: [{ kind: "doc", key: "deck", seq: 9 }] }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(await screen.findByRole("button", { name: "Undo #12" })).toBeTruthy();
  });

  it("history: the move to the new sync (#1) can be restored to but not undone", async () => {
    api.plugin_scope_history.mockResolvedValue({ ok: true, more: false, entries: [
      { seq: 2, by: "Ana", count: 1 },
      { seq: 1, by: "Imported from team data", count: 40 },
    ] });
    drawer(status());
    go("History");
    expect(await screen.findByRole("button", { name: "Undo #2" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Undo #1" })).toBeNull();
    expect(screen.getByRole("button", { name: "Restore to #1" })).toBeTruthy();
  });

  it("folder units show only when the plugin declares folders; Lock and Release", async () => {
    const view = drawer(status());
    expect(screen.queryByText("Folders")).toBeNull();
    view.unmount();
    drawer(status({
      folders: [
        { unit: "tree:scenes/1", name: "MEGA_Duel_Stadium", state: "changed" },
        { unit: "tree:scenes/2", name: "Arena", state: "locked", holder: "Ana" },
        { unit: "tree:scenes/3", name: "Lobby", state: "locked", mine: true },
        { unit: "tree:scenes/4", name: "Vault", state: "ok" },
      ],
    }));
    expect(screen.getByText("Folders")).toBeTruthy();
    expect(rows().slice(-4)).toEqual([
      ["MEGA_Duel_Stadium", "Changed here"],
      ["Arena", "Locked by Ana"],
      ["Lobby", "Locked by you"],
      ["Vault", "Up to date"],
    ]);
    expect(screen.queryByRole("button", { name: "Lock Arena" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Lock Vault" }));
    await waitFor(() => expect(api.plugin_scope_lock).toHaveBeenCalledWith("forge", "tree:scenes/4", "Vault"));
    fireEvent.click(screen.getByRole("button", { name: "Release Lobby" }));
    await waitFor(() => expect(api.plugin_scope_unlock).toHaveBeenCalledWith("forge", "tree:scenes/3"));
  });

  it("folder units: size and each state; the plaintext note is in Settings", () => {
    drawer(status({
      teamFolders: [{ id: "scenes" }], folderNote: NOTE,
      folders: [
        { folder_id: "scenes", unit: "MEGA_Duel_Stadium", name: "MEGA_Duel_Stadium", state: "up_to_date", files: 12, bytes: 226 * 1024 ** 2 },
        { folder_id: "scenes", unit: "Arena", name: "Arena", state: "conflict", files: 3, bytes: 2048 },
        { folder_id: "scenes", unit: "Lobby", name: "Lobby", state: "refused", files: 2, bytes: 10,
          reason: "Lobby.json and lobby.json differ only by upper/lower case or accents. Rename one so the team can tell them apart." },
      ],
    }));
    expect(rows().slice(-3)).toEqual([
      ["MEGA_Duel_Stadium", "12 files · 226 MB · Up to date"],
      ["Arena", "3 files · 2 KB · Changed here and by someone else"],
      ["Lobby", "2 files · 10 B · Lobby.json and lobby.json differ only by upper/lower case or accents. Rename one so the team can tell them apart."],
    ]);
    // A refused unit can't be locked; the others can.
    expect(screen.queryByRole("button", { name: "Lock Lobby" })).toBeNull();
    expect(screen.getByRole("button", { name: "Lock Arena" })).toBeTruthy();
    go("Settings");
    expect(screen.getByText(new RegExp(NOTE.replace(/[.;]/g, "\\$&")))).toBeTruthy();
  });

  it("says why a declared folder can't be shared from this PC", () => {
    drawer(status({ teamFolders: [{ id: "design", refused: URC }, { id: "noproject" }] }));
    expect(rows()).toContainEqual(["This folder can't be shared", URC]);
    cleanup();
    drawer(status({ teamFolders: [
      { id: "design", refused: URC },
      { id: "noproject", refused: "This folder is synced by OneDrive. Move it out of OneDrive to share it with a team." },
    ] }));
    expect(rows()).toContainEqual(["design", URC]);
    expect(rows()).toContainEqual(["noproject", "This folder is synced by OneDrive. Move it out of OneDrive to share it with a team."]);
  });

  it("a unit changed while someone else holds it: Ask to release, Keep mine as a copy, Discard mine after a confirm", async () => {
    drawer(status({
      folders: [{ folder_id: "scenes", unit: "MEGA_Duel_Stadium", name: "MEGA_Duel_Stadium", state: "quarantined",
        reason: "Changed here while Ana holds it" }],
    }));
    expect(rows()).toContainEqual(["MEGA_Duel_Stadium", "Changed here while Ana holds it"]);
    expect(screen.queryByRole("button", { name: "Lock MEGA_Duel_Stadium" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Ask the holder to release MEGA_Duel_Stadium" }));
    await waitFor(() => expect(api.plugin_scope_ask).toHaveBeenCalledWith("forge", "scenes/MEGA_Duel_Stadium"));

    fireEvent.click(screen.getByRole("button", { name: "Keep mine as a copy: MEGA_Duel_Stadium" }));
    await waitFor(() => expect(api.plugin_scope_resolve).toHaveBeenCalledWith(
      "forge", "tree", "scenes/MEGA_Duel_Stadium", "keep_copy"));
    expect(await screen.findByText(
      "Your version of MEGA_Duel_Stadium is kept beside it as a copy; MEGA_Duel_Stadium has Alpha's version again.")).toBeTruthy();
    expect(onChanged).toHaveBeenLastCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "Discard mine: MEGA_Duel_Stadium" }));
    expect(await screen.findByText("Discard your changes to MEGA_Duel_Stadium?")).toBeTruthy();
    expect(screen.getByText("MEGA_Duel_Stadium goes back to Alpha's version. What you changed here is not kept.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Cancel/ }));
    await waitFor(() => expect(screen.queryByText("Discard your changes to MEGA_Duel_Stadium?")).toBeNull());
    expect(api.plugin_scope_resolve).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Discard mine: MEGA_Duel_Stadium" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Discard mine \(/ }));
    await waitFor(() => expect(api.plugin_scope_resolve).toHaveBeenLastCalledWith(
      "forge", "tree", "scenes/MEGA_Duel_Stadium", "discard"));
  });

  it("a {PROJECT} folder's unit goes by its folder and project id; a refusal from the host stays in the panel", async () => {
    api.plugin_scope_resolve.mockResolvedValueOnce({ ok: false, error: "MEGA_Duel_Stadium is open in another program." });
    drawer(status({
      folders: [{ folder_id: "design", unit: "1f0c-guid", name: "design", state: "quarantined", holder: "Ben" }],
    }));
    expect(rows()).toContainEqual(["design", "Changed here while Ben holds it"]);
    fireEvent.click(screen.getByRole("button", { name: "Keep mine as a copy: design" }));
    await waitFor(() => expect(api.plugin_scope_resolve).toHaveBeenCalledWith("forge", "tree", "design/1f0c-guid", "keep_copy"));
    expect((await screen.findByRole("alert")).textContent).toBe("MEGA_Duel_Stadium is open in another program.");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("without the host's call, a held unit offers no Keep or Discard", () => {
    const saved = api.plugin_scope_resolve;
    Object.assign(api, { plugin_scope_resolve: undefined });
    try {
      drawer(status({ folders: [{ folder_id: "scenes", unit: "Arena", name: "Arena", state: "quarantined" }] }));
      expect(rows()).toContainEqual(["Arena", "Changed here while someone else holds it"]);
      expect(screen.queryByRole("button", { name: "Keep mine as a copy: Arena" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Discard mine: Arena" })).toBeNull();
    } finally {
      Object.assign(api, { plugin_scope_resolve: saved });
    }
  });

  it("the host's unit states; Lock and Release go by the unit's lock id, else <folder>/<unit>", async () => {
    drawer(status({
      locks: [{ unit: "tree:scenes/9f2c", name: "Lobby", mine: true }],
      folders: [
        { folder_id: "scenes", unit: "Arena", unitId: "tree:scenes/ab12", name: "Arena", state: "up_to_date" },
        { folder_id: "scenes", unit: "Vault", name: "Vault", state: "changed_here" },
        { folder_id: "scenes", unit: "Lobby", unitId: "tree:scenes/9f2c", name: "Lobby", state: "up_to_date", mine: true },
        { folder_id: "scenes", unit: "Dock", unitId: "tree:scenes/77d0", name: "Dock", state: "locked_by", holder: "Ana" },
        { folder_id: "scenes", unit: "Pier", unitId: "tree:scenes/5e1a", name: "Pier", state: "incoming" },
      ],
    }));
    expect(rows().slice(-5).map(([, sub]) => sub)).toEqual(["Up to date", "Changed here", "Up to date", "Locked by Ana", "Update waiting"]);
    expect(screen.queryByRole("button", { name: "Lock Dock" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Lock Arena" }));
    await waitFor(() => expect(api.plugin_scope_lock).toHaveBeenCalledWith("forge", "tree:scenes/ab12", "Arena"));
    fireEvent.click(screen.getByRole("button", { name: "Lock Vault" }));
    await waitFor(() => expect(api.plugin_scope_lock).toHaveBeenLastCalledWith("forge", "scenes/Vault", "Vault"));
    // This PC holds Lobby: Release, never Lock.
    expect(screen.queryByRole("button", { name: "Lock Lobby" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Release Lobby" }));
    await waitFor(() => expect(api.plugin_scope_unlock).toHaveBeenCalledWith("forge", "tree:scenes/9f2c"));
  });

  it("a folder unit's outgoing change shows its file count and why it is held, without Retry or Drop", () => {
    drawer(status({
      changes: { outgoing: [
        { kind: "tree", key: "scenes/Arena", folder_id: "scenes", unit: "Arena", name: "Arena", files: 3, state: "quarantined",
          reason: "Changed here while Ana holds it" },
        { kind: "tree", key: "scenes/Vault", folder_id: "scenes", unit: "Vault", name: "Vault", files: 1, state: "queued" },
        { kind: "tree", key: "scenes/Lobby", folder_id: "scenes", unit: "Lobby", name: "Lobby", files: 2, state: "refused",
          reason: "Lobby.json and lobby.json differ only by upper/lower case or accents. Rename one." },
      ] },
    }));
    expect(rows()).toEqual([
      ["Arena", "3 files · Changed here while Ana holds it"],
      ["Vault", "1 file · Queued"],
      ["Lobby", "2 files · Refused: Lobby.json and lobby.json differ only by upper/lower case or accents. Rename one."],
    ]);
    // Renaming fixes a name clash: Retry and Drop my change are for data items.
    expect(screen.queryByRole("button", { name: "Retry Lobby" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Drop my change to Lobby" })).toBeNull();
  });

  it("a folder unit the server kept failing has Retry (and Retry all), never Drop my change", async () => {
    drawer(status({
      changes: { outgoing: [
        { kind: "tree", key: "scenes/Arena", folder_id: "scenes", unit: "Arena", name: "Arena", files: 2, state: "blocked",
          reason: "bad request" },
        { kind: "tree", key: "scenes/Vault", folder_id: "scenes", unit: "Vault", name: "Vault", files: 1, state: "blocked",
          reason: "bad request" },
      ] },
    }));
    expect(rows().map(([, sub]) => sub)).toEqual(["2 files · Couldn't be sent: bad request", "1 file · Couldn't be sent: bad request"]);
    expect(screen.queryByRole("button", { name: "Drop my change to Arena" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry Arena" }));
    await waitFor(() => expect(api.plugin_scope_retry).toHaveBeenCalledWith("forge", "scenes/Arena"));
    fireEvent.click(screen.getByRole("button", { name: "Retry all" }));
    await waitFor(() => expect(api.plugin_scope_retry).toHaveBeenLastCalledWith("forge"));
  });

  it("Sending: Auto or Manual in Settings; Manual sends with an optional message, and gets in Incoming", async () => {
    const view = drawer(status());
    go("Settings");
    expect(screen.getByRole("radio", { name: "Auto" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("radio", { name: "Manual" }));
    await waitFor(() => expect(api.plugin_scope_mode).toHaveBeenCalledWith("forge", "manual"));
    view.unmount();

    drawer(status({ mode: "manual" }));
    expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Message (optional)" }), { target: { value: "  New arena  " } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.plugin_scope_push).toHaveBeenCalledWith("forge", "New arena"));
    await waitFor(() => expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe(""));
    go(/^Incoming/);
    fireEvent.click(screen.getByRole("button", { name: "Get now" }));
    await waitFor(() => expect(api.plugin_scope_pull).toHaveBeenCalledWith("forge"));
    go("Settings");
    fireEvent.click(screen.getByRole("radio", { name: "Auto" }));
    await waitFor(() => expect(api.plugin_scope_mode).toHaveBeenLastCalledWith("forge", "auto"));
  });

  it("Settings: the copy, storage and who's signed in", () => {
    drawer(status({ email: "ana@x.org", members: 4, usage: { usedBytes: 34 * 1024 ** 2, limitBytes: 5 * 1024 ** 3 } }));
    go("Settings");
    expect(screen.getByText("34 MB of 5 GB")).toBeTruthy();
    expect(rows()).toContainEqual(["Account", "Signed in as ana@x.org · 4 members"]);
    expect(rows()[0][1]).toMatch(/^Alpha's team copy\./);
  });

  it("a lost space offers to save the unsent changes to Local until the person has chosen", async () => {
    api.plugin_scope_save_unsent.mockResolvedValue({ ok: true, saved: [
      { plugin: "forge", kind: "doc", key: "deck", localKey: "deck-from-alpha" },
      { plugin: "forge", kind: "doc", key: "pack", localKey: "pack" },
    ] });
    const view = drawer(status({ state: "lost", unsent: 2 }));
    expect(screen.getByText(/^2 changes made here never reached Alpha\./)).toBeTruthy();
    expect(screen.queryByText(/^Your changes on this PC/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save to Local" }));
    await waitFor(() => expect(api.plugin_scope_save_unsent).toHaveBeenCalledWith("forge", "save"));
    expect(await screen.findByText("Saved 2 changes to Local.")).toBeTruthy();

    // Or let them go, after a confirm.
    fireEvent.click(screen.getByRole("button", { name: "Let them go" }));
    expect(await screen.findByText("They are never sent, and they leave this PC with Alpha's copy.")).toBeTruthy();
    fireEvent.click(confirmButton("Let them go"));
    await waitFor(() => expect(api.plugin_scope_save_unsent).toHaveBeenLastCalledWith("forge", "discard"));
    view.unmount();

    drawer(status({ state: "lost", unsent: 2, unsentChosen: true }));
    expect(screen.queryByRole("button", { name: "Save to Local" })).toBeNull();
  });
});

