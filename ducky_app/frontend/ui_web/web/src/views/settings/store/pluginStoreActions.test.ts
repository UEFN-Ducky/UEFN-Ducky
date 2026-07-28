import { describe, expect, it } from "vitest";
import {
  pluginHasWalkthrough,
  settingsTargetFromContributes,
} from "./pluginStoreActions";

describe("settingsTargetFromContributes", () => {
  it("prefers settings.tabs", () => {
    expect(
      settingsTargetFromContributes({
        "settings.tabs": [{ id: "Discord", label: "Discord" }],
        "llm.providers": [{ id: "gemini", label: "Google" }],
      }),
    ).toEqual({ kind: "tab", tab: "Discord" });
  });

  it("falls back to llm.providers", () => {
    expect(
      settingsTargetFromContributes({
        "llm.providers": [{ id: "gemini", label: "Google" }],
      }),
    ).toEqual({ kind: "llm", providerId: "gemini", label: "Google" });
  });

  it("uses walkthrough settings_tab when nothing else", () => {
    expect(
      settingsTargetFromContributes({
        walkthrough: { settings_tab: "Languages", steps: [{ target: "x" }] },
      }),
    ).toEqual({ kind: "tab", tab: "Languages" });
  });

  it("returns null when empty", () => {
    expect(settingsTargetFromContributes({})).toBeNull();
    expect(settingsTargetFromContributes(null)).toBeNull();
  });
});

describe("pluginHasWalkthrough", () => {
  it("requires steps", () => {
    expect(pluginHasWalkthrough({ walkthrough: { id: "x", steps: [] } })).toBe(false);
    expect(
      pluginHasWalkthrough({
        walkthrough: { id: "x", steps: [{ target: "settings.llms.section.llms" }] },
      }),
    ).toBe(true);
  });
});
