// @vitest-environment jsdom
import { expect, it, vi } from "vitest";

const reportOpen = vi.fn();
const claimTab = vi.fn();
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({ report_open_tabs: reportOpen, claim_tab: claimTab }),
}));
vi.mock("../hooks/useAgentEventBus", () => ({ subscribeAgentEvents: () => () => {} }));
vi.mock("../utils/visibleInterval", () => ({ setVisibleInterval: () => () => {} }));

it("the restored tabs at boot are reported once the page is idle, not during startup", async () => {
  vi.useFakeTimers();
  const idle: Array<() => void> = [];
  (window as Window & { requestIdleCallback?: unknown }).requestIdleCallback = (cb: () => void) => {
    idle.push(cb);
    return idle.length;
  };
  try {
    const { reportOpenTabs, BOOT_REPORT_DELAY_MS } = await import("./tabRegistryClient");
    reportOpenTabs(["chat:a", "chat:b"]);
    await vi.advanceTimersByTimeAsync(300);
    expect(reportOpen).not.toHaveBeenCalled(); // the old 300 ms debounce sent it mid-boot
    await vi.advanceTimersByTimeAsync(BOOT_REPORT_DELAY_MS);
    expect(reportOpen).not.toHaveBeenCalled(); // waits for an idle moment
    idle.shift()!();
    expect(reportOpen).toHaveBeenCalledTimes(1);
    expect(claimTab.mock.calls.map(([id]) => id)).toEqual(["chat:a", "chat:b"]);
    // Later changes keep the short debounce.
    reportOpenTabs(["chat:a"]);
    await vi.advanceTimersByTimeAsync(300);
    expect(reportOpen).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
  }
});

it("a newer report while the boot one waits for idle wins", async () => {
  vi.useFakeTimers();
  vi.resetModules();
  reportOpen.mockClear();
  const idle: Array<() => void> = [];
  (window as Window & { requestIdleCallback?: unknown }).requestIdleCallback = (cb: () => void) => {
    idle.push(cb);
    return idle.length;
  };
  try {
    const { reportOpenTabs, BOOT_REPORT_DELAY_MS } = await import("./tabRegistryClient");
    reportOpenTabs(["chat:old"]);
    await vi.advanceTimersByTimeAsync(BOOT_REPORT_DELAY_MS);
    reportOpenTabs(["chat:new"]);
    await vi.advanceTimersByTimeAsync(300);
    idle.shift()?.(); // the stale boot report must not send chat:old
    expect(reportOpen.mock.calls.map(([, ids]) => ids)).toEqual([["chat:new"]]);
  } finally {
    vi.useRealTimers();
  }
});
