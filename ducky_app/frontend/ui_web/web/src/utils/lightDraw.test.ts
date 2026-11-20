// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { PanelPushEvent } from "../types/panel";
import { APP_LIGHT_CLASS, installLightDraw, isLightDraw } from "./lightDraw";

const push = (event: Partial<PanelPushEvent>) => window.__uefnPanelPush?.(event as PanelPushEvent);

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  installLightDraw()();
  vi.useRealTimers();
});

it("draws light while the app says the graphics card is full, and normally when it has room", () => {
  installLightDraw();
  expect(isLightDraw()).toBe(false);
  push({ type: "pc_draw", light: true });
  expect(document.documentElement.classList.contains(APP_LIGHT_CLASS)).toBe(true);
  push({ type: "project_changed" }); // any other event changes nothing
  expect(isLightDraw()).toBe(true);
  push({ type: "pc_draw", light: false });
  expect(isLightDraw()).toBe(false);
});

it("goes back to normal drawing by itself when the app stops saying so", () => {
  installLightDraw();
  push({ type: "pc_draw", light: true });
  vi.advanceTimersByTime(30_000);
  push({ type: "pc_draw", light: true }); // the next 30 s sample: still full
  vi.advanceTimersByTime(60_000);
  expect(isLightDraw()).toBe(true);
  vi.advanceTimersByTime(15_000); // two samples missed
  expect(isLightDraw()).toBe(false);
});

it("stopping it draws normally again", () => {
  const stop = installLightDraw();
  push({ type: "pc_draw", light: true });
  stop();
  expect(isLightDraw()).toBe(false);
  push({ type: "pc_draw", light: true });
  expect(isLightDraw()).toBe(false);
});

it("light drawing blurs nothing and steps every animation (two global rules)", () => {
  const css = readFileSync(`${process.cwd()}/src/theme/styles/global.css`, "utf8");
  const vars = css.match(/:root\.app-light\s*\{([^}]*)\}/);
  // A radius of 0, not `none`: the panel keeps its own layer, so nothing placed inside it moves.
  expect(vars?.[1]).toMatch(/--blur-md:\s*blur\(0px\)/);
  expect(vars?.[1]).toMatch(/--wf-panel-blur:\s*0px/);
  const all = css.match(/:root\.app-light \*,\s*:root\.app-light \*::before,\s*:root\.app-light \*::after\s*\{([^}]*)\}/);
  expect(all?.[1]).toMatch(/animation-timing-function:\s*steps\(8, end\)\s*!important/);
});

it("the app's event reaches the window's panel bus", () => {
  const bus = readFileSync(`${process.cwd()}/src/hooks/useAgentEventBus.ts`, "utf8");
  expect(bus.match(/PANEL_PUSH_TYPES = new Set<string>\(\[([\s\S]*?)\]\)/)?.[1]).toContain('"pc_draw"');
});
