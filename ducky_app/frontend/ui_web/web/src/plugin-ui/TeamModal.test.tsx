// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import type { TeamOverview, TeamPluginRow } from "../types/panel";
import { _resetActiveTeamForTests, summarizeTeam } from "./activeTeamStore";
import { publishTeamScope, unpublishTeamScope, type TeamScope } from "./teamScopeStore";
import { TeamModal } from "./TeamModal";

const api = vi.hoisted(() => ({
  plugin_team_overview: vi.fn(),
  plugin_scope_choices: vi.fn(),
  plugin_scope_set_all: vi.fn(),
  plugin_scope_status: vi.fn(),
  plugin_scope_pull: vi.fn(),
  plugin_scope_history: vi.fn(),
  plugin_scope_resolve: vi.fn(),
  get_settings: vi.fn(async () => ({ team_send_delay_s: 10, team_poll_s: 60 })),
  save_agent_settings: vi.fn(async () => "Saved"),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));
vi.mock("../hooks/useLiveOverview", () => ({ readLiveOverview: vi.fn(async () => {}) }));
vi.mock("../hooks/usePanelPushBus", () => ({ installPanelPushBus: () => {}, subscribePanelPush: () => () => {} }));

afterEach(() => {
  act(() => unpublishTeamScope("forge"));
  cleanup();
  vi.clearAllMocks();
  _resetActiveTeamForTests();
});

const row = (extra: Partial<TeamPluginRow>): TeamPluginRow => ({
  pluginId: "kanban", label: "Kanban", icon: "", source: "store", incoming: 0, incomingBy: [], held: false,
  outgoing: [], pending: 0, conflicts: 0, blocked: [], state: "ok", error: "", items: 21, ...extra,
});
const ALPHA: TeamOverview = {
  ok: true, current: "s1", label: "Alpha Studio",
  plugins: [
    row({ incoming: 3, incomingBy: ["iliya"], blocked: [{ kind: "doc", key: "card.pip", name: "Pip", error: "Bad JSON" }] }),
    row({ pluginId: "ducky.automations", label: "Workflows", source: "workflows", items: 20 }),
  ],
};
const LOCAL: TeamOverview = { ok: true, current: "personal", label: "Local", plugins: [] };
const CHOICES = { ok: true, choices: [{ id: "personal", kind: "personal", label: "Local" }, { id: "s1", kind: "shared", label: "Alpha Studio" }] };
const STATUS = { ok: true, visible: true, canChange: true, state: "ok", pluginLabel: "Kanban",
  scope: { kind: "shared", label: "Alpha Studio", spaceId: "s1", readOnly: false } };

const modal = async (overview: TeamOverview = ALPHA) => {
  api.plugin_team_overview.mockResolvedValue(overview);
  api.plugin_scope_choices.mockResolvedValue(CHOICES);
  api.plugin_scope_status.mockResolvedValue(STATUS);
  render(<ConfirmModalProvider><TeamModal open onClose={() => {}} /></ConfirmModalProvider>);
  await screen.findByText(`${overview.current === "personal" ? "Local" : overview.label} · Team settings`);
};

const pane = (extra: Partial<TeamScope> = {}): TeamScope => ({
  pluginId: "forge", plugin: "Forge",
  status: { ok: true, visible: true, canChange: true, scope: { kind: "shared", label: "Alpha Studio", spaceId: "s1", readOnly: false } },
  phrase: { key: "updates", text: "2 updates from Ana", tone: "", action: "update" },
  syncing: false, busy: false, switching: false, shared: true, variant: "shared", kind: "team", current: "s1",
  menu: CHOICES.choices as TeamScope["menu"], offers: [], refusal: "", manual: false, canSync: true, failing: false,
  canPush: false, canPull: true, badge: 2, conflicts: 0, notes: [],
  actions: {
    pull: vi.fn(async () => true), push: vi.fn(), syncNow: vi.fn(), choose: vi.fn(), copyInto: vi.fn(), settle: vi.fn(),
    loadChoices: vi.fn(async () => {}), refresh: vi.fn(),
  },
  ...extra,
} as TeamScope);

describe("TeamModal", () => {
  it("is the team's, not a plugin's: titled by the team, with the team picker and summed counts", async () => {
    await modal();
    expect(screen.getByRole("button", { name: "Team: Alpha Studio" })).toBeTruthy();
    const incoming = screen.getByRole("button", { name: /^Incoming/ });
    const count = incoming.querySelector(".plugin-scope-count");
    expect(count?.textContent).toBe("4"); // 3 coming in + 1 that failed
    expect(count?.classList.contains("is-alert")).toBe(true);
    expect(screen.getByText("Alpha Studio: 1 coming in".replace("1", "3"))).toBeTruthy();
  });

  it("Incoming lists every plugin as a Ledger group; a header folds its lines open", async () => {
    await modal();
    fireEvent.click(screen.getByRole("button", { name: /^Incoming/ }));
    const list = screen.getByRole("list", { name: "Plugins on Alpha Studio" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText("Store plugin · 21 items · from iliya")).toBeTruthy();
    expect(within(list).getByText("20 workflows")).toBeTruthy();
    const head = within(list).getByRole("button", { name: "Kanban" });
    expect(head.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(head);
    expect(head.getAttribute("aria-expanded")).toBe("true");
    expect(within(list).getByText("Pip")).toBeTruthy();
    expect(within(list).getByText("Bad JSON")).toBeTruthy();
    fireEvent.click(within(list).getByRole("button", { name: "Get now" }));
    await waitFor(() => expect(api.plugin_scope_pull).toHaveBeenCalledWith("kanban"));
  });

  it("opening a plugin slides the list out and its own view in; Back slides home", async () => {
    await modal();
    fireEvent.click(screen.getByRole("button", { name: /^Incoming/ }));
    const slide = document.querySelector(".team-slide")!;
    expect(slide.classList.contains("is-detail")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Open Kanban" }));
    expect(slide.classList.contains("is-detail")).toBe(true);
    await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalledWith("kanban"));
    const region = await screen.findByRole("region", { name: "Source control: Alpha Studio" });
    expect(within(region).getByText(/^Teammates' changes on their way/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(slide.classList.contains("is-detail")).toBe(false);
    await waitFor(() => expect(document.activeElement?.getAttribute("aria-label")).toBe("Kanban"));
  });

  it("Your team switches every plugin after asking, and says it leaves the live group", async () => {
    api.plugin_scope_set_all.mockResolvedValue({ ok: true, switched: ["kanban"], pending: [] });
    await modal();
    fireEvent.click(await waitFor(() => {
      const t = screen.getByRole("button", { name: "Team: Alpha Studio" }) as HTMLButtonElement;
      expect(t.disabled).toBe(false);
      return t;
    }));
    fireEvent.click(screen.getByRole("option", { name: /^Local/ }));
    expect(await screen.findByText(/You leave Alpha Studio's live group now/)).toBeTruthy();
    expect(api.plugin_scope_set_all).not.toHaveBeenCalled();
    api.plugin_team_overview.mockResolvedValue(LOCAL);
    fireEvent.click(screen.getByRole("button", { name: /^Switch/ }));
    await waitFor(() => expect(api.plugin_scope_set_all).toHaveBeenCalledWith("personal"));
    expect(await screen.findByText("Local · Team settings")).toBeTruthy();
  });

  it("on a plugin tab, Overview adds that plugin's card; its status slides it in on that section", async () => {
    const p = pane();
    act(() => publishTeamScope(p));
    await modal();
    const card = screen.getByRole("group", { name: "Forge on this tab" });
    fireEvent.click(within(card).getByRole("button", { name: "Update now" }));
    expect(p.actions.pull).toHaveBeenCalledTimes(1);
    expect(within(card).getByRole("button", { name: "Whose data: Alpha Studio" })).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: /2 updates from Ana/ }));
    expect(screen.getByRole("button", { name: /^Incoming/ }).getAttribute("aria-current")).toBe("page");
    await waitFor(() => expect(document.querySelector(".team-slide")?.classList.contains("is-detail")).toBe(true));
  });

  it("every section is a Ledger: Conflicts, Locks, People and History fold open their own lines", async () => {
    api.plugin_scope_history.mockResolvedValue({ ok: true, entries: [{ seq: 44, by: "iliya", at: 1, count: 1, message: "Moved Pip" }] });
    await modal({ ...ALPHA, plugins: [row({
      conflicts: 1, head: 44,
      conflictList: [{ kind: "doc", key: "deck", name: "deck", by: "Ana", reason: "changed" }],
      locks: [{ unit: "doc:pip", name: "Pip", holder: "Ana", elsewhere: "", mine: false }],
      people: [{ name: "Ana", device: "Laptop", me: false, viewing: true, editing: ["Pip"], live: true },
        { name: "You", device: "MainPC", me: true, viewing: true, editing: [], live: false }],
    })] });
    const fold = (section: RegExp) => {
      fireEvent.click(screen.getByRole("button", { name: section }));
      const list = screen.getByRole("list", { name: "Plugins on Alpha Studio" });
      fireEvent.click(within(list).getByRole("button", { name: "Kanban" }));
      return list;
    };
    let list = fold(/^Conflicts/);
    expect(within(list).getByText("1 conflict")).toBeTruthy();
    expect(within(list).getByText("deck")).toBeTruthy();
    expect(within(list).getByRole("button", { name: "Resolve" })).toBeTruthy();
    list = fold(/^Locks/);
    expect(within(list).getByText("1 held by others")).toBeTruthy();
    expect(within(list).getByText("by Ana")).toBeTruthy();
    list = fold(/^People/);
    expect(within(list).getByText("1 here now")).toBeTruthy();
    expect(within(list).getByText("Laptop · editing Pip")).toBeTruthy();
    expect(within(list).queryByText(/MainPC/)).toBeNull();
    list = fold(/^History/);
    expect(within(list).getByText("#44")).toBeTruthy();
    expect(await within(list).findByText("Moved Pip")).toBeTruthy();
    expect(api.plugin_scope_history).toHaveBeenCalledWith("kanban", { limit: 5 });
  });

  it("Settings has the timing sliders: their values, and a move saves the new value once it settles", async () => {
    await modal();
    fireEvent.click(screen.getByRole("button", { name: /^Settings/ }));
    const send = await screen.findByRole("slider", { name: /^Send changes after/ });
    expect(screen.getByText("10 s")).toBeTruthy();
    expect(screen.getByText("1 min")).toBeTruthy();
    fireEvent.change(send, { target: { value: "20" } });
    fireEvent.change(send, { target: { value: "30" } });
    fireEvent.change(screen.getByRole("slider", { name: /^Check for teammates' changes every/ }), { target: { value: "90" } });
    await waitFor(() => expect(api.save_agent_settings).toHaveBeenCalledWith({ team_send_delay_s: 30, team_poll_s: 90 }));
    expect(api.save_agent_settings).toHaveBeenCalledTimes(1);
  });

  it("on Local only Overview and Settings are offered", async () => {
    await modal(LOCAL);
    expect(screen.queryByRole("button", { name: /^Conflicts/ })).toBeNull();
    expect(screen.getByRole("button", { name: /^Settings/ })).toBeTruthy();
    expect(screen.getByText("Not on a team")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Team: Local" })).toBeTruthy();
  });
});

describe("active team", () => {
  it("sums what needs looking at across plugins; a conflict or a failed round is red", () => {
    const t = summarizeTeam({ ...ALPHA, plugins: [...ALPHA.plugins, row({ pluginId: "x", pending: 2, conflicts: 1 })] });
    expect([t.incoming, t.pending, t.conflicts, t.blocked, t.badge]).toEqual([3, 2, 1, 1, 7]);
    expect([t.phrase, t.tone]).toEqual(["1 conflict", "error"]);
    expect(summarizeTeam(LOCAL)).toMatchObject({ shared: false, badge: 0, phrase: "Not on a team" });
    expect(summarizeTeam({ ...ALPHA, plugins: [row({})] }).phrase).toBe("Up to date");
  });
});
