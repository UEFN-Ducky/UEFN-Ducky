// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatTab, GroupMemberDto } from "../types/panel";
import { fmtCompactTokens } from "../utils/contextFormat";

const api = vi.hoisted(() => ({
  group_members: vi.fn(),
  group_set_member_lane: vi.fn(),
  group_check_lane: vi.fn(async () => ({ ok: true, errors: [], warnings: [], normalized: null })),
  group_remove: vi.fn(),
}));
const state = vi.hoisted(() => ({ running: new Set<string>(), waiting: new Set<string>() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../hooks/useAgentEventBus", () => ({ subscribeAgentEvents: () => () => {} }));
vi.mock("../hooks/useRunningAgents", () => ({ useRunningAgents: () => state.running }));
vi.mock("../hooks/waitingChats", () => ({ useWaitingChatIds: () => state.waiting }));

const { GroupMemberStrip } = await import("./GroupMemberStrip");
const { resetRosterStore } = await import("./team/rosterStore");

const member = (id: string, extra: Partial<GroupMemberDto> = {}): GroupMemberDto => ({
  member_conv_id: id, profile_id: "", name: id, ...extra,
});
const top: GroupMemberDto[] = [member("Lead"), member("Crew", { is_group: true })];
const crew: GroupMemberDto[] = [member("Builder", { write_allowed: null }), member("Reviewer", { write_allowed: [] })];
const chats: ChatTab[] = [
  { id: "Lead", name: "Lead", contextTokens: 1000 },
  { id: "Builder", name: "Builder", contextTokens: 2000 },
  { id: "Reviewer", name: "Reviewer", contextTokens: 3000 },
];

function setup(onMembersChange = vi.fn()) {
  api.group_members.mockImplementation(async (id: string) => ({ ok: true, members: id === "Crew" ? crew : top }));
  render(
    <GroupMemberStrip groupId="team" members={top} allChats={chats} onMembersChange={onMembersChange} onOpenMember={vi.fn()} />,
  );
  return onMembersChange;
}
const openTeam = () => fireEvent.click(screen.getByRole("button", { name: /^Team/ }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
  state.running = new Set();
  state.waiting = new Set();
  resetRosterStore();
});

describe("team bar and roster", () => {
  it("sums up everyone on one line: agents in nested groups, who runs and who waits", async () => {
    state.running = new Set(["Builder"]);
    state.waiting = new Set(["Reviewer"]);
    setup();
    await screen.findByText("3 agents · 1 group");
    // No avatar row on the bar: the roster lists everyone.
    expect(screen.queryByRole("button", { name: "Open Reviewer" })).toBeNull();
    expect(screen.getByText("1 running")).toBeTruthy();
    expect(screen.getByText("1 waiting")).toBeTruthy();
    // The roster stays closed until asked for, and remembers being opened.
    expect(screen.queryByRole("table", { name: "Team" })).toBeNull();
    openTeam();
    const table = screen.getByRole("table", { name: "Team" });
    expect(within(table).getByText("Builder")).toBeTruthy();
    expect(within(table).getByText("Running")).toBeTruthy();
    expect(within(table).getByText("Waiting")).toBeTruthy();
    expect(within(table).getByText(fmtCompactTokens(5000))).toBeTruthy(); // Crew's total context
    cleanup();
    setup();
    expect(screen.getByRole("table", { name: "Team" })).toBeTruthy();
  });

  it("changes a nested agent's write lane in its own group, not the top one", async () => {
    const onMembersChange = setup();
    openTeam();
    const builderRow = (await screen.findByTitle("Open Builder's work")).closest<HTMLElement>("[role=row]")!;
    fireEvent.click(within(builderRow).getByRole("button", { name: "Any file" }));
    fireEvent.change(screen.getByLabelText("Write lane for Builder"), { target: { value: "Content/Verse/**" } });
    api.group_set_member_lane.mockResolvedValue({ ok: true, group_members: [{ ...crew[0], write_allowed: ["Content/Verse/**"] }, crew[1]] });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 300));
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.group_set_member_lane).toHaveBeenCalledWith("Crew", "Builder", ["Content/Verse/**"], false));
    // The lane cell: the glob, then what the mode does with a write outside it.
    await screen.findByRole("button", { name: /^Verse\/\s*Blocks$/ });
    expect(onMembersChange).not.toHaveBeenCalled();
  });

  it("removes a nested agent from its own group", async () => {
    setup();
    openTeam();
    api.group_remove.mockResolvedValue({ ok: true, group_members: [crew[0]] });
    fireEvent.click(await screen.findByRole("button", { name: "Remove Reviewer" }));
    await waitFor(() => expect(api.group_remove).toHaveBeenCalledWith("Crew", "Reviewer"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Remove Reviewer" })).toBeNull());
  });

  it("folds and unfolds nested groups", async () => {
    setup();
    openTeam();
    await screen.findByText("Reviewer", { selector: ".team-roster-name" });
    fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.queryByText("Reviewer", { selector: ".team-roster-name" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show Crew's agents" }));
    expect(screen.getByText("Reviewer", { selector: ".team-roster-name" })).toBeTruthy();
  });
});
