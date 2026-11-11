import { describe, expect, it } from "vitest";
import { __setAssetBaseForTest } from "../../../remote/assetBase";
import {
  panelMediaSrc,
  parseScreenshotResult,
  pickScreenshotBase64,
  pickScreenshotMediaUrl,
  pickScreenshotPath,
  screenshotSrcFromPath,
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

  it("shows the picture view_image looked at (JSON plus Claude's [Image: …] line)", () => {
    const path = "C:/Users/x/AppData/Local/UEFN-Ducky/chats/projects/_no_project/conversations/b47c8c50/attachments/1792941138083_1_Screenshot.png";
    const raw = `${JSON.stringify({ path, bytes: 54637 })}\n[Image: source: C:\\Users\\x\\.claude\\projects\\p\\2d81da25.png]`;
    const data = parseScreenshotResult(raw);
    expect(pickScreenshotPath(data, {})).toBe(path);
    expect(screenshotSrcFromPath(pickScreenshotPath(data, {}))).toBe("/chat-attachments/b47c8c50/1792941138083_1_Screenshot.png");
    const copied = parseScreenshotResult(JSON.stringify({ path: "D:/proj/Content/icon.png", bytes: 9, media_url: "http://127.0.0.1:4199/chat-attachments/c1/viewed_icon_1.png" }));
    expect(pickScreenshotMediaUrl(copied)).toContain("/chat-attachments/c1/viewed_icon_1.png");
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

  it("builds a chat-attachment url from the saved path", () => {
    const path =
      "C:/Users/x/AppData/Local/UEFN-Ducky/chats/projects/island/conversations/chat-1/attachments/uefn_viewport_1.png";
    expect(screenshotSrcFromPath(path)).toBe("/chat-attachments/chat-1/uefn_viewport_1.png");
    expect(screenshotSrcFromPath("C:/Temp/ducky_captures/shot.png")).toBe("");
  });

  it("does not invent Saved/Screenshots path when nothing was returned", () => {
    expect(pickScreenshotPath(null, {})).toBe("");
    expect(pickScreenshotPath({ ok: true }, {})).toBe("");
  });

  it("reads the JSON when Claude Code appends an [Image: source: …] line", () => {
    const url = "http://127.0.0.1:4199/chat-attachments/chat-1/uefn_viewport_1.png";
    const raw =
      JSON.stringify({ ok: true, path: "C:\\x\\uefn_viewport_1.png", media_url: url }) +
      "\n[Image: source: C:\\Users\\x\\.claude\\projects\\p\\tool-results\\mcp-uefn-blob-1.png]";
    const data = parseScreenshotResult(raw);
    expect(pickScreenshotMediaUrl(data)).toBe(url);
    expect(pickScreenshotPath(data, {})).toContain("uefn_viewport_1.png");
  });

  it("loads a made picture through the panel, also in Remote View", () => {
    const url = "http://127.0.0.1:4199/generated-images/image_1_0.png";
    __setAssetBaseForTest("/");
    expect(panelMediaSrc(url)).toBe("/generated-images/image_1_0.png");
    __setAssetBaseForTest("/static/plugins/uefn-ducky/panel/");
    expect(panelMediaSrc(url)).toBe("/static/plugins/uefn-ducky/panel/generated-images/image_1_0.png");
    expect(panelMediaSrc("https://cdn.example.com/a.png")).toBe("https://cdn.example.com/a.png");
    __setAssetBaseForTest(null);
  });

  it("unwraps string data envelopes", () => {
    const inner = JSON.stringify({ base64: TINY_PNG_B64, path: "/tmp/a.png" });
    const data = parseScreenshotResult(JSON.stringify({ ok: true, data: inner }));
    expect(pickScreenshotBase64(data)).toBe(TINY_PNG_B64);
    expect(pickScreenshotPath(data, {})).toBe("/tmp/a.png");
  });
});
