import { describe, expect, it } from "vitest";
import {
  effortInMenu,
  effortSuffix,
  formatEffortReadout,
  formatTokenText,
} from "./thinkingMenu";

const MENU = {
  lo: "Faster",
  hi: "Smarter",
  levels: [
    { id: "off", label: "Off", thinking_tokens: 0, hint: "No extended thinking" },
    { id: "low", label: "Low", thinking_tokens: 2048, hint: "2048 thinking tokens" },
    { id: "high", label: "High", thinking_tokens: 16384 },
    { id: "xhigh", label: "Extra", thinking_tokens: null, hint: "reasoning_effort=xhigh, no token cap" },
  ],
};

describe("thinkingMenu", () => {
  it("uses advertised ids, not a host Off/Low/Med/High list", () => {
    expect(effortInMenu(MENU, "xhigh")).toBe("xhigh");
    expect(effortInMenu(MENU, "medium")).toBe("off");
    expect(effortInMenu(null, "high")).toBe("off");
  });

  it("prints exact token counts, not ~2k", () => {
    expect(formatTokenText(MENU.levels[1])).toBe("2048 thinking tokens");
    expect(formatTokenText(MENU.levels[3])).toBe("no token cap");
    expect(formatEffortReadout("Opus 5", MENU.levels[0])).toBe("Opus 5 · Off · 0 thinking tokens");
    expect(effortSuffix(MENU, "off")).toBe(" · Off");
  });
});
