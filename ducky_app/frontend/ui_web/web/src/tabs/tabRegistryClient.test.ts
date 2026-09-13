// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";

const focusTab = vi.fn();
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({
    focus_tab: focusTab,
    claim_tab: vi.fn(),
    report_open_tabs: vi.fn(),
  }),
}));
vi.mock("../hooks/useAgentEventBus", () => ({ subscribeAgentEvents: () => () => {} }));
vi.mock("../utils/visibleInterval", () => ({ setVisibleInterval: () => () => {} }));

const { openOrFocusTab, reportOpenTabs, reportOpenTabsNow } = await import("./tabRegistryClient");

beforeEach(() => {
  focusTab.mockReset();
  reportOpenTabsNow([]);
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
