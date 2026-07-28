import { describe, expect, it } from "vitest";
import { resolvePluginIconContent } from "./pluginHeaderActions";

describe("resolvePluginIconContent", () => {
  it("prefers image assets over named keys", () => {
    expect(resolvePluginIconContent("assets/icon.svg")).toEqual({
      kind: "image",
      src: "assets/icon.svg",
    });
    expect(resolvePluginIconContent("data:image/png;base64,abc").kind).toBe("image");
  });

  it("maps named line-icon keys to emoji", () => {
    expect(resolvePluginIconContent("user")).toEqual({ kind: "emoji", emoji: "👤" });
    expect(resolvePluginIconContent("speaker")).toEqual({ kind: "emoji", emoji: "🔊" });
    expect(resolvePluginIconContent("globe")).toEqual({ kind: "emoji", emoji: "🌐" });
  });

  it("passes through raw emoji", () => {
    expect(resolvePluginIconContent("🎨")).toEqual({ kind: "emoji", emoji: "🎨" });
  });
});
