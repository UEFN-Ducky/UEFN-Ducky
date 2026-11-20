/**
 * Light drawing while the graphics card is full.
 *
 * Ducky shares the card with UEFN and the game. In a week (Nov 20 2026) 17,968 of 19,561
 * window freezes had none of Ducky's own script, layout or paint time in them, and where
 * the card was recorded most of that time was with it 85% full or more. The app tells the
 * window when the card is full, or when the window is drawn without the card
 * (frontend/busy_pc.py `light_draw`, the `pc_draw` event). While it says so the root
 * element carries the `app-light` class: nothing is blurred behind panels and animations
 * move in steps (theme `app-light` rules). Both come back when the card has room.
 */

import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";

export const APP_LIGHT_CLASS = "app-light";
/** The app says "light" again with every 30 s sample. Two missed in a row (it restarted,
 *  the link dropped) and the window draws normally again by itself. */
const STALE_MS = 75_000;

let timer = 0;
let uninstall: (() => void) | null = null;

export function isLightDraw(): boolean {
  return document.documentElement.classList.contains(APP_LIGHT_CLASS);
}

export function setLightDraw(on: boolean): void {
  window.clearTimeout(timer);
  timer = 0;
  document.documentElement.classList.toggle(APP_LIGHT_CLASS, on);
  if (on) timer = window.setTimeout(() => setLightDraw(false), STALE_MS);
}

/** Starts listening (once); the returned function stops it and draws normally again. */
export function installLightDraw(): () => void {
  if (uninstall || typeof document === "undefined") return uninstall ?? (() => {});
  installPanelPushBus();
  const stop = subscribePanelPush((event) => {
    if (event.type === "pc_draw") setLightDraw(Boolean(event.light));
  });
  uninstall = () => {
    stop();
    setLightDraw(false);
    uninstall = null;
  };
  return uninstall;
}
