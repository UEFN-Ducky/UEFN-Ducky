// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

import { firstPickTourFor, installFirstPickTours, MODEL_PICKED_EVENT } from "./firstPick";
import {
  expandGatewayManifest,
  isLlmsProviderRowTarget,
  parsePluginWalkthroughs,
  pluginManifestToTour,
} from "./pluginWalkthroughs";
import { _resetWalkthroughServiceForTests, getWalkthroughState, registerTour, skipTour } from "./WalkthroughService";

afterEach(() => {
  vi.useRealTimers();
  _resetWalkthroughServiceForTests();
});

const rows = parsePluginWalkthroughs([
  {
    id: "ducky-auto",
    auto_start: "first_pick",
    provider_id: "ducky_auto",
    steps: [{ target: "settings.llms.provider.panel", title: "Ducky Auto", body: "Your rules" }],
  },
  { id: "anthropic", steps: [{ target: "settings.llms.provider.anthropic", title: "A", body: "a" }] },
]);

it("keeps first_pick and finds the tour for that gateway only", () => {
  expect(rows[0].auto_start).toBe("first_pick");
  expect(rows[1].auto_start).toBe("first_enable");
  expect(firstPickTourFor(rows, "ducky-auto")).toBe("plugin.ducky-auto");
  expect(firstPickTourFor(rows, "anthropic")).toBe(""); // first_enable tours never start on a pick
  expect(firstPickTourFor(rows, "")).toBe("");
});

it("treats the gateway's panel as a page target, not a provider row (no key steps added)", () => {
  expect(isLlmsProviderRowTarget("settings.llms.provider.panel")).toBe(false);
  expect(expandGatewayManifest(rows[0]).steps.map((s) => s.target)).toEqual(["settings.llms.provider.panel"]);
});

it("starts once on the first pick, never after it was finished or skipped", async () => {
  vi.useFakeTimers();
  registerTour(pluginManifestToTour(rows[0])!);
  const stop = installFirstPickTours(() => rows);
  const pick = () => window.dispatchEvent(new CustomEvent(MODEL_PICKED_EVENT, { detail: { providerKey: "ducky_auto" } }));

  pick();
  await vi.advanceTimersByTimeAsync(400);
  expect(getWalkthroughState().tourId).toBe("plugin.ducky-auto");

  await skipTour();
  pick();
  await vi.advanceTimersByTimeAsync(400);
  expect(getWalkthroughState().tourId).toBeFalsy();
  stop();
});
