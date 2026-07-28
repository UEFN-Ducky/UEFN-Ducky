import { describe, expect, it } from "vitest";
import { attributePluginFromErrorEvent, attributePluginFromUrl } from "./pluginCrashGuard";

describe("pluginCrashGuard attribution", () => {
  it("parses plugin-ui URLs", () => {
    expect(attributePluginFromUrl("/plugin-ui/discord/ui/boot.js")).toBe("discord");
    expect(
      attributePluginFromUrl("http://127.0.0.1:4199/plugin-ui/matrix-skin/fx.js?t=1"),
    ).toBe("matrix-skin");
    expect(attributePluginFromUrl("/assets/index.js")).toBeNull();
  });

  it("attributes plugin-ui filename as plugin script", () => {
    expect(
      attributePluginFromErrorEvent({
        filename: "/plugin-ui/browser/ui/boot.js",
        message: "boom",
      }),
    ).toEqual({
      pluginId: "browser",
      surface: "script",
      kind: "plugin",
    });
  });

  it("attributes appearance stack paths via URL", () => {
    expect(
      attributePluginFromUrl(
        "Error: x\n    at https://local/plugin-ui/coolskin/skin.js:1:1",
      ),
    ).toBe("coolskin");
  });
});
