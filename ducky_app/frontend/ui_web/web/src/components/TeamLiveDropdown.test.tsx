// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { _resetBackgroundActivityForTests, getBackgroundJobs } from "../hooks/backgroundActivity";
import { _resetLiveOverviewForTests } from "../hooks/useLiveOverview";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { OPEN_TEAM_MODAL_EVENT } from "../plugin-ui/scopeActivity";
import { publishTeamScope, unpublishTeamScope, type TeamScope } from "../plugin-ui/teamScopeStore";
import type { LiveTeam } from "../types/panel";
import { TeamLiveDropdown } from "./TeamLiveDropdown";

const api = vi.hoisted(() => ({
  live_overview: vi.fn(),
  live_team_join: vi.fn(),
  live_team_leave: vi.fn(),
  live_team_invite: vi.fn(),
}));
const remote = vi.hoisted(() => ({ on: false }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => remote.on }));
const push = vi.hoisted(() => ({ handlers: new Set<(event: Record<string, unknown>) => void>() }));
vi.mock("../hooks/usePanelPushBus", () => ({
  installPanelPushBus: () => {},
  subscribePanelPush: (fn: (event: Record<string, unknown>) => void) => {
    push.handlers.add(fn);
    return () => push.handlers.delete(fn);
  },
}));

afterEach(() => {
  act(() => unpublishTeamScope("island-media-kit"));
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
  remote.on = false;
  push.handlers.clear();
  _resetLiveOverviewForTests();
  _resetBackgroundActivityForTests();
});

const ILIYA = { room: "h:d1", host: "iliya", hostDevice: "d1",
  members: [{ name: "iliya", user: "u1", device: "d1", plugin: "kanban", pluginLabel: "Kanban" },
    { name: "Mo", user: "u2", device: "d2", plugin: "cards", pluginLabel: "Cards" }] };
const team = (extra: Partial<LiveTeam> = {}): LiveTeam => ({
  account: "acct", space: "s1", label: "Alpha", supported: true, on: false, groups: [], ...extra,
});
const overview = (...teams: LiveTeam[]) => ({ ok: true, on: teams.some((t) => t.on), teams });
const trigger = () => screen.getByRole("button", { name: "Team and Live" });
const scope = (extra: Partial<TeamScope> = {}): TeamScope => ({
  pluginId: "island-media-kit", plugin: "Island Media Kit",
  status: { ok: true, visible: true, canChange: false, scope: { kind: "shared", label: "Mindful Path", spaceId: "s1", readOnly: false } },
  phrase: { key: "up_to_date", text: "Up to date", tone: "" },
  syncing: false, busy: false, switching: false, shared: true, variant: "shared", kind: "team", current: "s1",
  menu: [], offers: [], refusal: "", manual: false, canSync: true, failing: false, canPush: false, canPull: false,
  badge: 0, conflicts: 0, notes: [],
  actions: {
    pull: vi.fn(), push: vi.fn(), syncNow: vi.fn(), choose: vi.fn(), copyInto: vi.fn(), settle: vi.fn(),
    loadChoices: vi.fn(async () => {}), refresh: vi.fn(),
  },
  ...extra,
} as TeamScope);

describe("Team and Live in the title bar", () => {
  it("shows nothing with no team copy and no sharing pane, and nothing on the phone", async () => {
    api.live_overview.mockResolvedValue(overview());
    const { container } = render(<TeamLiveDropdown />);
    await waitFor(() => expect(api.live_overview).toHaveBeenCalled());
    expect(container.textContent).toBe("");
    cleanup();
    _resetLiveOverviewForTests();
    remote.on = true;
    api.live_overview.mockResolvedValue(overview(team()));
    const phone = render(<TeamLiveDropdown />);
    await waitFor(() => expect(api.live_overview).toHaveBeenCalledTimes(2));
    expect(phone.container.textContent).toBe("");
  });

  it("is one button: green with the live count, the team dot and a red conflict count", async () => {
    const mine = { ...ILIYA, mine: true, members: [...ILIYA.members, { name: "You", device: "me", me: true }] };
    api.live_overview.mockResolvedValue(overview(team({ on: true, groups: [mine] })));
    render(<TeamLiveDropdown />);
    act(() => publishTeamScope(scope({ badge: 2, conflicts: 2 })));
    await waitFor(() => expect(trigger().className).toContain("is-live"));
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(trigger().querySelector(".live-trigger-count")?.textContent).toBe("3");
    expect(trigger().querySelector(".plugin-scope-dot--shared")).toBeTruthy();
    const count = trigger().querySelector(".app-header-team-btn__count");
    expect(count?.textContent).toBe("2");
    expect(count?.classList.contains("is-alert")).toBe(true);
    expect(trigger().title).toBe("Mindful Path · Up to date\nLive with iliya in Alpha · 3 people");
  });

  it("shows with only a sharing pane; Settings opens the Team modal", async () => {
    api.live_overview.mockResolvedValue(overview());
    render(<TeamLiveDropdown />);
    await waitFor(() => expect(api.live_overview).toHaveBeenCalled());
    act(() => publishTeamScope(scope()));
    fireEvent.click(trigger());
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText("Mindful Path · Island Media Kit")).toBeTruthy();
  });

  it("the tray's Resolve opens the Team modal on Conflicts", async () => {
    api.live_overview.mockResolvedValue(overview());
    render(<ConfirmModalProvider><TeamLiveDropdown /></ConfirmModalProvider>);
    act(() => publishTeamScope(scope({
      badge: 1, conflicts: 1,
      status: {
        ok: true, visible: true, canChange: false, conflicts: [{ kind: "doc", key: "deck", name: "deck", by: "Ana", reason: "both" }],
        scope: { kind: "shared", label: "Mindful Path", spaceId: "s1", readOnly: false },
      },
    })));
    expect(screen.queryByRole("dialog")).toBeNull();
    act(() => {
      window.dispatchEvent(new CustomEvent(OPEN_TEAM_MODAL_EVENT, { detail: { section: "conflicts" } }));
    });
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Conflicts/ }).getAttribute("aria-current")).toBe("page");
  });

  it("shows up when a sync push comes after an empty first read", async () => {
    api.live_overview.mockResolvedValue(overview());
    const { container } = render(<TeamLiveDropdown />);
    await waitFor(() => expect(api.live_overview).toHaveBeenCalledTimes(1));
    expect(container.textContent).toBe("");
    api.live_overview.mockResolvedValue(overview(team()));
    push.handlers.forEach((fn) => fn({ type: "plugin_scope_changed", plugins: ["kanban"], synced: true }));
    expect(await waitFor(trigger)).toBeTruthy();
  });

  it("lists each group with who's in which plugin; Join goes into it with every plugin", async () => {
    api.live_overview.mockResolvedValue(overview(team({ groups: [ILIYA] })));
    api.live_team_join.mockResolvedValue({ ok: true });
    render(<TeamLiveDropdown />);
    expect((await waitFor(trigger)).className).not.toContain("is-live");
    fireEvent.click(trigger());
    const menu = await screen.findByRole("menu", { name: "Live" });
    expect(within(menu).getByText("Live in Alpha")).toBeTruthy();
    expect(within(menu).getByText("2 people")).toBeTruthy();
    expect(within(menu).getByText("iliya in Kanban, Mo in Cards")).toBeTruthy();
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Join iliya" }));
    await waitFor(() => expect(api.live_team_join).toHaveBeenCalledWith("acct", "s1", "h:d1"));
  });

  it("Start live where nobody is; live: green with how many are in the group, and Leave", async () => {
    api.live_overview.mockResolvedValue(overview(team()));
    api.live_team_join.mockResolvedValue({ ok: true });
    api.live_team_leave.mockResolvedValue({ ok: true });
    render(<TeamLiveDropdown />);
    fireEvent.click(await waitFor(trigger));
    expect(await screen.findByText("Nobody's live in Alpha yet.")).toBeTruthy();
    const mine = { ...ILIYA, mine: true, members: [...ILIYA.members, { name: "You", device: "me", me: true }] };
    api.live_overview.mockResolvedValue(overview(team({ on: true, room: "h:d1", groups: [mine] })));
    fireEvent.click(screen.getByRole("menuitem", { name: "Start live" }));
    await waitFor(() => expect(api.live_team_join).toHaveBeenCalledWith("acct", "s1", ""));
    await waitFor(() => expect(trigger().className).toContain("is-live"));
    expect(trigger().textContent).toBe("3");
    fireEvent.click(trigger());
    expect(screen.queryByRole("menuitem", { name: "Start live" })).toBeNull();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Leave" }));
    await waitFor(() => expect(api.live_team_leave).toHaveBeenCalledWith("acct", "s1"));
  });

  it("Same screen as the group is one setting for every plugin", async () => {
    const mine = { ...ILIYA, mine: true, members: [...ILIYA.members, { name: "You", device: "me", me: true }] };
    api.live_overview.mockResolvedValue(overview(team({ on: true, groups: [mine] })));
    render(<TeamLiveDropdown />);
    fireEvent.click(await waitFor(trigger));
    const box = await screen.findByRole("checkbox", { name: "Same screen as iliya" });
    expect((box as HTMLInputElement).checked).toBe(true);
    fireEvent.click(box);
    expect(localStorage.getItem("ducky.liveFollow")).toBe("0");
    expect((box as HTMLInputElement).checked).toBe(false);
  });

  it("pulses while linking; the dot turns amber when someone can't be reached or needs to update", async () => {
    const mine = { ...ILIYA, mine: true, members: [...ILIYA.members, { name: "You", device: "me", me: true }] };
    api.live_overview.mockResolvedValue(overview(team({ on: true, groups: [mine], connecting: ["Mo"] })));
    render(<TeamLiveDropdown />);
    await waitFor(() => expect(trigger().className).toContain("is-connecting"));
    expect(trigger().querySelector(".plugin-scope-dot--warn")).toBeNull();
    fireEvent.click(trigger());
    expect(await screen.findByText("Connecting to Mo…")).toBeTruthy();
    cleanup();
    _resetLiveOverviewForTests();
    const older = { ...mine, members: [{ ...mine.members[0], partial: true }, mine.members[1], mine.members[2]] };
    api.live_overview.mockResolvedValue(overview(team({ on: true, groups: [older], unlinked: ["Mo"] })));
    render(<TeamLiveDropdown />);
    await waitFor(() => expect(trigger().querySelector(".plugin-scope-dot--warn")).toBeTruthy());
    fireEvent.click(trigger());
    expect(await screen.findByText(/^Can't reach Mo on this network/)).toBeTruthy();
    expect(screen.getByText(/^iliya needs to update UEFN Ducky to be live in every plugin\. Until then they're live in Kanban only/))
      .toBeTruthy();
  });

  it("says why a team copy can't go live", async () => {
    api.live_overview.mockResolvedValue(overview(team({ supported: false, reason: "This team service has no live editing." })));
    render(<TeamLiveDropdown />);
    fireEvent.click(await waitFor(trigger));
    expect(await screen.findByText("This team service has no live editing.")).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Start live" })).toBeNull();
  });

  it("reads again when who is live moved, and says so in the activity list when Live ended by itself", async () => {
    api.live_overview.mockResolvedValue(overview(team({ on: true, groups: [{ ...ILIYA, mine: true }] })));
    render(<TeamLiveDropdown />);
    await waitFor(() => expect(trigger().className).toContain("is-live"));
    api.live_overview.mockResolvedValue(overview(team()));
    push.handlers.forEach((fn) => fn({ type: "live_team", account: "acct", space: "s1", on: false, ended: "alone" }));
    await waitFor(() => expect(trigger().className).not.toContain("is-live"));
    const row = getBackgroundJobs().find((j) => j.id === "live-ended:s1");
    expect(row?.title).toBe("Live ended in Alpha");
    expect(row?.detail).toMatch(/^Everyone else left the group/);
  });

  it("lists who else is online; Invite asks them and the menu stays open", async () => {
    const online = [{ name: "Bo", device: "d_bo" }, { name: "Mo", device: "d_mo", live: true, invited: true }];
    api.live_overview.mockResolvedValue(overview(team({ online })));
    api.live_team_invite.mockResolvedValue({ ok: true });
    render(<TeamLiveDropdown />);
    fireEvent.click(await waitFor(trigger));
    const menu = await screen.findByRole("menu", { name: "Live" });
    expect(within(menu).getByText("Online")).toBeTruthy();
    expect(within(menu).getByText("Live in another group")).toBeTruthy();
    expect((within(menu).getByRole("menuitem", { name: "Invited Mo" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Invite Bo" }));
    await waitFor(() => expect(api.live_team_invite).toHaveBeenCalledWith("acct", "s1", "d_bo"));
    expect(screen.getByRole("menu", { name: "Live" })).toBeTruthy();
  });

  it("an invite pops Join with a countdown; Join goes into their group, and it goes by itself", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      api.live_overview.mockResolvedValue(overview(team()));
      api.live_team_join.mockResolvedValue({ ok: true });
      render(<TeamLiveDropdown />);
      await waitFor(trigger);
      const send = (expires: number) =>
        act(() => push.handlers.forEach((fn) => fn({ type: "live_invite", account: "acct", space: "s1", room: "h:d_mo", from: "Mo", expires })));
      send(Date.now() / 1000 + 30);
      expect(await screen.findByText("Mo invites you to go live.")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Join (30)" }));
      await waitFor(() => expect(api.live_team_join).toHaveBeenCalledWith("acct", "s1", "h:d_mo"));
      await waitFor(() => expect(screen.queryByText("Mo invites you to go live.")).toBeNull());
      send(Date.now() / 1000 + 2);
      expect(await screen.findByText("Mo invites you to go live.")).toBeTruthy();
      for (let i = 0; i < 3; i += 1) await act(async () => { vi.advanceTimersByTime(1000); });
      await waitFor(() => expect(screen.queryByText("Mo invites you to go live.")).toBeNull());
    } finally {
      vi.useRealTimers();
    }
  });

  it("an error joining shows in the menu", async () => {
    api.live_overview.mockResolvedValue(overview(team()));
    api.live_team_join.mockResolvedValue({ ok: false, error: "Couldn't go live: no link" });
    render(<TeamLiveDropdown />);
    fireEvent.click(await waitFor(trigger));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Start live" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't go live: no link");
  });
});
