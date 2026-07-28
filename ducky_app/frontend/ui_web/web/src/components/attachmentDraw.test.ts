import { describe, expect, it } from "vitest";
import { mapPointerToCanvas } from "./AttachmentPreviewModal";

describe("mapPointerToCanvas", () => {
  it("maps CSS-scaled pointer coords to canvas pixels", () => {
    // 1920x1080 image displayed at 480x270 (4x downscale), offset 100,50.
    const rect = { left: 100, top: 50, width: 480, height: 270 };
    const pt = mapPointerToCanvas(340, 185, rect, 1920, 1080);
    expect(pt.x).toBeCloseTo(960);
    expect(pt.y).toBeCloseTo(540);
    expect(pt.scale).toBeCloseTo(4);
  });

  it("is identity when canvas is displayed 1:1", () => {
    const rect = { left: 0, top: 0, width: 200, height: 100 };
    const pt = mapPointerToCanvas(20, 30, rect, 200, 100);
    expect(pt).toEqual({ x: 20, y: 30, scale: 1 });
  });
});
