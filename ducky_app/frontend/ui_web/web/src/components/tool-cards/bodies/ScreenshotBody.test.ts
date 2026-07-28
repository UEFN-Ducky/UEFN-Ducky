import { describe, expect, it } from "vitest";
import {
  parseScreenshotResult,
  pickScreenshotBase64,
  pickScreenshotError,
  pickScreenshotMediaUrl,
  pickScreenshotPath,
  pickScreenshotStatus,
} from "./ScreenshotBody";

const TINY_PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

describe("ScreenshotBody result parsing", () => {
  it("prefers media_url path payload (no base64)", () => {
    const raw = JSON.stringify({
      ok: true,
      format: "png",
      bytes: 1200,
      path: "C:\\\\Users\\\\x\\\\AppData\\\\Local\\\\UEFN-Ducky\\\\tool_captures\\\\blender_viewport_1.png",
      filename: "blender_viewport_1.png",
      media_url: "http://127.0.0.1:4199/tool-captures/blender_viewport_1.png",
      width: 400,
      height: 242,
    });
    const data = parseScreenshotResult(raw);
    expect(pickScreenshotMediaUrl(data)).toContain("/tool-captures/blender_viewport_1.png");
    expect(pickScreenshotPath(data, {})).toContain("blender_viewport_1.png");
    expect(pickScreenshotBase64(data)).toBe("");
  });

  it("still reads legacy base64 payloads", () => {
    const raw = JSON.stringify({
      ok: true,
      format: "png",
      bytes: 68,
      base64: TINY_PNG_B64,
      blender_result: {
        success: true,
        filepath: "C:\\\\Temp\\\\blender_screenshot_1.png",
      },
    });
    const data = parseScreenshotResult(raw);
    expect(pickScreenshotBase64(data)).toBe(TINY_PNG_B64);
    expect(pickScreenshotPath(data, {})).toContain("blender_screenshot_1.png");
  });

  it("does not invent Saved/Screenshots path when nothing was returned", () => {
    expect(pickScreenshotPath(null, {})).toBe("");
    expect(pickScreenshotPath({ ok: true }, {})).toBe("");
  });

  it("unwraps string data envelopes", () => {
    const inner = JSON.stringify({ base64: TINY_PNG_B64, path: "/tmp/a.png" });
    const data = parseScreenshotResult(JSON.stringify({ ok: true, data: inner }));
    expect(pickScreenshotBase64(data)).toBe(TINY_PNG_B64);
    expect(pickScreenshotPath(data, {})).toBe("/tmp/a.png");
  });

  it("does not treat bare args.filename labels as filesystem paths", () => {
    expect(
      pickScreenshotPath({ status: "timed_out", error: "timeout" }, { filename: "city_00_foundation" }),
    ).toBe("");
  });

  it("surfaces capture errors and failed status", () => {
    const data = parseScreenshotResult(
      JSON.stringify({
        status: "timed_out",
        error: "Screenshot timed out after 25s",
        filename: "shot.png",
      }),
    );
    expect(pickScreenshotStatus(data)).toBe("timed_out");
    expect(pickScreenshotError(data)).toContain("timed out");
  });

  it("prefers capture_path when path is missing", () => {
    const data = {
      capture_path: "C:\\\\Users\\\\x\\\\AppData\\\\Local\\\\UEFN-Ducky\\\\tool_captures\\\\a.png",
      filename: "a.png",
    };
    expect(pickScreenshotPath(data, {})).toContain("tool_captures");
  });
});
