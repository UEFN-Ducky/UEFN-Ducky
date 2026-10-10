// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AppHeaderActionsProvider, useAppHeaderActions } from "../contexts/AppHeaderActionsContext";
import type { EditorLayoutState, EditorTab } from "../types/panel";

const api = vi.hoisted(() => ({ terminal_busy: vi.fn(), terminal_busy_many: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../contexts/TerminalsSettingsContext", () => ({ useTerminalsSettings: () => ({ enabled: true }) }));

import { TerminalHeaderBridge } from "../components/TerminalHeaderBridge";
import { useTerminalBusyStatuses } from "./useTerminalBusyStatuses";

let runningIds = new Set(["s2"]);
const running = (id: string) => runningIds.has(id);
const terminal = (n: number): EditorTab => ({ id: `t${n}`, kind: "terminal", name: `T${n}`, terminalSessionId: `s${n}` });
const open = [terminal(1), terminal(2), terminal(3)];
const parked = [terminal(4)];
const layout: EditorLayoutState = {
  root: { type: "group", groupId: "main" },
  groups: { main: { id: "main", tabIds: open.map((t) => t.id), activeTabId: "t1" } },
  focusedGroupId: "main",
};

let headerRenders = 0;
let stripRenders = 0;

function Header() {
  const { terminal: menu } = useAppHeaderActions();
  headerRenders += 1;
  return <span data-testid="running">{menu?.terminals.filter((t) => t.running).map((t) => t.sessionId).join(",")}</span>;
}

/** What each editor group's tab strip does with its terminal tabs. */
function TabStrip({ ids }: { ids: string[] }) {
  useTerminalBusyStatuses(ids);
  stripRenders += 1;
  return null;
}

const noop = () => {};

function App() {
  return (
    <AppHeaderActionsProvider>
      <Header />
      <TerminalHeaderBridge projectPath="C:/Project" openTabs={open} parkedTabs={parked} layout={layout}
        defaultTerminalShell="bash" setDefaultTerminalShell={noop} onNewTerminal={noop} onCloseTerminal={noop}
        activateTabInGroup={noop} setFocusedGroup={noop} />
      <TabStrip ids={["s1", "s2", "s3"]} />
    </AppHeaderActionsProvider>
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  runningIds = new Set(["s2"]);
  headerRenders = 0;
  stripRenders = 0;
  api.terminal_busy.mockImplementation(async (id: string) => ({ ok: true, busy: false, running: running(id) }));
  api.terminal_busy_many.mockImplementation(async (ids: string[]) => ({
    ok: true, states: Object.fromEntries(ids.map((id) => [id, { ok: true, busy: false, running: running(id) }])),
  }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.resetAllMocks();
});

const calls = () => api.terminal_busy.mock.calls.length + api.terminal_busy_many.mock.calls.length;
const askedFor = (id: string) =>
  api.terminal_busy.mock.calls.filter(([sid]) => sid === id).length
  + api.terminal_busy_many.mock.calls.filter(([ids]) => (ids as string[]).includes(id)).length;

it("asks once per tick for all terminals and re-renders nothing while nothing changes", async () => {
  const view = render(<App />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(view.getByTestId("running").textContent).toBe("s2");
  const settled = calls();
  expect(settled).toBeLessThanOrEqual(2);
  const header = headerRenders;
  const strip = stripRenders;

  for (let i = 0; i < 10; i += 1) await act(async () => { await vi.advanceTimersByTimeAsync(2000); });

  // Two pollers used to ask for every session one by one: 7 bridge calls each tick.
  expect(calls() - settled).toBeLessThanOrEqual(10);
  // The parked terminal is only a dot in the header list: every 10 s.
  expect(askedFor("s4") - 1).toBeLessThanOrEqual(2);
  expect(askedFor("s1") - 1).toBe(10);
  expect(headerRenders).toBe(header);
  expect(stripRenders).toBe(strip);
});

it("shows a change on the next tick", async () => {
  const view = render(<App />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  runningIds = new Set(["s3"]);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(view.getByTestId("running").textContent).toBe("s3");
});
