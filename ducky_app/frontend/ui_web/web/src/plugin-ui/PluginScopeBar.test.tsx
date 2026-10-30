// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { _resetBackgroundActivityForTests, getBackgroundJobs } from "../hooks/backgroundActivity";
import { writeLiveFollow } from "./liveFollow";
import { PluginScopeBar } from "./PluginScopeBar";
import { getAllTeamScopes, getTeamScope } from "./teamScopeStore";
import { TeamModal } from "./TeamModal";

const api = vi.hoisted(() => ({
  plugin_scope_status: vi.fn(),
  plugin_scope_choices: vi.fn(),
  plugin_scope_set: vi.fn(),
  plugin_scope_sync: vi.fn(),
  plugin_scope_retry: vi.fn() as ReturnType<typeof vi.fn> | undefined,
  plugin_scope_drop: vi.fn() as ReturnType<typeof vi.fn> | undefined,
  space_reset_settle: vi.fn() as ReturnType<typeof vi.fn> | undefined,
  plugin_scope_push: vi.fn() as ReturnType<typeof vi.fn> | undefined,
  plugin_scope_pull: vi.fn() as ReturnType<typeof vi.fn> | undefined,
  plugin_scope_ask: vi.fn(),
  plugin_live_join: vi.fn(),
  plugin_live_leave: vi.fn(),
  notify_activity: vi.fn(),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));
const push = vi.hoisted(() => ({ handlers: new Set<(event: Record<string, unknown>) => void>() }));
vi.mock("../hooks/usePanelPushBus", () => ({
  installPanelPushBus: () => {},
  subscribePanelPush: (fn: (event: Record<string, unknown>) => void) => {
    push.handlers.add(fn);
    return () => push.handlers.delete(fn);
  },
}));
const saved = { retry: api.plugin_scope_retry, drop: api.plugin_scope_drop, settle: api.space_reset_settle,
  push: api.plugin_scope_push, pull: api.plugin_scope_pull };
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  Object.assign(api, { plugin_scope_retry: saved.retry, plugin_scope_drop: saved.drop, space_reset_settle: saved.settle,
    plugin_scope_push: saved.push, plugin_scope_pull: saved.pull });
  localStorage.clear();
  _resetBackgroundActivityForTests();
});

/** The pane's headless bar with the header's Team modal open over it. */
const bar = (pluginId = "brainrot-tcg", onScopeChanged: () => void = () => {}) =>
  render(
    <ConfirmModalProvider>
      <PluginScopeBar pluginId={pluginId} onScopeChanged={onScopeChanged} />
      <TeamModal open onClose={() => {}} />
    </ConfirmModalProvider>,
  );
/** The bar published its copy (the Team modal is on screen). */
const shown = () => waitFor(() => expect(getTeamScope()).not.toBeNull());
/** A section in the Team modal's left nav. */
const nav = (name: RegExp) => fireEvent.click(screen.getByRole("button", { name }));
/** The Overview's "Whose data" button. */
const picker = () => screen.getByRole("button", { name: /^Whose data/ }) as HTMLButtonElement;
/** Open the "Whose data" dropdown once its choices have loaded. */
const openPicker = async () => {
  const trigger = await waitFor(() => {
    const t = screen.getByRole("button", { name: /^Whose data: (?!Loading)/ }) as HTMLButtonElement;
    expect(t.disabled).toBe(false);
    return t;
  });
  if (trigger.getAttribute("aria-expanded") !== "true") fireEvent.click(trigger);
};
/** A row in the "Whose data" dropdown. */
const option = async (name: RegExp) => {
  await openPicker();
  return screen.findByRole("option", { name });
};

/** A shared copy's status, with ``extra`` on top (``scope`` fields merge too). */
const sharedStatus = (extra: Record<string, unknown> = {}, scope: Record<string, unknown> = {}) => ({
  ok: true, visible: true, pluginLabel: "BrainRot TCG", canChange: true, state: "ok", pending: 0,
  syncedAt: Date.now() / 1000 - 5, ...extra,
  scope: { kind: "shared", label: "Alpha Studio", spaceId: "s1", readOnly: false, ...scope },
});

/** The first share's picker: the dialog over the Team modal. */
const shareDialog = async () => {
  await waitFor(() => expect(screen.getAllByRole("dialog")).toHaveLength(2));
  return screen.getAllByRole("dialog")[1];
};
const noShareDialog = () => expect(screen.getAllByRole("dialog")).toHaveLength(1);
const panel = () => screen.getByRole("region", { name: "Source control: Alpha Studio" });

describe("plugin scope bar", () => {
  it("renders nothing while the account has no shared spaces", async () => {
    api.plugin_scope_status.mockResolvedValue({ ok: true, visible: false, scope: { kind: "personal", label: "Local", spaceId: "", readOnly: false } });
    const { container } = bar();
    await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalled());
    expect(container.textContent).toBe("");
    expect(api.plugin_scope_sync).not.toHaveBeenCalled();
  });

  it("renders nothing for a plugin that can't share, even from an app that still sends it visible", async () => {
    api.plugin_scope_status.mockResolvedValue({
      ok: true, visible: true, shareable: false, canChange: true, state: "ok",
      scope: { kind: "personal", label: "Local", spaceId: "", readOnly: false },
    });
    const { container } = bar();
    await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalled());
    expect(container.textContent).toBe("");
    expect(api.plugin_scope_sync).not.toHaveBeenCalled();
  });

  it("draws no strip above the panel: one status in the Team modal, and the count for the header", async () => {
    api.plugin_scope_status.mockResolvedValue(sharedStatus({
      head: 482, syncedAt: Date.now() / 1000 - 120, pending: 2, changes: { incoming: 1 },
      locks: [{ unit: "doc:deck", name: "deck", holder: "Ana", mine: false }, { unit: "doc:pack", name: "pack", mine: true }],
    }));
    const { container } = bar();
    const status = await screen.findByRole("button", { name: "Locked by Ana: deck (read-only for you)" });
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(container.querySelector(".plugin-scope-bar")).toBeNull();
    expect(container.textContent).toBe("");
    // 2 to send + 1 coming in.
    expect(getTeamScope()?.badge).toBe(3);
    expect(screen.queryByRole("region")).toBeNull();
    // The status opens the section that explains it.
    fireEvent.click(status);
    expect(screen.getByRole("button", { name: /^Locks/ }).getAttribute("aria-current")).toBe("page");
    expect(panel()).toBeTruthy();
  });

  it("Settings in the Team modal shows the account and the team's storage", async () => {
    api.plugin_scope_status.mockResolvedValue(sharedStatus({
      email: "ana@x.org", members: 4, usage: { usedBytes: 34 * 1024 ** 2, limitBytes: 5 * 1024 ** 3 },
    }));
    bar();
    await shown();
    nav(/^Settings/);
    expect(within(panel()).getByText("Signed in as ana@x.org · 4 members")).toBeTruthy();
    expect(within(panel()).getByText("34 MB of 5 GB")).toBeTruthy();
  });

  it("confirms before switching, counting the changes still to send as of the click", async () => {
    api.plugin_scope_status.mockResolvedValue(sharedStatus({ pending: 1 }));
    api.plugin_scope_choices.mockResolvedValue({ ok: true, choices: [
      { id: "personal", kind: "personal", label: "Local" },
      { id: "s1", kind: "shared", label: "Alpha Studio" },
    ] });
    api.plugin_scope_set.mockResolvedValue({ ok: true });
    bar();
    expect(await screen.findByText("1 to send")).toBeTruthy();
    // More changes landed since the bar last refreshed: the confirm reads them again.
    api.plugin_scope_status.mockResolvedValue(sharedStatus({
      pending: 3, blocked: 1, items: [{ kind: "doc", key: "scene_1", state: "blocked", attempts: 5 }],
    }));
    fireEvent.click(await option(/^Local/));
    expect(
      await screen.findByText(
        "3 changes not sent to Alpha Studio will be sent first. 1 change not shared stays in Alpha Studio's copy on this PC. " +
          "Nothing is copied. BrainRot TCG restarts with its Local data.",
      ),
    ).toBeTruthy();
    expect(api.plugin_scope_set).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^Switch/ }));
    await waitFor(() => expect(api.plugin_scope_set).toHaveBeenCalledWith("brainrot-tcg", "personal"));
  });

  it("asks for one sync on open and one on focus, never on a timer", async () => {
    const every = vi.spyOn(window, "setInterval");
    api.plugin_scope_status.mockResolvedValue(sharedStatus());
    bar();
    await waitFor(() => expect(api.plugin_scope_sync).toHaveBeenCalledTimes(1));
    // A slow timer only moves "12 s ago" on: running it asks for nothing.
    await waitFor(() => expect(every.mock.calls.some(([, ms]) => Number(ms) >= 10_000)).toBe(true));
    for (const [fn, ms] of every.mock.calls) if (Number(ms) >= 10_000) (fn as () => void)();
    expect(api.plugin_scope_sync).toHaveBeenCalledTimes(1);
    expect(api.plugin_scope_status).toHaveBeenCalledTimes(1);
    fireEvent.focus(window);
    expect(api.plugin_scope_sync).toHaveBeenCalledTimes(2);
    expect(api.plugin_scope_sync).toHaveBeenLastCalledWith("brainrot-tcg", false);
  });

  it("a failed round says so in plain words; Retry forces a round and waits; the reason is in Changes", async () => {
    const why = "Network error: <urlopen error [WinError 10060] A connection attempt failed>";
    api.plugin_scope_status.mockResolvedValue(sharedStatus({ state: "offline", pending: 9, error: why, nextTry: Date.now() / 1000 + 40 }));
    api.plugin_scope_sync.mockResolvedValue({ ok: true, started: true });
    bar();
    expect(await screen.findByText(/^Offline · 9 to send · retry in (39|40) s$/)).toBeTruthy();
    // The raw reason never sits on the bar.
    expect(screen.queryByText(/WinError/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(api.plugin_scope_sync).toHaveBeenCalledWith("brainrot-tcg", true));
    expect(await screen.findByText("Syncing…")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Retry" }) as HTMLButtonElement).disabled).toBe(true);
    push.handlers.forEach((fn) => fn({ type: "plugin_scope_changed", plugins: [], synced: true }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Retry" }) as HTMLButtonElement).disabled).toBe(false));
    // The status opens Changes, which says why, once.
    fireEvent.click(screen.getByRole("button", { name: /^Offline/ }));
    expect(within(panel()).getAllByText(why)).toHaveLength(1);
  });

  it("without a provider, says it isn't connected and keeps the queue count", async () => {
    const why = "Not connected: Ducky Account isn't running";
    api.plugin_scope_status.mockResolvedValue(sharedStatus({ state: "no_provider", pending: 2, error: why }));
    bar();
    expect(await screen.findByText(`${why} · 2 to send`)).toBeTruthy();
    expect(screen.getAllByText(new RegExp(why)).length).toBe(1);
  });

  it("an unavailable copy is read-only with its reason, said once", async () => {
    const why = "permission denied: uefn-ducky-store.teams";
    api.plugin_scope_status.mockResolvedValue(sharedStatus({ state: "unavailable", error: why }, { readOnly: true }));
    bar();
    expect(
      await screen.findByText(`Alpha Studio isn't available right now (${why}). Read-only until it's back.`),
    ).toBeTruthy();
    expect(screen.getAllByText(new RegExp(why)).length).toBe(1);
  });

  it("in Manual mode the bar has Send and Get", async () => {
    api.plugin_scope_status.mockResolvedValue(sharedStatus({ mode: "manual", head: 3, pending: 2 }));
    api.plugin_scope_push?.mockResolvedValue({ ok: true, started: true });
    api.plugin_scope_pull?.mockResolvedValue({ ok: true, started: true });
    bar();
    fireEvent.click(await screen.findByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.plugin_scope_push).toHaveBeenCalledWith("brainrot-tcg"));
    fireEvent.click(screen.getByRole("button", { name: "Get" }));
    await waitFor(() => expect(api.plugin_scope_pull).toHaveBeenCalledWith("brainrot-tcg"));
  });

  it("updates waiting for a restart say who from, and Update now brings them in", async () => {
    api.plugin_scope_status.mockResolvedValue(sharedStatus({ head: 4, changes: { incoming: 2, incomingBy: ["Ana"], held: true } }));
    api.plugin_scope_pull?.mockResolvedValue({ ok: true });
    bar();
    expect(await screen.findByText("2 updates from Ana")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Update now" }));
    await waitFor(() => expect(api.plugin_scope_pull).toHaveBeenCalledWith("brainrot-tcg"));
  });

  it("a refused write is told through the alerts system and offers Ask to release", async () => {
    api.plugin_scope_status.mockResolvedValue(sharedStatus({ pluginLabel: "Forge" }));
    api.plugin_scope_ask.mockResolvedValue({ ok: true });
    api.notify_activity.mockResolvedValue(true);
    bar("forge");
    await shown();
    push.handlers.forEach((fn) => fn({
      type: "plugin_write_refused", plugin_id: "forge", unit: "tree:scenes/ab12", unit_name: "MEGA_Duel_Stadium", holder: "Ana",
      since: new Date(2026, 9, 17, 15, 2).getTime() / 1000,
    }));
    const text = await screen.findByText(/^Forge: MEGA_Duel_Stadium is locked by Ana since .+\. Your change wasn't saved\.$/);
    expect(text).toBeTruthy();
    expect(api.notify_activity).toHaveBeenCalledWith(expect.objectContaining({ title: "Forge", phase: "error", detail: text.textContent }));
    expect(getBackgroundJobs()).toEqual([expect.objectContaining({ title: "Forge", phase: "error", detail: text.textContent })]);
    // Another plugin's refusal is not this bar's.
    push.handlers.forEach((fn) => fn({ type: "plugin_write_refused", plugin_id: "other", unit: "doc:x", holder: "Ben" }));

    fireEvent.click(screen.getByRole("button", { name: "Ask to release" }));
    await waitFor(() => expect(api.plugin_scope_ask).toHaveBeenCalledWith("forge", "tree:scenes/ab12"));
    expect(await screen.findByText("Asked Ana to release MEGA_Duel_Stadium.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Asked Ana to release MEGA_Duel_Stadium.")).toBeNull();
  });

  it("after a reset on the server, offers Upload my copy and Clear my copy for the whole copy, each asked first", async () => {
    api.plugin_scope_status.mockResolvedValue(sharedStatus({
      state: "reset", kindLabel: "Team", resetContents: ["BrainRot TCG", "Forge", "Workflows"],
      refused: 2, items: [
        { kind: "doc", key: "card.a", state: "refused", reason: "storage full" },
        { kind: "doc", key: "card.b", state: "refused", reason: "storage full" },
      ],
    }, { readOnly: true }));
    api.space_reset_settle?.mockResolvedValue({ ok: true });
    bar();
    expect(await screen.findByText("Alpha Studio's team data was reset on the server")).toBeTruthy();
    // Retry all, Retry, Drop and Sync now wait for the choice: Retry all once uploaded the whole copy unasked.
    nav(/^Changes/);
    expect(within(panel()).getAllByText("Refused: storage full").length).toBe(2);
    expect(screen.queryByRole("button", { name: "Retry all" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Retry / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Drop my change to/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();

    nav(/^Overview/);
    fireEvent.click(screen.getByRole("button", { name: "Upload my copy" }));
    expect(await screen.findByText("Upload your copy of Alpha Studio?")).toBeTruthy();
    expect(screen.getByText(
      "This PC's whole copy of Alpha Studio's data is sent again, and everyone in Alpha Studio gets it: BrainRot TCG, Forge and Workflows.",
    )).toBeTruthy();
    expect(api.space_reset_settle).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^Upload my copy \(/ }));
    await waitFor(() => expect(api.space_reset_settle).toHaveBeenCalledWith("s1", "upload"));

    fireEvent.click(screen.getByRole("button", { name: "Clear my copy" }));
    expect(await screen.findByText("Clear your copy of Alpha Studio?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Yes, clear it/ }));
    await waitFor(() => expect(api.space_reset_settle).toHaveBeenCalledWith("s1", "clear"));
    expect(api.plugin_scope_retry).not.toHaveBeenCalled();
    expect(api.plugin_scope_drop).not.toHaveBeenCalled();
  });

  describe("plugins that keep their own folders", () => {
    const LOCAL = { kind: "personal", label: "Local", spaceId: "", readOnly: false };
    const URC = "Carried by this island's UEFN revision control";
    const NOTE = "This plugin's files are stored as it writes them on each member's PC; on the server and in transit they are encrypted.";
    /** The host's folder fields: the declared folders, the teams it offers Local into, the note. */
    const declared = (folders: Record<string, unknown>[] = [{ id: "scenes" }], offers: unknown[] = []) =>
      ({ teamFolders: folders, folderOffers: offers, folderNote: NOTE });
    const localStatus = (extra: Record<string, unknown> = {}) => ({
      ok: true, visible: true, pluginLabel: "Forge", canChange: true, state: "ok", ...declared(), binding: null,
      ...extra, scope: LOCAL,
    });
    const UNITS = [
      { folder: "scenes", unit: "MEGA_Duel_Stadium", name: "MEGA_Duel_Stadium", files: 12, bytes: 226 * 1024 ** 2 },
      { folder: "scenes", unit: "Arena", name: "Arena", files: 3, bytes: 2 * 1024 ** 2 },
    ];
    const chooseAlpha = async () => {
      fireEvent.click(await option(/Alpha Studio/));
      fireEvent.click(await screen.findByRole("button", { name: /^Switch/ }));
    };
    const choices = () => api.plugin_scope_choices.mockResolvedValue({ ok: true, choices: [
      { id: "personal", kind: "personal", label: "Local" },
      { id: "s1", kind: "shared", label: "Alpha Studio" },
    ] });
    /** A call that answers when the test says so. */
    const pending = () => {
      let answer: (value: unknown) => void = () => {};
      api.plugin_scope_set.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }));
      return (value: unknown) => answer(value);
    };
    /** Copy Local into a team: under the Overview's team picker. */
    const copyInto = async () => {
      await openPicker();
      fireEvent.click(await screen.findByRole("button", { name: "Copy Local into Alpha Studio" }));
    };

    it("a switch says it is getting the team's files, reads how far it got, then asks which Local units to share", async () => {
      api.plugin_scope_status.mockResolvedValue(localStatus());
      choices();
      const answer = pending();
      bar("forge");
      expect(await screen.findByText("Only on this PC")).toBeTruthy();
      await chooseAlpha();
      await waitFor(() => expect(api.plugin_scope_set).toHaveBeenCalledWith("forge", "s1"));
      expect(await screen.findByText("Getting Alpha Studio's files…")).toBeTruthy();
      expect(picker().disabled).toBe(true);
      // The host notes its progress without telling the panel: the bar reads it while the switch runs.
      api.plugin_scope_status.mockResolvedValue(localStatus({ state: "binding", binding: { team: "Alpha Studio", pct: 40, at: 1 } }));
      expect(await screen.findByText("Getting Alpha Studio's files… 40%", undefined, { timeout: 3000 })).toBeTruthy();

      answer({ ok: false, needs: "first_share", team: "Alpha Studio", note: NOTE, units: UNITS });
      const dialog = await shareDialog();
      expect(within(dialog).getByText("Share Forge's files with Alpha Studio?")).toBeTruthy();
      expect(within(dialog).getByText(
        "Alpha Studio has none of Forge's files yet. Pick what to copy from your Local copy; the rest stays only on this PC.",
      )).toBeTruthy();
      expect(within(dialog).getByText(NOTE)).toBeTruthy();
      expect(within(dialog).getByText("12 files · 226 MB")).toBeTruthy();
      expect(within(dialog).getByText("2 of 2 picked · 228 MB")).toBeTruthy();
      fireEvent.click(within(dialog).getByRole("checkbox", { name: /Arena/ }));
      expect(within(dialog).getByText("1 of 2 picked · 226 MB")).toBeTruthy();

      // The picked units go in: the bar says they are being sent, then shows the team's copy.
      api.plugin_scope_status.mockResolvedValue(localStatus());
      const sent = pending();
      fireEvent.click(within(dialog).getByRole("button", { name: "Copy and share" }));
      await waitFor(() => expect(api.plugin_scope_set).toHaveBeenLastCalledWith(
        "forge", "s1", { units: ["MEGA_Duel_Stadium"] }));
      noShareDialog();
      expect(await screen.findByText("Sending your files to Alpha Studio…")).toBeTruthy();
      api.plugin_scope_status.mockResolvedValue(sharedStatus({ pluginLabel: "Forge", ...declared() }));
      sent({ ok: true });
      expect(await screen.findByText(/^Up to date/)).toBeTruthy();
    });

    it("picking nothing shares nothing until Start empty; Cancel changes nothing", async () => {
      api.plugin_scope_status.mockResolvedValue(localStatus());
      choices();
      api.plugin_scope_set.mockResolvedValueOnce({ ok: false, needs: "first_share", team: "Alpha Studio", units: UNITS.slice(1) });
      bar("forge");
      await chooseAlpha();
      const dialog = await shareDialog();
      // Without the host's note the dialog still says the files are stored as written on each PC.
      expect(within(dialog).getByText(NOTE)).toBeTruthy();
      fireEvent.click(within(dialog).getByRole("checkbox", { name: /Arena/ }));
      expect((within(dialog).getByRole("button", { name: "Copy and share" }) as HTMLButtonElement).disabled).toBe(true);
      api.plugin_scope_set.mockResolvedValue({ ok: true });
      fireEvent.click(within(dialog).getByRole("button", { name: "Start empty" }));
      await waitFor(() => expect(api.plugin_scope_set).toHaveBeenLastCalledWith("forge", "s1", { start_empty: true }));

      api.plugin_scope_set.mockResolvedValueOnce({ ok: false, needs: "first_share", team: "Alpha Studio", units: [] });
      await chooseAlpha();
      const again = await shareDialog();
      expect(within(again).getByText("Your Local copy is empty.")).toBeTruthy();
      fireEvent.click(within(again).getByRole("button", { name: "Cancel" }));
      noShareDialog();
      expect(api.plugin_scope_set).toHaveBeenCalledTimes(3);
    });

    it("a failed pull fails the switch with the reason, and nothing changes", async () => {
      api.plugin_scope_status.mockResolvedValue(localStatus());
      choices();
      api.plugin_scope_set.mockResolvedValue({ ok: false, error: "Alpha Studio's files didn't arrive (offline). Nothing changed." });
      bar("forge");
      await chooseAlpha();
      expect(await screen.findByText("Alpha Studio's files didn't arrive (offline). Nothing changed.")).toBeTruthy();
      noShareDialog();
      expect(picker().disabled).toBe(false);
      expect((await option(/^Local/)).getAttribute("aria-selected")).toBe("true");
    });

    it("a folder that can't be shared from this PC says why and offers no team", async () => {
      api.plugin_scope_status.mockResolvedValue(localStatus(declared([{ id: "design", refused: URC }, { id: "noproject" }])));
      choices();
      bar("roguelike");
      expect(await option(/^Local/)).toBeTruthy();
      expect(screen.queryByRole("option", { name: /Alpha Studio/ })).toBeNull();
      expect(screen.getAllByText(URC).length).toBe(2);
      cleanup();

      // Nothing else to share: no bar at all (Settings → Data says why).
      api.plugin_scope_status.mockResolvedValue(localStatus({ shareable: false, ...declared([{ id: "design", refused: URC }]) }));
      const { container } = bar("roguelike");
      await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalled());
      expect(container.textContent).toBe("");
    });

    it("offers to copy Local into a team it was linked to before: the first share's picker", async () => {
      api.plugin_scope_status.mockResolvedValue(localStatus(declared(undefined, [{ space: "s1", label: "Alpha Studio" }])));
      choices();
      api.plugin_scope_set.mockResolvedValueOnce({ ok: false, needs: "first_share", team: "Alpha Studio", units: UNITS });
      bar("forge");
      await copyInto();
      // It asks which units even when the team has files already.
      await waitFor(() => expect(api.plugin_scope_set).toHaveBeenCalledWith("forge", "s1", { pick: true }));
      const dialog = await shareDialog();
      api.plugin_scope_set.mockResolvedValue({ ok: true });
      fireEvent.click(within(dialog).getByRole("button", { name: "Copy and share" }));
      await waitFor(() => expect(api.plugin_scope_set).toHaveBeenLastCalledWith(
        "forge", "s1", { units: ["MEGA_Duel_Stadium", "Arena"] }));
    });

    it("the offer, when the team already has the files, switches and says nothing was copied", async () => {
      api.plugin_scope_status.mockResolvedValue(localStatus(declared(undefined, [{ space: "s1", label: "Alpha Studio" }])));
      choices();
      api.plugin_scope_set.mockResolvedValue({ ok: true });
      bar("forge");
      await copyInto();
      expect(await screen.findByText(
        "Alpha Studio already has Forge's files, so nothing was copied. Your Local copy stays on this PC.",
      )).toBeTruthy();
      noShareDialog();
      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      expect(screen.queryByText(/already has Forge's files/)).toBeNull();
    });

    it("on a team, offers to copy Local in for a project the team has no copy of, and names what it lacks", async () => {
      api.plugin_scope_status.mockResolvedValue(sharedStatus({
        pluginLabel: "Forge", ...declared(undefined, [{ space: "s1", label: "Alpha Studio" }]),
      }));
      choices();
      api.plugin_scope_set.mockResolvedValueOnce({
        ok: false, needs: "first_share", team: "Alpha Studio", units: UNITS.slice(1), partial: true,
      });
      bar("forge");
      await copyInto();
      await waitFor(() => expect(api.plugin_scope_set).toHaveBeenCalledWith("forge", "s1", { pick: true }));
      const dialog = await shareDialog();
      expect(within(dialog).getByText(
        "Alpha Studio has some of Forge's files already. Pick what else to copy from your Local copy; the rest stays only on this PC.",
      )).toBeTruthy();
      expect(within(dialog).getByRole("button", { name: "Copy nothing" })).toBeTruthy();
      api.plugin_scope_set.mockResolvedValue({ ok: true });
      fireEvent.click(within(dialog).getByRole("button", { name: "Copy and share" }));
      await waitFor(() => expect(api.plugin_scope_set).toHaveBeenLastCalledWith("forge", "s1", { units: ["Arena"] }));
    });

    it("no offer while a folder can't be shared from this PC", async () => {
      api.plugin_scope_status.mockResolvedValue(localStatus(declared([{ id: "design", refused: URC }], [{ space: "s1", label: "Alpha Studio" }])));
      choices();
      bar("roguelike");
      await option(/^Local/);
      expect(screen.queryByRole("button", { name: "Copy Local into Alpha Studio" })).toBeNull();
    });

    it("on a team: a unit changed while someone holds it, and updates waiting behind Update now", async () => {
      api.plugin_scope_status.mockResolvedValue(sharedStatus({
        pluginLabel: "Forge", ...declared(),
        folders: [{ folder_id: "scenes", unit: "MEGA_Duel_Stadium", name: "MEGA_Duel_Stadium", state: "quarantined",
          reason: "Changed here while Ana holds it" }],
      }));
      bar("forge");
      expect(await screen.findByText("MEGA_Duel_Stadium: changed here while Ana holds it")).toBeTruthy();
      cleanup();

      api.plugin_scope_status.mockResolvedValue(sharedStatus({
        pluginLabel: "Forge", ...declared(), head: 9,
        changes: { incoming: 2, incomingBy: ["Ana"], held: true },
      }));
      api.plugin_scope_pull?.mockResolvedValue({ ok: true });
      const changed = vi.fn();
      bar("forge", changed);
      expect(await screen.findByText("2 updates from Ana")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Update now" }));
      await waitFor(() => expect(api.plugin_scope_pull).toHaveBeenCalledWith("forge"));
      // The panel merges the updates once they are in (its open item stays open).
      await waitFor(() => expect(changed).toHaveBeenCalledWith(expect.anything(), { reason: "synced" }));
    });
  });

  describe("what the panel is told", () => {
    const watch = (active = true) => {
      const changed = vi.fn();
      const view = render(
        <ConfirmModalProvider>
          <PluginScopeBar pluginId="island-media-kit" onScopeChanged={changed} active={active} />
        </ConfirmModalProvider>,
      );
      const rerender = (on: boolean) => view.rerender(
        <ConfirmModalProvider>
          <PluginScopeBar pluginId="island-media-kit" onScopeChanged={changed} active={on} />
        </ConfirmModalProvider>,
      );
      return { changed, rerender };
    };
    const send = (event: Record<string, unknown>) => push.handlers.forEach((fn) => fn(event));

    it("teammates' changes: one note per burst with every key, never a restart", async () => {
      api.plugin_scope_status.mockResolvedValue(sharedStatus());
      const { changed } = watch();
      await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalledTimes(1));
      send({ type: "plugin_scope_changed", plugins: ["island-media-kit"], synced: true,
        keys: { "island-media-kit": ["project.a"] } });
      send({ type: "plugin_scope_changed", plugins: ["island-media-kit", "forge"], synced: true,
        keys: { "island-media-kit": ["sequence.a.1", "project.a"], forge: ["scene.x"] } });
      await waitFor(() => expect(changed).toHaveBeenCalledTimes(1), { timeout: 3000 });
      expect(changed).toHaveBeenCalledWith(expect.objectContaining({ ok: true }),
        { reason: "synced", keys: ["project.a", "sequence.a.1"] });
      // The burst made one status read.
      expect(api.plugin_scope_status).toHaveBeenCalledTimes(2);
    });

    it("a lock, the mode, another plugin's round or a switch restarting it: the bar only", async () => {
      api.plugin_scope_status.mockResolvedValue(sharedStatus());
      const { changed } = watch();
      await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalledTimes(1));
      send({ type: "plugin_scope_changed", plugins: ["island-media-kit"], status: true });
      send({ type: "plugin_scope_changed", plugins: ["forge"], synced: true });
      send({ type: "plugin_scope_changed", plugins: [], synced: true });
      send({ type: "plugin_scope_changed", plugins: ["island-media-kit"], switched: true });
      await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalledTimes(2));
      await new Promise((r) => setTimeout(r, 1000));
      expect(changed).not.toHaveBeenCalled();
    });

    it("whose data it shows moved: the panel starts over at once", async () => {
      api.plugin_scope_status.mockResolvedValue(sharedStatus());
      const { changed } = watch();
      await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalledTimes(1));
      api.plugin_scope_status.mockResolvedValue(sharedStatus({}, { spaceId: "s2", label: "Beta" }));
      send({ type: "plugin_scope_changed", plugins: ["island-media-kit"], synced: true });
      await waitFor(() => expect(changed).toHaveBeenCalledWith(expect.anything(), { reason: "switched" }));
      expect(changed).toHaveBeenCalledTimes(1);
    });

    it("hidden: no status reads and nothing told; shown again: one read and one note", async () => {
      api.plugin_scope_status.mockResolvedValue(sharedStatus());
      const { changed, rerender } = watch(false);
      await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalledTimes(1));
      for (const key of ["project.a", "project.b", "project.c"]) {
        send({ type: "plugin_scope_changed", plugins: ["island-media-kit"], synced: true, keys: { "island-media-kit": [key] } });
      }
      await new Promise((r) => setTimeout(r, 600));
      expect(api.plugin_scope_status).toHaveBeenCalledTimes(1);
      rerender(true);
      await waitFor(() => expect(changed).toHaveBeenCalledTimes(1), { timeout: 3000 });
      expect(changed).toHaveBeenCalledWith(expect.anything(), { reason: "synced", keys: ["project.a", "project.b", "project.c"] });
      expect(api.plugin_scope_status).toHaveBeenCalledTimes(2);
    });

    it("the visible pane publishes its copy for the header; a hidden one doesn't", async () => {
      api.plugin_scope_status.mockResolvedValue(sharedStatus({ pluginLabel: "Island Media Kit" }, { label: "Mindful Path" }));
      const { rerender } = watch(false);
      await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalledTimes(1));
      expect(getTeamScope()).toBeNull();
      rerender(true);
      await waitFor(() => expect(getTeamScope()).toMatchObject({
        pluginId: "island-media-kit", plugin: "Island Media Kit", status: { scope: { label: "Mindful Path" } },
      }));
      rerender(false);
      expect(getTeamScope()).toBeNull();
    });

    it("every pane, hidden too, is in the all-panes map until it closes", async () => {
      api.plugin_scope_status.mockResolvedValue(sharedStatus({ pluginLabel: "Island Media Kit" }, { label: "Mindful Path" }));
      watch(false);
      await waitFor(() => expect(getAllTeamScopes()["island-media-kit"]).toMatchObject({
        plugin: "Island Media Kit", status: { scope: { label: "Mindful Path" } },
      }));
      expect(getTeamScope()).toBeNull();
      cleanup();
      expect(getAllTeamScopes()["island-media-kit"]).toBeUndefined();
    });

    it("tells the pane whether it's a team copy and how many changes are on their way", async () => {
      api.plugin_scope_status.mockResolvedValue(sharedStatus({ changes: { incoming: 2 } }));
      const told = vi.fn();
      render(
        <ConfirmModalProvider>
          <PluginScopeBar pluginId="island-media-kit" onScopeChanged={() => {}} onSyncState={told} />
        </ConfirmModalProvider>,
      );
      await waitFor(() => expect(told).toHaveBeenLastCalledWith({ shared: true, incoming: 2 }));
    });
  });

  describe("live groups", () => {
    // Live itself is the title bar's (TeamLiveDropdown): the bar tells the page whether it
    // is live and whose screen it follows, and People's rows still Join.
    const ILIYA = { room: "h:d1", host: "iliya", hostDevice: "d1", members: [{ name: "iliya", user: "u1", device: "d1" }] };
    const MO = { room: "h:d2", host: "Mo", hostDevice: "d2",
      members: [{ name: "Mo", user: "u2", device: "d2" }, { name: "Cy", user: "u3", device: "d3" }] };
    const MINE = { room: "h:me", host: "You", hostDevice: "me", hostIsMe: true, mine: true,
      members: [{ name: "You", device: "me", me: true }] };
    const live = (on: boolean, groups: Record<string, unknown>[] = [], peers: Record<string, unknown>[] = []) =>
      sharedStatus({ live: { supported: true, on, room: on ? "h:me" : "", state: on ? "live" : "off", peers, groups } });
    const followBar = (followed: ReturnType<typeof vi.fn>) =>
      render(
        <ConfirmModalProvider>
          <PluginScopeBar pluginId="brainrot-tcg" onScopeChanged={() => {}} onFollow={followed} />
        </ConfirmModalProvider>,
      );

    it("has no Live button of its own: Live is the title bar's", async () => {
      api.plugin_scope_status.mockResolvedValue(live(true, [MINE, ILIYA]));
      bar();
      await shown();
      expect(screen.queryByRole("button", { name: /^(Live|You're live)/ })).toBeNull();
      expect(api.plugin_live_join).not.toHaveBeenCalled();  // nor does it go live by itself
    });

    it("in a teammate's group the page shares the leader's screen until that is turned off", async () => {
      api.plugin_scope_status.mockResolvedValue(
        live(true, [{ ...ILIYA, mine: true, members: [...ILIYA.members, { name: "You", device: "me", me: true }] }]));
      const followed = vi.fn();
      followBar(followed);
      await waitFor(() => expect(followed).toHaveBeenLastCalledWith("d1", false, []));
      act(() => writeLiveFollow(false));  // Same screen as the group, off in the Live menu
      await waitFor(() => expect(followed).toHaveBeenLastCalledWith("", false, []));
      expect(localStorage.getItem("ducky.liveFollow")).toBe("0");
    });

    it("the host left: whoever has been in the group longest leads it, and the page follows them", async () => {
      const followed = vi.fn();
      const passedOn = { room: "h:d1", host: "Mo", hostDevice: "d2", mine: true,
        members: [{ name: "Mo", device: "d2" }, { name: "You", device: "me", me: true }] };
      api.plugin_scope_status.mockResolvedValue(live(true, [passedOn]));
      followBar(followed);
      await waitFor(() => expect(followed).toHaveBeenLastCalledWith("d2", false, []));
    });

    it("leading a group, the page's screen is the group's: nobody to follow", async () => {
      const followed = vi.fn();
      api.plugin_scope_status.mockResolvedValue(live(true, [{ ...MINE, room: "h:d1",
        members: [...MINE.members, { name: "Mo", device: "d2" }] }]));
      followBar(followed);
      await waitFor(() => expect(followed).toHaveBeenLastCalledWith("", true, []));
    });

    it("a groupmate on an older app: the leader's page does their clicks", async () => {
      const followed = vi.fn();
      // The room is iliya's, but iliya's app is older: this PC leads it.
      const withOlder = { room: "h:d1", host: "You", hostDevice: "me", hostIsMe: true, mine: true,
        members: [{ name: "iliya", device: "d1", older: true }, { name: "You", device: "me", me: true }] };
      api.plugin_scope_status.mockResolvedValue(live(true, [withOlder]));
      followBar(followed);
      await waitFor(() => expect(followed).toHaveBeenLastCalledWith("", true, ["d1"]));
    });

    it("alone in its own group, the page leads nobody", async () => {
      const followed = vi.fn();
      api.plugin_scope_status.mockResolvedValue(live(true, [{ ...MINE, room: "h:d1" }]));
      followBar(followed);
      await waitFor(() => expect(followed).toHaveBeenCalled());
      expect(followed).toHaveBeenLastCalledWith("", false, []);
    });

    it("tells the pane when live goes on and off", async () => {
      api.plugin_scope_status.mockResolvedValue(live(true, [MINE]));
      const told = vi.fn();
      render(
        <ConfirmModalProvider>
          <PluginScopeBar pluginId="brainrot-tcg" onScopeChanged={() => {}} onLiveState={told} />
        </ConfirmModalProvider>,
      );
      await waitFor(() => expect(told).toHaveBeenLastCalledWith(true));
    });

    it("who is live moved: the bar reads its status again", async () => {
      api.plugin_scope_status.mockResolvedValue(live(false, [ILIYA]));
      bar();
      await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalledTimes(1));
      api.plugin_scope_status.mockResolvedValue(live(false, [ILIYA, MO]));
      push.handlers.forEach((fn) => fn({ type: "live_peers", plugin: "brainrot-tcg", on: true, peers: [] }));
      push.handlers.forEach((fn) => fn({ type: "live_peers", plugin: "other", on: true, peers: [] }));
      await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalledTimes(2));
    });

    it("People in the Team modal: Join a teammate's group from their row", async () => {
      api.plugin_scope_status.mockResolvedValue({
        ...live(false, [ILIYA]),
        people: [{ user_name: "iliya", user: "u1", device: "Laptop", deviceId: "d1", plugins: { "brainrot-tcg": { viewing: true, live: "h:d1" } } }],
      });
      api.plugin_live_join.mockResolvedValue({ ok: true });
      bar();
      await shown();
      nav(/^People/);
      expect(within(panel()).getByText("Hosting")).toBeTruthy();
      fireEvent.click(within(panel()).getByRole("button", { name: "Join iliya" }));
      await waitFor(() => expect(api.plugin_live_join).toHaveBeenCalledWith("brainrot-tcg", "h:d1"));
    });
  });

  it("after a reset, without the host call, says so and offers no choice", async () => {
    api.space_reset_settle = undefined;
    api.plugin_scope_status.mockResolvedValue(sharedStatus({ state: "reset", kindLabel: "Team" }, { readOnly: true }));
    bar();
    expect(await screen.findByText("Alpha Studio's team data was reset on the server")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Upload my copy" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear my copy" })).toBeNull();
  });
});
