// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { APP_IDLE_CLASS, installAppIdle, isAppIdle, subscribeAppIdle } from "./appIdle";

let focused = true;
let hidden = false;

beforeEach(() => {
  vi.useFakeTimers();
  focused = true;
  hidden = false;
  vi.spyOn(document, "hasFocus").mockImplementation(() => focused);
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
});
afterEach(() => {
  installAppIdle()();
  vi.restoreAllMocks();
  vi.useRealTimers();
  delete (document as { hidden?: boolean }).hidden;
});

const blur = () => { focused = false; window.dispatchEvent(new Event("blur")); };
const focus = () => { focused = true; window.dispatchEvent(new Event("focus")); };
const rootIsIdle = () => document.documentElement.classList.contains(APP_IDLE_CLASS);

it("goes idle once the window has been in the background for a while, and wakes on focus", () => {
  const seen: boolean[] = [];
  const stop = subscribeAppIdle((idle) => seen.push(idle));
  blur();
  vi.advanceTimersByTime(29_000);
  expect(isAppIdle()).toBe(false);
  vi.advanceTimersByTime(1_000);
  expect(isAppIdle()).toBe(true);
  expect(rootIsIdle()).toBe(true);
  focus();
  expect(rootIsIdle()).toBe(false);
  expect(seen).toEqual([true, false]);
  stop();
});

it("is idle at once while hidden and awake again when shown", () => {
  installAppIdle();
  hidden = true;
  document.dispatchEvent(new Event("visibilitychange"));
  expect(isAppIdle()).toBe(true);
  hidden = false;
  document.dispatchEvent(new Event("visibilitychange"));
  expect(isAppIdle()).toBe(false);
});

it("wakes when the pointer moves over a window in the background", () => {
  installAppIdle();
  blur();
  vi.advanceTimersByTime(30_000);
  expect(isAppIdle()).toBe(true);
  window.dispatchEvent(new Event("pointermove"));
  expect(isAppIdle()).toBe(false);
  vi.advanceTimersByTime(30_000);
  expect(isAppIdle()).toBe(true);
});

it("stays awake while focus is inside a plugin's frame", () => {
  installAppIdle();
  window.dispatchEvent(new Event("blur")); // the window blurs, but the document still has focus
  vi.advanceTimersByTime(60_000);
  expect(isAppIdle()).toBe(false);
});

it("holds every endless glow that stays on while nothing runs", () => {
  // Each repainted 60 times a second for as long as it showed, with nobody looking.
  const css = ["components-inline.css", "chat.css"]
    .map((name) => readFileSync(`${process.cwd()}/src/theme/styles/${name}`, "utf8"))
    .join("\n");
  const held = [...css.matchAll(/([^{}]*:root\.app-idle[^{}]*)\{([^}]*)\}/g)]
    .filter(([, , body]) => /animation-play-state:\s*paused/.test(body))
    .flatMap(([, selectors]) => selectors.split(",").map((s) => s.replace(/\/\*[\s\S]*?\*\//g, "").trim()));
  for (const glow of [".chat-completion-alert", ".update-toast-card", ".update-toast-dot", ".chat-changes-btn.is-open"]) {
    expect(held).toContain(`:root.app-idle ${glow}`);
  }
});
