import { afterEach, describe, expect, it } from "vitest";

import { classifySidebarDragOut, type SidebarDragPoint } from "./sidebarDragOut";

/** classifySidebarDragOut reads `window`/`document` at call time, so the node
 * test env just needs these globals stubbed before each call. */
function stubWindow(overrides: Partial<Record<string, number>> = {}) {
  const win = {
    innerWidth: 1200,
    innerHeight: 800,
    screenX: 100,
    screenY: 100,
    outerWidth: 1200,
    outerHeight: 840,
    ...overrides,
  };
  (globalThis as unknown as { window: unknown }).window = win;
}

type FakeEl = {
  dataset?: { editorGroupId?: string };
  getBoundingClientRect: () => {
    left: number;
    right: number;
    top: number;
    bottom: number;
    width: number;
    height: number;
  };
};

function stubDom(opts: {
  leftRail?: { left: number; right: number; top?: number; bottom?: number } | null;
  rightRail?: { left: number; right: number; top?: number; bottom?: number } | null;
  editorGroup?: { id: string; left: number; right: number; top: number; bottom: number } | null;
}) {
  const mkRail = (r: { left: number; right: number; top?: number; bottom?: number }): FakeEl => ({
    getBoundingClientRect: () => ({
      left: r.left,
      right: r.right,
      top: r.top ?? 0,
      bottom: r.bottom ?? 800,
      width: r.right - r.left,
      height: (r.bottom ?? 800) - (r.top ?? 0),
    }),
  });
  const left = opts.leftRail ? mkRail(opts.leftRail) : null;
  const right = opts.rightRail ? mkRail(opts.rightRail) : null;
  const group = opts.editorGroup
    ? ({
        dataset: { editorGroupId: opts.editorGroup.id },
        getBoundingClientRect: () => ({
          left: opts.editorGroup!.left,
          right: opts.editorGroup!.right,
          top: opts.editorGroup!.top,
          bottom: opts.editorGroup!.bottom,
          width: opts.editorGroup!.right - opts.editorGroup!.left,
          height: opts.editorGroup!.bottom - opts.editorGroup!.top,
        }),
      } satisfies FakeEl)
    : null;

  (globalThis as unknown as { document: unknown }).document = {
    querySelector: (selector: string) => {
      if (
        selector.includes("dock-rail--left") &&
        (selector.includes("is-open") || selector.includes("is-peek"))
      ) {
        return left;
      }
      if (
        selector.includes("dock-rail--right") &&
        (selector.includes("is-open") || selector.includes("is-peek"))
      ) {
        return right;
      }
      if (selector.includes("[data-editor-group-id]")) return group;
      return null;
    },
    querySelectorAll: (selector: string) => {
      if (selector.includes("[data-editor-group-id]")) return group ? [group] : [];
      return [];
    },
  };
}

afterEach(() => {
  delete (globalThis as unknown as { window?: unknown }).window;
  delete (globalThis as unknown as { document?: unknown }).document;
});

const point = (p: Partial<SidebarDragPoint>): SidebarDragPoint => ({
  clientX: 0,
  clientY: 0,
  screenX: 0,
  screenY: 0,
  ...p,
});

const defaultEditor = {
  id: "g1",
  left: 260,
  right: 900,
  top: 40,
  bottom: 780,
};

describe("classifySidebarDragOut", () => {
  it("opens in the editor when a drop grazes just above the top edge (tab strip)", () => {
    stubWindow();
    stubDom({
      leftRail: { left: 0, right: 240 },
      editorGroup: { ...defaultEditor, top: 0 },
    });
    // 5px above the window top — the old strict `clientY < 0` test tore this off.
    const zone = classifySidebarDragOut(point({ clientX: 600, clientY: -5, screenX: 700, screenY: 95 }));
    expect(zone?.kind).toBe("editor");
    if (zone?.kind === "editor") {
      expect(zone.groupId).toBe("g1");
      expect(zone.zone).toBeTruthy();
    }
  });

  it("opens in the editor for a near-edge drop when only client coords are known", () => {
    stubWindow();
    stubDom({ leftRail: { left: 0, right: 240 }, editorGroup: { ...defaultEditor, top: 0 } });
    const zone = classifySidebarDragOut(point({ clientX: 600, clientY: -5 }));
    expect(zone?.kind).toBe("editor");
  });

  it("tears off only when the drop is clearly outside the window", () => {
    stubWindow();
    stubDom({ leftRail: { left: 0, right: 240 }, editorGroup: defaultEditor });
    const zone = classifySidebarDragOut(point({ clientX: 600, clientY: 400, screenX: 1400, screenY: 500 }));
    expect(zone).toEqual({ kind: "outside", screenX: 1400, screenY: 500 });
  });

  it("keeps normal move semantics when the drop stays over the left sidebar", () => {
    stubWindow();
    stubDom({ leftRail: { left: 0, right: 240 }, editorGroup: defaultEditor });
    const zone = classifySidebarDragOut(point({ clientX: 100, clientY: 400, screenX: 200, screenY: 500 }));
    expect(zone).toBeNull();
  });

  it("keeps normal move semantics when the drop stays over the right sidebar", () => {
    stubWindow();
    stubDom({
      leftRail: { left: 0, right: 240 },
      rightRail: { left: 960, right: 1200 },
      editorGroup: defaultEditor,
    });
    expect(classifySidebarDragOut(point({ clientX: 1100, clientY: 400 }))).toBeNull();
  });

  it("returns an editor zone when dragging from either side into the center", () => {
    stubWindow();
    stubDom({
      leftRail: { left: 0, right: 240 },
      rightRail: { left: 960, right: 1200 },
      editorGroup: defaultEditor,
    });
    const zone = classifySidebarDragOut(point({ clientX: 300, clientY: 400 }));
    expect(zone).toEqual({ kind: "editor", groupId: "g1", zone: "left" });
    const center = classifySidebarDragOut(point({ clientX: 580, clientY: 400 }));
    expect(center).toEqual({ kind: "editor", groupId: "g1", zone: "center" });
  });

  it("ignores an all-zero point (no drag data)", () => {
    stubWindow();
    stubDom({ leftRail: { left: 0, right: 240 }, editorGroup: defaultEditor });
    expect(classifySidebarDragOut(point({}))).toBeNull();
  });
});
