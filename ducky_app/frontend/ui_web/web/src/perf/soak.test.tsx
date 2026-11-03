// @vitest-environment jsdom
/**
 * Soak: one simulated hour of the app shell pieces that run timers and animation frames
 * (a plugin tab, a long chat, the team store) against a fake bridge that counts calls.
 * Fails on per-frame bridge loops (budget: 600 calls/h per method) and on listeners or
 * timers that keep piling up between minute 10 and minute 60.
 */
import { act } from "react";
import ReactDOM from "react-dom/client";
import { afterAll, beforeAll, expect, it, vi } from "vitest";

const PLUGIN = "soakplug";
const TAB = `plugin:${PLUGIN}:main`;

vi.mock("../hooks/usePluginContributions", async (importOriginal) => {
  const real = await importOriginal<typeof import("../hooks/usePluginContributions")>();
  const contrib = new Proxy(
    {
      ready: true,
      ui_panels: [{ plugin_id: "soakplug", id: "main", entry: "index.html", version: "1", title: "Soak" }],
    } as Record<string, unknown>,
    { get: (t, k) => (k in t ? t[k as string] : []) },
  );
  return { ...real, usePluginContributions: () => contrib };
});
vi.mock("../plugin-ui/usePluginPage", () => ({
  usePluginPage: () => ({ src: "about:blank", linked: false, error: "" }),
}));

import { PluginWebviewPane } from "../plugin-ui/PluginWebviewPane";
import { useActiveTeam, _resetActiveTeamForTests } from "../plugin-ui/activeTeamStore";
import { VirtualChatMessageList } from "../components/VirtualChatMessageList";
import { buildChatRows } from "../utils/chatMessageGroups";
import { syntheticMessages } from "./syntheticChat";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";

const CALLS_PER_HOUR = 600;
const FRAME_MS = 16;
const MINUTE = 60_000;

/** Bridge calls per method, like the perf log's ui_js rows. */
const calls = new Map<string, number>();
const reply = (method: string): unknown => {
  switch (method) {
    case "get_listener_status": return { ok: true };
    // A plugin tab that never opened a browser pane (the 81439a9c flood). SOAK_BOUNDS_ERROR=other
    // takes the pre-fix path (any failure clears the dedupe): the soak must fail then.
    case "browser_pane_set_bounds": return { ok: false, error: process.env.SOAK_BOUNDS_ERROR || "pane not open" };
    case "browser_pane_list": return { ok: true, panes: [] };
    case "plugin_team_overview": return { ok: true, current: "personal", label: "Local", plugins: [] };
    default: return null;
  }
};
const fakeApi = new Proxy({}, {
  get: (_t, key) => {
    if (typeof key !== "string" || key === "then") return undefined;
    return async () => {
      calls.set(key, (calls.get(key) ?? 0) + 1);
      return reply(key);
    };
  },
});

/** Net window/document listeners added and not removed. */
let liveListeners = 0;
function countListeners(target: EventTarget): void {
  const add = target.addEventListener.bind(target);
  const remove = target.removeEventListener.bind(target);
  target.addEventListener = ((...a: Parameters<EventTarget["addEventListener"]>) => {
    liveListeners++;
    add(...a);
  }) as EventTarget["addEventListener"];
  target.removeEventListener = ((...a: Parameters<EventTarget["removeEventListener"]>) => {
    liveListeners--;
    remove(...a);
  }) as EventTarget["removeEventListener"];
}

const noop = () => {};
const EMPTY: never[] = [];

function TeamProbe() {
  const team = useActiveTeam();
  return <span data-testid="team">{team?.phrase ?? ""}</span>;
}

function Shell() {
  const rows = buildChatRows({ committed: syntheticMessages(40), turnMessages: [], streamBuffer: "", inFlight: false });
  return (
    <ConfirmModalProvider>
      <TeamProbe />
      <PluginWebviewPane tabId={TAB} visible />
      <div className="soak-chat">
        <VirtualChatMessageList
          rows={rows}
          showActivityPanel={false}
          activityHeaderOnly={false}
          isWaitingOnLinked={false}
          waitingLinked={EMPTY}
          isAtBottom
          hasNewBelow={false}
          editableRowId={null}
          composerMode="agent"
          composerModel="soak"
          setComposerModel={noop}
          composerCodingAgent="ducky"
          setComposerCodingAgent={noop}
          convId="soak"
          onResend={noop}
          onAtBottomChange={noop}
          onJumpToLatest={noop}
          onOpenChat={noop}
          onStopLinked={noop}
          allChats={EMPTY}
          linkedAgents={EMPTY}
          activityLines={EMPTY}
          chatPlan={null}
          chatPlanProgress={null}
          planAllDone={false}
        />
      </div>
    </ConfirmModalProvider>
  );
}

let root: ReactDOM.Root | null = null;
let host: HTMLDivElement;

beforeAll(() => {
  vi.useFakeTimers({
    toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval",
      "requestAnimationFrame", "cancelAnimationFrame", "Date"],
  });
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  (window as unknown as { pywebview: unknown }).pywebview = { api: fakeApi };
  // jsdom lays nothing out: give the plugin frame a real size so the pane pins every frame.
  const rect = { x: 0, y: 40, width: 900, height: 600 };
  HTMLIFrameElement.prototype.getBoundingClientRect = () =>
    ({ ...rect, top: rect.y, left: rect.x, right: rect.width, bottom: rect.y + rect.height, toJSON: () => ({}) }) as DOMRect;
  Object.defineProperty(HTMLIFrameElement.prototype, "clientWidth", { configurable: true, get: () => rect.width });
  Object.defineProperty(HTMLIFrameElement.prototype, "clientHeight", { configurable: true, get: () => rect.height });
  document.elementFromPoint = () => document.querySelector("iframe");
  if (!("ResizeObserver" in window)) {
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  countListeners(window);
  countListeners(document);
  host = document.createElement("div");
  document.body.appendChild(host);
});

afterAll(() => {
  act(() => root?.unmount());
  _resetActiveTeamForTests();
  vi.useRealTimers();
});

async function advance(ms: number): Promise<void> {
  // One frame at a time so bridge replies (promises) settle between frames, like the real app.
  for (let t = 0; t < ms; t += 1000) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(Math.min(1000, ms - t));
    });
  }
}

it("one simulated hour stays inside the call budget and does not leak", { timeout: 300_000 }, async () => {
  await act(async () => {
    root = ReactDOM.createRoot(host);
    root.render(<Shell />);
  });
  expect(host.querySelector("iframe")).not.toBeNull();

  await advance(10 * MINUTE);
  const at10 = { listeners: liveListeners, timers: vi.getTimerCount() };
  await advance(50 * MINUTE);
  const at60 = { listeners: liveListeners, timers: vi.getTimerCount() };

  const table = [...calls.entries()].sort((a, b) => b[1] - a[1]);
  console.info(`soak 60 min @ ${FRAME_MS} ms frames, bridge calls per method:\n`
    + table.map(([m, n]) => `  ${m.padEnd(32)} ${n}`).join("\n")
    + `\n  listeners ${at10.listeners} -> ${at60.listeners}, timers ${at10.timers} -> ${at60.timers}`);

  const over = table.filter(([, n]) => n >= CALLS_PER_HOUR).map(([m, n]) => `${m}: ${n} calls/h`);
  expect(over).toEqual([]);
  expect(calls.get("browser_pane_set_bounds") ?? 0).toBeGreaterThan(0); // the frame loop really ran
  expect(at60.listeners).toBeLessThanOrEqual(at10.listeners);
  expect(at60.timers).toBeLessThanOrEqual(at10.timers);
});
