// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { McpDiagnosticsDto } from "../../types/panel";
import { McpDiagnosticsView, ToolInspector } from "./McpToolCatalogView";
import { McpPluginsSection } from "./McpPluginsSection";

const mocks = vi.hoisted(() => ({ diagnostics: vi.fn(), list: vi.fn(), tools: vi.fn() }));
vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => ({ get_mcp_diagnostics: mocks.diagnostics, list_mcp_plugins: mocks.list }) }));
vi.mock("../../hooks/onApiReady", () => ({ onApiReady: (fn: () => void) => { fn(); return () => {}; } }));
vi.mock("../../hooks/bridgeJobAsync", () => ({ runBridgeJob: (...args: unknown[]) => mocks.tools(...args) }));
vi.mock("../../contexts/ConfirmModalContext", () => ({ useConfirmModal: () => ({ confirm: vi.fn() }) }));
vi.mock("./McpConnectionEditor", () => ({ McpConnectionEditor: () => null }));

import sharedReport from "./McpDiagnostics.fixture.json";
const report = sharedReport as McpDiagnosticsDto;

beforeEach(() => {
  vi.clearAllMocks();
  mocks.diagnostics.mockResolvedValue(report);
  mocks.list.mockResolvedValue({ plugins: [{ id: "docs", label: "Docs", enabled: true, kind: "custom", setup_steps: [] }] });
  mocks.tools.mockResolvedValue({ tools: [], categories: [], total: 0 });
});
afterEach(cleanup);

it("renders the shared report without turning cached inventory into runtime exposure", async () => {
  render(<McpDiagnosticsView serverId="docs" />);
  expect(await screen.findByText(report.correlation_id)).toBeTruthy();
  expect(screen.getByText(/Catalog tools: 2 · Policy-blocked: 1 · Unexposed by local filter: 0/)).toBeTruthy();
  expect(screen.getByText("Started: Unknown · Connected: Unknown · Model-exposed: Unknown")).toBeTruthy();
  expect(screen.getByText("Recent error: Inventory refresh failed")).toBeTruthy();
  expect(screen.getByText(report.scope)).toBeTruthy();
  expect(screen.getByText(report.stage_note)).toBeTruthy();
  expect(screen.getByText(report.policy_guidance)).toBeTruthy();
  expect(screen.getByText(report.servers[0].guidance)).toBeTruthy();
  expect(screen.getByText(/blocked in Ask mode: tool is not a verified read operation/)).toBeTruthy();
  expect(mocks.diagnostics).toHaveBeenCalledWith("docs", "agent");
  fireEvent.change(screen.getByLabelText("Policy preview mode"), { target: { value: "ask" } });
  await waitFor(() => expect(mocks.diagnostics).toHaveBeenLastCalledWith("docs", "ask"));
  fireEvent.click(screen.getByText("Refresh observations"));
  await waitFor(() => expect(mocks.diagnostics).toHaveBeenCalledTimes(3));
  expect(mocks.tools).not.toHaveBeenCalled();
});

it("shows failed diagnostics as unknown without exposing raw exceptions", async () => {
  mocks.diagnostics.mockRejectedValue(new Error("Bearer SECRET C:/private/config"));
  render(<McpDiagnosticsView serverId="docs" />);
  expect(await screen.findByText("Diagnostics unavailable. Runtime status is unknown.")).toBeTruthy();
  expect(document.body.textContent).not.toContain("SECRET");
  expect(document.body.textContent).not.toContain("private");
});

it("discards stale responses when selection changes", async () => {
  let finish!: (value: McpDiagnosticsDto) => void;
  mocks.diagnostics.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = render(<McpDiagnosticsView serverId="old" />);
  view.rerender(<McpDiagnosticsView serverId="docs" />);
  await screen.findByText(report.correlation_id);
  await act(async () => { finish({ ...report, correlation_id: "stale" }); });
  expect(screen.queryByText("stale")).toBeNull();
});

it.each(["missing", "disabled", "unknown"] as const)("renders %s and unknown counts explicitly", async (status) => {
  mocks.diagnostics.mockResolvedValue({ ...report, servers: [{ ...report.servers[0], status,
    counts: { catalog: null, policy_blocked: null, unexposed_by_filter: null }, tools: [], recent_error: "unobserved" }] });
  render(<McpDiagnosticsView serverId="docs" />);
  await screen.findByText(report.correlation_id);
  expect(screen.getByText(`— ${status}`, { exact: false })).toBeTruthy();
  expect(screen.getByText(/Catalog tools: Unknown/)).toBeTruthy();
  expect(screen.getByText("Recent error: Unobserved")).toBeTruthy();
});

it("mounts diagnostics through the actual settings server selection, including disabled servers", async () => {
  mocks.list.mockResolvedValue({ plugins: [{ id: "docs", label: "Docs", enabled: false, kind: "custom", setup_steps: [] }] });
  render(<McpPluginsSection />);
  fireEvent.click(await screen.findByText("Docs"));
  const section = await screen.findByRole("region", { name: "MCP diagnostics" });
  await within(section).findByText(report.correlation_id);
  expect(mocks.diagnostics).toHaveBeenCalledWith("docs", "agent");
  expect(mocks.tools).not.toHaveBeenCalled();
});

it("catalog eligibility badges no longer claim observed model exposure", () => {
  render(<ToolInspector tool={{ name: "ducky_get_tools", description: "", parameters: [], in_agent: true, in_plan: false } as never} />);
  expect(screen.getByText("Agent").title).toContain("does not prove runtime model exposure");
});

it("shows explicit filter exclusion without claiming model exposure", async () => {
  mocks.diagnostics.mockResolvedValue({ ...report, servers: [{ ...report.servers[0],
    counts: { catalog: 1, policy_blocked: 0, unexposed_by_filter: 1 },
    tools: [{ name: "docs__read_page", status: "unexposed", mode_reason: "" }] }] });
  render(<McpDiagnosticsView serverId="docs" />);
  await screen.findByText(report.correlation_id);
  expect(screen.getByText(/Unexposed by local filter: 1/)).toBeTruthy();
  expect(screen.getByText(": unexposed", { exact: false })).toBeTruthy();
  expect(screen.getByText(/Model-exposed: Unknown/)).toBeTruthy();
});

it("displays observed local session health with unobserved model exposure", async () => {
  mocks.diagnostics.mockResolvedValue({ ...report, servers: [{ ...report.servers[0], status: "connected",
    stages: { started: null, connected: true, model_exposed: null } }] });
  render(<McpDiagnosticsView serverId="docs" />);
  await screen.findByText(report.correlation_id);
  expect(screen.getByText("Started: Unknown · Connected: Yes · Model-exposed: Unknown")).toBeTruthy();
});
