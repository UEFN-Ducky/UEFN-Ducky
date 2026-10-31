import { afterEach, describe, expect, it, vi } from "vitest";
import { dragShieldRaised } from "./dragShield";
import { beginEditorTabDrag, beginWorkflowListDrag, endEditorTabDrag } from "./editorTabDrag";

function fakePage() {
  const classes = new Set<string>();
  const listeners = new Map<string, Set<() => void>>();
  const covers: boolean[] = [];
  vi.stubGlobal("document", {
    documentElement: { classList: { toggle: (c: string, on: boolean) => (on ? classes.add(c) : classes.delete(c)) } },
  });
  vi.stubGlobal("window", {
    __duckySetBrowserUiCover: (on: boolean) => covers.push(on),
    addEventListener: (type: string, fn: () => void) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(fn);
    },
    removeEventListener: (type: string, fn: () => void) => listeners.get(type)?.delete(fn),
  });
  const fire = (type: string) => [...(listeners.get(type) ?? [])].forEach((fn) => fn());
  return { classes, covers, fire, listening: () => [...listeners.values()].reduce((n, s) => n + s.size, 0) };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("drag shield", () => {
  it("lets a tab drag through plugin panels and hides native pages until the drag ends", () => {
    // Oct 31 2026: a tab dragged onto the Browser (or any plugin tab) showed no split squares.
    const page = fakePage();
    beginEditorTabDrag("tab-1", "group-1");
    expect(page.classes.has("ducky-drag-shield")).toBe(true);
    expect(page.covers).toEqual([true]);
    endEditorTabDrag();
    expect(page.classes.size).toBe(0);
    expect(page.covers).toEqual([true, false]);
    expect(page.listening()).toBe(0);
  });

  it("is lowered by the drag's own end when its source never ends it", () => {
    const page = fakePage();
    beginEditorTabDrag("tab-1", "group-1");
    page.fire("dragend");
    expect(dragShieldRaised()).toBe(false);
    endEditorTabDrag();
    expect(page.covers).toEqual([true, false]);
  });

  it("covers a sidebar tree drag the same way", () => {
    const page = fakePage();
    beginWorkflowListDrag();
    expect(page.classes.has("ducky-drag-shield")).toBe(true);
    page.fire("drop");
    expect(dragShieldRaised()).toBe(false);
    expect(page.covers).toEqual([true, false]);
    expect(page.listening()).toBe(0);
  });
});
