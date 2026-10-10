// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentEvent, GroupMemberDto } from "../types/panel";
const api = vi.hoisted(() => ({ group_members: vi.fn() }));
const listeners = vi.hoisted(() => new Set<(event: AgentEvent) => void>());
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../hooks/useAgentEventBus", () => ({ subscribeAgentEvents: (fn: (event: AgentEvent) => void) => {
  listeners.add(fn); return () => listeners.delete(fn);
} }));
import { GroupMemberStrip } from "./GroupMemberStrip";
import { memberObservationLines } from "./groupMemberHover";

const rows = (): GroupMemberDto[] => ["Builder", "Designer"].map((name, index) => ({
  name, profile_id: "", member_conv_id: name.toLowerCase(),
  observation: { group_id: "group", project_slug: "stored-project", role: index ? "member" : "leader",
    runtime: index ? "idle" : "running", observed_at: Date.now() / 1000,
    assignment: { plan_id: "canonical", chat_id: "coord", node_id: "original", title: "Original task", status: "in_progress" },
  },
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const props = { groupId: "group", members: rows(), onMembersChange: () => {}, onOpenMember: () => {} };

describe("member observations", () => {
  it("renders scoped canonical task and runtime snapshots for two members", async () => {
    api.group_members.mockResolvedValue({ ok: true, members: rows() });
    render(<GroupMemberStrip {...props} />);
    fireEvent.focus(screen.getByTitle("Open Builder's work"));
    await screen.findByText("Runtime: running (snapshot; not task completion)");
    expect(screen.getByText("Assignment: Original task [in_progress] · plan canonical · node original")).toBeTruthy();
    expect(screen.getByText("Project: stored-project")).toBeTruthy();
    expect(screen.getByText("Group: group · Role: leader")).toBeTruthy();
    fireEvent.focus(screen.getByTitle("Open Designer's work"));
    await screen.findByText("Runtime: idle (snapshot; not task completion)");
    expect(screen.getByText("Group: group · Role: member")).toBeTruthy();
  });

  it("rejects late responses after changing group and failed refreshes", async () => {
    let finish!: (value: unknown) => void;
    api.group_members.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const view = render(<GroupMemberStrip {...props} />);
    api.group_members.mockResolvedValue({ ok: false });
    view.rerender(<GroupMemberStrip {...props} groupId="other" />);
    await waitFor(() => expect(api.group_members).toHaveBeenCalledWith("other"));
    await act(async () => finish({ ok: true, members: rows() }));
    fireEvent.focus(screen.getByTitle("Open Builder's work"));
    await screen.findByText("Runtime: unknown");
    expect(screen.queryByText(/plan canonical/)).toBeNull();
  });

  it("does not present old, future or wrong-group observations as active", () => {
    const member = rows()[0];
    const now = Date.now();
    member.observation!.observed_at = now / 1000 - 61;
    expect(memberObservationLines(member, "group", now)).toContain("Runtime: unknown (stale observation)");
    expect(memberObservationLines(member, "group", now).some(line => line.startsWith("Last observed assignment:"))).toBe(true);
    member.observation!.observed_at = now / 1000 + 61;
    expect(memberObservationLines(member, "group", now)).toContain("Runtime: unknown (stale observation)");
    expect(memberObservationLines(member, "other", now)).toEqual(["Runtime: unknown", "Assignment: unknown"]);
  });

  it("refreshes canonical-owner events but ignores unrelated projects and stale requests", async () => {
    api.group_members.mockResolvedValue({ ok: true, members: rows() });
    render(<GroupMemberStrip {...props} />);
    fireEvent.focus(screen.getByTitle("Open Builder's work"));
    await screen.findByText(/Assignment: Original task/);
    api.group_members.mockClear();
    act(() => { for (const fn of listeners) fn({ type: "plan_updated", conv_id: "unrelated" }); });
    expect(api.group_members).not.toHaveBeenCalled();
    let finish!: (value: unknown) => void;
    api.group_members.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    act(() => { for (const fn of listeners) fn({ type: "plan_updated", conv_id: "coord" }); });
    expect(api.group_members).toHaveBeenCalledWith("group");
    api.group_members.mockResolvedValue({ ok: false });
    fireEvent.click(screen.getByText("Refresh observation"));
    await screen.findByText("Runtime: unknown");
    await act(async () => finish({ ok: true, members: rows() }));
    expect(screen.queryByText(/plan canonical/)).toBeNull();
  });
});
