// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { _resetBackgroundActivityForTests, getBackgroundJobs } from "../hooks/backgroundActivity";
import { writeLiveFollow } from "./liveFollow";
import { PluginScopeBar } from "./PluginScopeBar";
import { getAllTeamScopes, getTeamScope } from "./teamScopeStore";

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

/** The pane's headless bar (its screens are Ducky Account's team menu now). */
const bar = (pluginId = "brainrot-tcg", onScopeChanged: () => void = () => {}) =>
  render(
    <ConfirmModalProvider>
      <PluginScopeBar pluginId={pluginId} onScopeChanged={onScopeChanged} />
    </ConfirmModalProvider>,
  );
/** The bar published its copy for the header. */
const shown = () => waitFor(() => expect(getTeamScope()).not.toBeNull());

/** A shared copy's status, with ``extra`` on top (``scope`` fields merge too). */
const sharedStatus = (extra: Record<string, unknown> = {}, scope: Record<string, unknown> = {}) => ({
  ok: true, visible: true, pluginLabel: "BrainRot TCG", canChange: true, state: "ok", pending: 0,
  syncedAt: Date.now() / 1000 - 5, ...extra,
  scope: { kind: "shared", label: "Alpha Studio", spaceId: "s1", readOnly: false, ...scope },
});

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

  it("a refused write is told through the alerts system", async () => {
    api.plugin_scope_status.mockResolvedValue(sharedStatus({ pluginLabel: "Forge" }));
    api.notify_activity.mockResolvedValue(true);
    bar("forge");
    await shown();
    push.handlers.forEach((fn) => fn({
      type: "plugin_write_refused", plugin_id: "forge", unit: "tree:scenes/ab12", unit_name: "MEGA_Duel_Stadium", holder: "Ana",
      since: new Date(2026, 9, 17, 15, 2).getTime() / 1000,
    }));
    const detail = expect.stringMatching(/^Forge: MEGA_Duel_Stadium is locked by Ana since .+\. Your change wasn't saved\.$/);
    await waitFor(() => expect(api.notify_activity).toHaveBeenCalledWith(expect.objectContaining({ title: "Forge", phase: "error", detail })));
    expect(getBackgroundJobs()).toEqual([expect.objectContaining({ title: "Forge", phase: "error", detail })]);
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
    // Live itself is the title bar's (Ducky Account's team menu): the bar tells the page whether it
    // is live and whose screen it follows.
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
  });

});
