// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { _resetBackgroundActivityForTests, getBackgroundJobs } from "../hooks/backgroundActivity";
import { _resetLiveOverviewForTests } from "../hooks/useLiveOverview";
import type { LiveTeam } from "../types/panel";
import { LiveHeaderDropdown } from "./LiveHeaderDropdown";

const api = vi.hoisted(() => ({
  live_overview: vi.fn(),
  live_team_join: vi.fn(),
  live_team_leave: vi.fn(),
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
const trigger = () => screen.getByRole("button", { name: "Live" });

describe("Live in the title bar", () => {
  it("shows nothing while no plugin here is on a team copy, and nothing on the phone", async () => {
    api.live_overview.mockResolvedValue(overview());
    const { container } = render(<LiveHeaderDropdown />);
    await waitFor(() => expect(api.live_overview).toHaveBeenCalled());
    expect(container.textContent).toBe("");
    cleanup();
    _resetLiveOverviewForTests();
    remote.on = true;
    api.live_overview.mockResolvedValue(overview(team()));
    const phone = render(<LiveHeaderDropdown />);
    await waitFor(() => expect(api.live_overview).toHaveBeenCalledTimes(2));
    expect(phone.container.textContent).toBe("");
  });

  it("lists each group with who's in which plugin; Join goes into it with every plugin", async () => {
    api.live_overview.mockResolvedValue(overview(team({ groups: [ILIYA] })));
    api.live_team_join.mockResolvedValue({ ok: true });
    render(<LiveHeaderDropdown />);
    expect((await waitFor(trigger)).className).not.toContain("is-live");  // grey: not live here
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
    render(<LiveHeaderDropdown />);
    fireEvent.click(await waitFor(trigger));
    expect(await screen.findByText("Nobody's live in Alpha yet.")).toBeTruthy();
    const mine = { ...ILIYA, mine: true, members: [...ILIYA.members, { name: "You", device: "me", me: true }] };
    api.live_overview.mockResolvedValue(overview(team({ on: true, room: "h:d1", groups: [mine] })));
    fireEvent.click(screen.getByRole("menuitem", { name: "Start live" }));
    await waitFor(() => expect(api.live_team_join).toHaveBeenCalledWith("acct", "s1", ""));
    await waitFor(() => expect(trigger().className).toContain("is-live"));
    expect(trigger().textContent).toBe("3");
    expect(trigger().title).toBe("Live with iliya in Alpha · 3 people");
    fireEvent.click(trigger());
    expect(screen.queryByRole("menuitem", { name: "Start live" })).toBeNull();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Leave" }));
    await waitFor(() => expect(api.live_team_leave).toHaveBeenCalledWith("acct", "s1"));
  });

  it("Same screen as the group is one setting for every plugin", async () => {
    const mine = { ...ILIYA, mine: true, members: [...ILIYA.members, { name: "You", device: "me", me: true }] };
    api.live_overview.mockResolvedValue(overview(team({ on: true, groups: [mine] })));
    render(<LiveHeaderDropdown />);
    fireEvent.click(await waitFor(trigger));
    const box = await screen.findByRole("checkbox", { name: /Same screen as the group/ });
    expect(screen.getByText(/through iliya, who leads the group/)).toBeTruthy();
    expect((box as HTMLInputElement).checked).toBe(true);
    fireEvent.click(box);
    expect(localStorage.getItem("ducky.liveFollow")).toBe("0");
    expect((box as HTMLInputElement).checked).toBe(false);
  });

  it("pulses while linking, and the corner dot says someone can't be reached or needs to update", async () => {
    const mine = { ...ILIYA, mine: true, members: [...ILIYA.members, { name: "You", device: "me", me: true }] };
    api.live_overview.mockResolvedValue(overview(team({ on: true, groups: [mine], connecting: ["Mo"] })));
    render(<LiveHeaderDropdown />);
    await waitFor(() => expect(trigger().className).toContain("is-connecting"));
    expect(trigger().querySelector(".live-trigger-dot")).toBeNull();
    fireEvent.click(trigger());
    expect(await screen.findByText("Connecting to Mo…")).toBeTruthy();
    cleanup();
    _resetLiveOverviewForTests();
    const older = { ...mine, members: [{ ...mine.members[0], partial: true }, mine.members[1], mine.members[2]] };
    api.live_overview.mockResolvedValue(overview(team({ on: true, groups: [older], unlinked: ["Mo"] })));
    render(<LiveHeaderDropdown />);
    await waitFor(() => expect(trigger().querySelector(".live-trigger-dot")).toBeTruthy());
    fireEvent.click(trigger());
    expect(await screen.findByText(/^Can't reach Mo on this network/)).toBeTruthy();
    expect(screen.getByText(/^iliya needs to update UEFN Ducky to be live in every plugin\. Until then they're live in Kanban only/))
      .toBeTruthy();
  });

  it("says why a team copy can't go live", async () => {
    api.live_overview.mockResolvedValue(overview(team({ supported: false, reason: "This team service has no live editing." })));
    render(<LiveHeaderDropdown />);
    fireEvent.click(await waitFor(trigger));
    expect(await screen.findByText("This team service has no live editing.")).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Start live" })).toBeNull();
  });

  it("reads again when who is live moved, and says so in the activity list when Live ended by itself", async () => {
    api.live_overview.mockResolvedValue(overview(team({ on: true, groups: [{ ...ILIYA, mine: true }] })));
    render(<LiveHeaderDropdown />);
    await waitFor(() => expect(trigger().className).toContain("is-live"));
    api.live_overview.mockResolvedValue(overview(team()));
    push.handlers.forEach((fn) => fn({ type: "live_team", account: "acct", space: "s1", on: false, ended: "alone" }));
    await waitFor(() => expect(trigger().className).not.toContain("is-live"));
    const row = getBackgroundJobs().find((j) => j.id === "live-ended:s1");
    expect(row?.title).toBe("Live ended in Alpha");
    expect(row?.detail).toMatch(/^Everyone else left the group/);
  });

  it("an error joining shows in the menu", async () => {
    api.live_overview.mockResolvedValue(overview(team()));
    api.live_team_join.mockResolvedValue({ ok: false, error: "Couldn't go live: no link" });
    render(<LiveHeaderDropdown />);
    fireEvent.click(await waitFor(trigger));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Start live" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't go live: no link");
  });
});
