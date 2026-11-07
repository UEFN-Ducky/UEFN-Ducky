// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ browser_pane_set_bounds: vi.fn(async () => ({ ok: true })) }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));

import { beginBrowserPaneMount, paneCoverer, pushBrowserPaneBounds } from "./bridge";

const TAB = "plugin:browser:main";
/** The long fake-timer loops below get room to finish on a busy PC (their checks are counts). */
const LOOP_TIMEOUT_MS = 60_000;
let rect = { x: 10, y: 20, width: 800, height: 600 };

function frame(): HTMLIFrameElement {
  const el = document.createElement("iframe");
  document.body.appendChild(el);
  el.getBoundingClientRect = () => ({ ...rect, top: rect.y, left: rect.x, right: rect.x + rect.width, bottom: rect.y + rect.height, toJSON: () => ({}) }) as DOMRect;
  Object.defineProperty(el, "clientWidth", { get: () => rect.width });
  Object.defineProperty(el, "clientHeight", { get: () => rect.height });
  return el;
}

beforeEach(() => {
  vi.useFakeTimers();
  rect = { x: 10, y: 20, width: 800, height: 600 };
  api.browser_pane_set_bounds.mockClear();
});
afterEach(() => {
  document.body.innerHTML = "";
  vi.useRealTimers();
});

it("an idle pane re-pinned every frame for 10 minutes sends almost nothing", async () => {
  const el = frame();
  document.elementFromPoint = () => el;
  beginBrowserPaneMount(TAB);
  // A rAF loop re-pins ~4 times a second; only the first send and the once-a-minute heal go out.
  for (let t = 0; t < 10 * 60_000; t += 250) {
    pushBrowserPaneBounds(TAB, el);
    await vi.advanceTimersByTimeAsync(250);
  }
  expect(api.browser_pane_set_bounds.mock.calls.length).toBeLessThan(15);
  // 2,400 fake-timer steps: a loaded PC can pass vitest's 5 s default, and a timed-out loop keeps
  // calling the shared mock into the next test. The budget is a call count, not a time.
}, LOOP_TIMEOUT_MS);

it("a plugin tab with no browser pane does not resend every frame", async () => {
  api.browser_pane_set_bounds.mockImplementation(async () => ({ ok: false, error: "pane not open" }) as never);
  const el = frame();
  document.elementFromPoint = () => el;
  beginBrowserPaneMount(TAB);
  for (let t = 0; t < 60_000; t += 16) {
    pushBrowserPaneBounds(TAB, el);
    await vi.advanceTimersByTimeAsync(16);
  }
  api.browser_pane_set_bounds.mockImplementation(async () => ({ ok: true }));
  expect(api.browser_pane_set_bounds.mock.calls.length).toBeLessThan(3);
}, LOOP_TIMEOUT_MS);

it("the split line's grab strip over the pane's edge does not cover it", () => {
  // Nov 7 2026: grabbing the line beside a Browser tab hid the pane (black) for the whole drag.
  const el = frame();
  const line = document.createElement("div");
  line.setAttribute("role", "separator");
  document.body.appendChild(line);
  document.elementFromPoint = (x: number) => (x < rect.x + 4 ? line : el);
  expect(paneCoverer(el, rect.x, rect.y, rect.width, rect.height)).toBe("");
  const card = document.createElement("div");
  document.body.appendChild(card);
  document.elementFromPoint = (x: number) => (x < rect.x + 4 ? card : el);
  expect(paneCoverer(el, rect.x, rect.y, rect.width, rect.height)).toBe("div");
});

it("a sub-pixel jitter sends nothing; a moved pane sends once", async () => {
  const el = frame();
  document.elementFromPoint = () => el;
  beginBrowserPaneMount(TAB);
  pushBrowserPaneBounds(TAB, el);
  await vi.advanceTimersByTimeAsync(0);
  expect(api.browser_pane_set_bounds).toHaveBeenCalledTimes(1);

  rect = { ...rect, x: 10.2, width: 800.3 };
  pushBrowserPaneBounds(TAB, el);
  expect(api.browser_pane_set_bounds).toHaveBeenCalledTimes(1);

  rect = { ...rect, x: 60 };
  pushBrowserPaneBounds(TAB, el);
  pushBrowserPaneBounds(TAB, el);
  expect(api.browser_pane_set_bounds).toHaveBeenCalledTimes(2);
});
