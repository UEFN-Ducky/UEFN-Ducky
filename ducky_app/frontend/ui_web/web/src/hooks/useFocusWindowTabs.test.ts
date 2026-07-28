import { describe, expect, it } from "vitest";

import {
  decodeFocusParam,
  focusActivateNeedsOpen,
  focusIdToEditorTab,
  parseFocusId,
} from "./useFocusWindow";

describe("focusActivateNeedsOpen", () => {
  it("re-opens when the tab is missing from React openTabs", () => {
    expect(focusActivateNeedsOpen(["chat:other"], "chat:ghost")).toBe(true);
    expect(focusActivateNeedsOpen([], "file:/a.verse")).toBe(true);
  });

  it("only activates when the tab is already open", () => {
    expect(focusActivateNeedsOpen(["chat:live", "file:/a.verse"], "chat:live")).toBe(false);
  });

  it("ignores empty focus ids", () => {
    expect(focusActivateNeedsOpen([], "")).toBe(false);
  });
});

describe("decodeFocusParam", () => {
  it("decodes a once-encoded settings id left by WebView2", () => {
    expect(decodeFocusParam("settings%3Amain")).toBe("settings:main");
  });

  it("is a no-op for already-decoded ids", () => {
    expect(decodeFocusParam("settings:main")).toBe("settings:main");
    expect(decodeFocusParam("chat:abc")).toBe("chat:abc");
  });

  it("unwraps a double-encoded colon", () => {
    expect(decodeFocusParam("settings%253Amain")).toBe("settings:main");
  });
});

describe("parseFocusId / focusIdToEditorTab", () => {
  it("accepts settings even when the URL param is still percent-encoded", () => {
    expect(parseFocusId("settings%3Amain")).toEqual({ kind: "settings" });
    expect(focusIdToEditorTab("settings%3Amain", "Settings")).toEqual({
      id: "settings:main",
      kind: "settings",
      name: "Settings",
    });
  });

  it("parses plan / plugin / verse-translated birth tabs", () => {
    expect(parseFocusId("plan:chat-1")).toEqual({ kind: "plan", chatId: "chat-1" });
    expect(parseFocusId("plugin:anim:panel")).toEqual({
      kind: "plugin",
      tabId: "plugin:anim:panel",
      pluginId: "anim",
      panelId: "panel",
    });
    expect(parseFocusId("verse-translated:es:content/foo.verse")).toEqual({
      kind: "verse-translated",
      lang: "es",
      path: "content/foo.verse",
    });
  });

  it("parses ducky-profile pop-out tabs", () => {
    expect(parseFocusId("ducky-profile:audio")).toEqual({
      kind: "ducky-profile",
      profileId: "audio",
    });
    expect(focusIdToEditorTab("ducky-profile:audio", "Audio")).toEqual({
      id: "ducky-profile:audio",
      kind: "ducky-profile",
      name: "Audio",
      path: "audio",
    });
  });
});
