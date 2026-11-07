import { describe, expect, it, vi } from "vitest";
import { reloadForStaleChunk } from "./ErrorBoundary";

function memStorage() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
}

const chunkError = new TypeError(
  "Failed to fetch dynamically imported module: http://127.0.0.1:4199/assets/AutomationsView-CaJB9e_R.js",
);

describe("reloadForStaleChunk", () => {
  it("reloads once for a missing lazy chunk, then shows the error if it fails again", () => {
    const s = memStorage();
    const reload = vi.fn();
    expect(reloadForStaleChunk(chunkError, s, reload, 1_000_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    // The reloaded page hits it again right away: no loop.
    expect(reloadForStaleChunk(chunkError, s, reload, 1_010_000)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
    // Much later (another update): one more reload is fine.
    expect(reloadForStaleChunk(chunkError, s, reload, 1_100_000)).toBe(true);
  });

  it("leaves other errors and Safari/Firefox wording alone or handled", () => {
    const reload = vi.fn();
    expect(reloadForStaleChunk(new Error("x is undefined"), memStorage(), reload)).toBe(false);
    expect(reloadForStaleChunk(new TypeError("Importing a module script failed."), memStorage(), reload)).toBe(true);
    expect(reloadForStaleChunk(chunkError, null, reload)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
