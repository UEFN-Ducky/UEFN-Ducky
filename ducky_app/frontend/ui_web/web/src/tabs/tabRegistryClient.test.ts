// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";

const focusTab = vi.fn();
const getTabOwner = vi.fn();
const reportOpen = vi.fn();
const claimTab = vi.fn();
let onEvent: (event: { type: string; tab_id: string; window_id: string }) => void;
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({
    focus_tab: focusTab,
    get_tab_owner: getTabOwner,
    claim_tab: claimTab,
    report_open_tabs: reportOpen,
  }),
}));
vi.mock("../hooks/useAgentEventBus", () => ({
  subscribeAgentEvents: (fn: typeof onEvent) => { onEvent = fn; return () => {}; },
}));
vi.mock("../utils/visibleInterval", () => ({ setVisibleInterval: () => () => {} }));

const { openInBackground, openOrFocusTab, reportOpenTabs, reportOpenTabsNow, installTabRegistryListener } = await import("./tabRegistryClient");

beforeEach(() => {
  focusTab.mockReset();
  getTabOwner.mockReset();
  reportOpenTabsNow([]);
});

it("ignores delayed claims from a closed focus window and honors current transfers", async () => {
  const close = vi.fn();
  installTabRegistryListener(() => true, close, vi.fn());
  getTabOwner.mockResolvedValue("focus-new");
  onEvent({ type: "tab_claimed", tab_id: "chat:reopened", window_id: "focus-old" });
  await vi.waitFor(() => expect(getTabOwner).toHaveBeenCalled());
  expect(close).not.toHaveBeenCalled();
  onEvent({ type: "tab_claimed", tab_id: "chat:reopened", window_id: "focus-new" });
  await vi.waitFor(() => expect(close).toHaveBeenCalledWith("chat:reopened", "focus-new"));
});

it("activates a tab that is already open here synchronously, without asking the registry", () => {
  reportOpenTabs(["chat:a"]);
  const open = vi.fn();
  void openOrFocusTab("chat:a", open);
  expect(open).toHaveBeenCalledOnce();
  expect(focusTab).not.toHaveBeenCalled();
});

it("applies rapid opens in click order even when registry replies land out of order", async () => {
  const replies: Array<(v: { ok: boolean }) => void> = [];
  focusTab.mockImplementation(() => new Promise((resolve) => replies.push(resolve)));
  const order: string[] = [];
  const first = openOrFocusTab("chat:a", () => order.push("a"));
  const second = openOrFocusTab("chat:b", () => order.push("b"));
  // FIFO: b's registry lookup only starts once a has resolved.
  await vi.waitFor(() => expect(focusTab).toHaveBeenCalledTimes(1));
  replies[0]({ ok: false });
  await first;
  await vi.waitFor(() => expect(focusTab).toHaveBeenCalledTimes(2));
  replies[1]({ ok: false });
  await second;
  expect(order).toEqual(["a", "b"]);
});

it("keeps opening after a registry failure or a tab owned elsewhere", async () => {
  focusTab.mockRejectedValueOnce(new Error("bridge down")).mockResolvedValueOnce({ ok: true, window_id: "focus-1" });
  const open = vi.fn();
  await openOrFocusTab("file:x", open);
  expect(open).toHaveBeenCalledOnce();
  const elsewhere = vi.fn();
  await openOrFocusTab("file:y", elsewhere);
  expect(elsewhere).not.toHaveBeenCalled();
});

it("reports and claims only when the tab list changes", async () => {
  vi.useFakeTimers();
  try {
    reportOpenTabsNow(["chat:a"]);
    reportOpen.mockClear();
    claimTab.mockClear();
    // Re-renders with the same tabs: nothing sent, nothing claimed.
    for (let i = 0; i < 5; i++) {
      reportOpenTabs(["chat:a"]);
      await vi.advanceTimersByTimeAsync(300);
    }
    reportOpenTabsNow(["chat:a"]);
    expect(reportOpen).not.toHaveBeenCalled();
    expect(claimTab).not.toHaveBeenCalled();
    // A new tab: one report, one claim for the new tab only.
    reportOpenTabs(["chat:a", "chat:b"]);
    await vi.advanceTimersByTimeAsync(300);
    expect(reportOpen).toHaveBeenCalledTimes(1);
    expect(claimTab.mock.calls.map(([id]) => id)).toEqual(["chat:b"]);
  } finally {
    vi.useRealTimers();
  }
});

it("opens locally when the registry lookup stalls instead of wedging later opens", async () => {
  vi.useFakeTimers();
  try {
    focusTab.mockImplementation(() => new Promise(() => {}));
    const open = vi.fn();
    const pending = openOrFocusTab("chat:stalled", open);
    await vi.advanceTimersByTimeAsync(2000);
    await pending;
    expect(open).toHaveBeenCalledOnce();
  } finally {
    vi.useRealTimers();
  }
});

it("an agent's background open never focuses anything and skips a tab open in any window", async () => {
  const open = vi.fn();
  reportOpenTabsNow(["plugin:browser:browser"]);
  await openInBackground("plugin:browser:browser", open);
  expect(open).not.toHaveBeenCalled();
  reportOpenTabsNow([]);
  getTabOwner.mockResolvedValue("focus-1");
  await openInBackground("plugin:browser:browser", open);
  expect(open).not.toHaveBeenCalled();
  getTabOwner.mockResolvedValue("");
  await openInBackground("plugin:browser:browser", open);
  expect(open).toHaveBeenCalledTimes(1);
  expect(focusTab).not.toHaveBeenCalled();
});
