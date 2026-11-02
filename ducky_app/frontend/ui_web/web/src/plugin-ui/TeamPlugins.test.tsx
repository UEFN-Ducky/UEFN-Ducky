// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import type { TeamPluginRow } from "../types/panel";
import { summarizeTeam } from "./activeTeamStore";
import { TeamPlugins } from "./TeamPlugins";

const api = vi.hoisted(() => ({ plugin_scope_sync: vi.fn(), plugin_team_overview: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));
vi.mock("../hooks/usePanelPushBus", () => ({ installPanelPushBus: () => {}, subscribePanelPush: () => () => {} }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const row = (id: string, label: string, extra: Partial<TeamPluginRow> = {}): TeamPluginRow => ({
  pluginId: id, label, icon: "", source: "store", incoming: 0, incomingBy: [], held: false,
  outgoing: [], pending: 0, conflicts: 0, blocked: [], state: "ok", error: "", items: 1, ...extra,
});
const out = (n: number) => Array.from({ length: n }, (_, i) => ({
  kind: "doc", key: `card.${i}`, name: `card.${i}`, size: 10, deleted: false, state: i ? "queued" : "sending", reason: "", attempts: 0,
}));

const show = (rows: TeamPluginRow[]) => render(
  <ConfirmModalProvider>
    <TeamPlugins team={summarizeTeam({ current: "s1", label: "Mindful Path", plugins: rows })} section="changes" onSection={() => {}} />
  </ConfirmModalProvider>,
);

describe("the Team modal's Changes", () => {
  it("a busy server (too many requests) is amber on the plugin that waits, up to date on the rest", () => {
    show([
      row("kanban", "Kanban", { state: "retry", error: "busy, retry in 40 s" }),
      row("island-media-kit", "Island Media Kit", { state: "retry", error: "busy, retry in 40 s", pending: 53, outgoing: out(53) }),
    ]);
    expect(screen.getByText("Up to date")).toBeTruthy();
    const busy = screen.getByText("Server busy · retrying");
    expect(busy.className).toContain("team-ledger__badge--warn");
    expect(screen.queryByText("Couldn't sync")).toBeNull();
  });

  it("folded open: every queued item up to 50 with its state, the reason, and Retry now", async () => {
    show([row("island-media-kit", "Island Media Kit", { state: "retry", error: "busy, retry in 40 s", pending: 53, outgoing: out(53) })]);
    fireEvent.click(screen.getByRole("button", { name: "Island Media Kit" }));
    expect(screen.getByText("card.0")).toBeTruthy();
    expect(screen.getByText("card.49")).toBeTruthy();
    expect(screen.queryByText("card.50")).toBeNull();
    expect(screen.getByText("+3 more")).toBeTruthy();
    expect(screen.getByText("10 B · Sending")).toBeTruthy();
    expect(screen.getByText("busy, retry in 40 s")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
    await waitFor(() => expect(api.plugin_scope_sync).toHaveBeenCalledWith("island-media-kit", true));
  });

  it("a real error stays red", () => {
    show([row("kanban", "Kanban", { state: "error", error: "HTTP 502" })]);
    expect(screen.getByText("Couldn't sync").className).toContain("team-ledger__badge--error");
  });
});
