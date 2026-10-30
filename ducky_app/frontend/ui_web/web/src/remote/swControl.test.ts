// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { serviceWorkerControl } from "./directTransport";

function fakeSw(controller: object | null) {
  const target = new EventTarget() as EventTarget & { controller: object | null };
  target.controller = controller;
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: target });
  return target;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  delete (navigator as { serviceWorker?: unknown }).serviceWorker;
});

describe("first open waits for the Service Worker", () => {
  it("draws at once when the worker already controls the page", async () => {
    fakeSw({});
    let ready = false;
    void serviceWorkerControl().then(() => (ready = true));
    await vi.advanceTimersByTimeAsync(0);
    expect(ready).toBe(true);
  });

  it("waits for the worker to take over (its pictures would 404 before)", async () => {
    const sw = fakeSw(null);
    let ready = false;
    void serviceWorkerControl().then(() => (ready = true));
    await vi.advanceTimersByTimeAsync(500);
    expect(ready).toBe(false);
    sw.dispatchEvent(new Event("controllerchange"));
    await vi.advanceTimersByTimeAsync(0);
    expect(ready).toBe(true);
  });

  it("draws anyway after 3 s", async () => {
    fakeSw(null);
    let ready = false;
    void serviceWorkerControl().then(() => (ready = true));
    await vi.advanceTimersByTimeAsync(2_999);
    expect(ready).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(ready).toBe(true);
  });
});
