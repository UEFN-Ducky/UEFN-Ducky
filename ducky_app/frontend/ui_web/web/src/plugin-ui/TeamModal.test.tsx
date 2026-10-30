// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { publishPaneScope, publishTeamScope, unpublishPaneScope, unpublishTeamScope, type TeamScope } from "./teamScopeStore";
import { TeamModal } from "./TeamModal";

const api = vi.hoisted(() => ({
  plugin_scope_resolve: vi.fn(),
  get_settings: vi.fn(async () => ({ team_send_delay_s: 10, team_poll_s: 60 })),
  save_agent_settings: vi.fn(async () => "Saved"),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));

afterEach(() => {
  act(() => {
    unpublishTeamScope("forge");
    unpublishPaneScope("forge");
    unpublishPaneScope("art");
  });
  cleanup();
});

const CONFLICTS = [
  { kind: "doc", key: "deck", name: "deck", by: "Ana", reason: "both" },
  { kind: "doc", key: "pack", name: "pack", by: "Ben", reason: "both" },
];

const scope = (extra: Partial<TeamScope> = {}): TeamScope => ({
  pluginId: "forge", plugin: "Forge",
  status: {
    ok: true, visible: true, canChange: true, conflicts: CONFLICTS,
    scope: { kind: "shared", label: "Alpha Studio", spaceId: "s1", readOnly: false },
  },
  phrase: { key: "update", text: "2 updates from Ana", tone: "", action: "update" },
  syncing: false, busy: false, switching: false, shared: true, variant: "shared", kind: "team", current: "s1",
  menu: [{ id: "personal", kind: "personal", label: "Local" }, { id: "s1", kind: "shared", label: "Alpha Studio" }],
  offers: [], refusal: "", manual: false, canSync: true, failing: false, canPush: false, canPull: true,
  badge: 2, conflicts: 2, notes: [],
  actions: {
    pull: vi.fn(async () => true), push: vi.fn(), syncNow: vi.fn(), choose: vi.fn(), copyInto: vi.fn(), settle: vi.fn(),
    loadChoices: vi.fn(async () => {}), refresh: vi.fn(),
  },
  ...extra,
} as TeamScope);

const modal = (s: TeamScope) => {
  act(() => publishTeamScope(s));
  return render(<ConfirmModalProvider><TeamModal open onClose={() => {}} /></ConfirmModalProvider>);
};

describe("TeamModal", () => {
  it("opens on Overview: status, Update now runs the pane's own pull, and the team picker", () => {
    const s = scope();
    modal(s);
    expect(screen.getByText("Alpha Studio · Forge")).toBeTruthy();
    expect(s.actions.loadChoices).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Update now" }));
    expect(s.actions.pull).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Whose data: Alpha Studio" }));
    expect(screen.getByRole("option", { name: /Alpha Studio/ }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("option", { name: /^Local/ }));
    expect(s.actions.choose).toHaveBeenCalledWith(expect.objectContaining({ id: "personal" }));
  });

  it("the left nav switches sections; Conflicts carries a red count and lists the files", () => {
    modal(scope());
    const conflicts = screen.getByRole("button", { name: /^Conflicts/ });
    const count = conflicts.querySelector(".plugin-scope-count");
    expect(count?.textContent).toBe("2");
    expect(count?.classList.contains("is-alert")).toBe(true);
    fireEvent.click(conflicts);
    expect(conflicts.getAttribute("aria-current")).toBe("page");
    const panel = screen.getByRole("region", { name: "Source control: Alpha Studio" });
    expect(within(panel).getByText("deck")).toBeTruthy();
    expect(within(panel).getByText("pack")).toBeTruthy();
    // No tab strip of its own inside the modal.
    expect(within(panel).queryByRole("tablist")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Overview/ }));
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("Settings has the timing sliders: their values, and a move saves the new value once it settles", async () => {
    modal(scope());
    fireEvent.click(screen.getByRole("button", { name: /^Settings/ }));
    const send = await screen.findByRole("slider", { name: /^Send changes after/ });
    expect(screen.getByText("10 s")).toBeTruthy();
    expect(screen.getByText("1 min")).toBeTruthy();
    fireEvent.change(send, { target: { value: "20" } });
    fireEvent.change(send, { target: { value: "30" } });
    fireEvent.change(screen.getByRole("slider", { name: /^Check for teammates' changes every/ }), { target: { value: "90" } });
    expect(screen.getByText("30 s")).toBeTruthy();
    expect(screen.getByText("1 min 30 s")).toBeTruthy();
    await waitFor(() => expect(api.save_agent_settings).toHaveBeenCalledWith({ team_send_delay_s: 30, team_poll_s: 90 }));
    expect(api.save_agent_settings).toHaveBeenCalledTimes(1);
  });

  it("on Local only Overview is offered, and notes with their buttons show there", () => {
    const run = vi.fn();
    modal(scope({
      shared: false, variant: "personal", current: "personal",
      status: { ok: true, visible: true, canChange: true, scope: { kind: "personal", label: "Local", spaceId: "", readOnly: false } },
      phrase: { key: "local", text: "Only on this PC", tone: "" },
      notes: [{ key: "error", tone: "error", text: "That didn't work.", actions: [{ label: "Dismiss", ghost: true, run }] }],
    }));
    expect(screen.queryByRole("button", { name: /^Conflicts/ })).toBeNull();
    expect(screen.getByRole("button", { name: /^Settings/ })).toBeTruthy();
    expect(screen.getByText("That didn't work.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(run).toHaveBeenCalled();
  });

  it("with the plugin pane hidden it keeps that pane's copy and the full nav", () => {
    const art = scope({ pluginId: "art", plugin: "AI Art Studio", shared: false, variant: "personal", current: "personal",
      status: { ok: true, visible: true, canChange: true, scope: { kind: "personal", label: "Local", spaceId: "", readOnly: false } } });
    act(() => {
      publishPaneScope(art);
      publishTeamScope(art);
      unpublishTeamScope("art");
    });
    render(<ConfirmModalProvider><TeamModal open onClose={() => {}} /></ConfirmModalProvider>);
    expect(screen.getByText("Local · AI Art Studio")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Overview/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Whose data: Local" })).toBeTruthy();
  });

  it("with no shareable plugin open the nav still has Overview and Settings with the sliders", async () => {
    render(<ConfirmModalProvider><TeamModal open onClose={() => {}} /></ConfirmModalProvider>);
    expect(screen.getByText(/Open a plugin that shares/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Settings/ }));
    expect(await screen.findByRole("slider", { name: /^Send changes after/ })).toBeTruthy();
  });
});
