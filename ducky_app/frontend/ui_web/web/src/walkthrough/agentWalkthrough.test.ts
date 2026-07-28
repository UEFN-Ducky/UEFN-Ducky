import { describe, expect, it } from "vitest";
import { parseAgentWalkthroughSteps } from "./agentWalkthrough";

describe("parseAgentWalkthroughSteps", () => {
  it("accepts target/title/body and spotlight/label aliases", () => {
    const steps = parseAgentWalkthroughSteps([
      {
        target: "settings.tab.store",
        title: "Store",
        body: "Install plugins here.",
        advance: "require_click",
        navigate: "settings.store",
      },
      {
        spotlight: "settings.store.catalog",
        label: "Browse the catalog",
        mode: "circle",
      },
    ]);
    expect(steps).toHaveLength(2);
    expect(steps[0]?.target).toBe("settings.tab.store");
    expect(steps[0]?.advance).toBe("require_click");
    expect(steps[0]?.onEnter).toBeTypeOf("function");
    expect(steps[1]?.target).toBe("settings.store.catalog");
    expect(steps[1]?.title).toBe("Browse the catalog");
    expect(steps[1]?.advance).toBe("next");
    expect(steps[1]?.mode).toBe("circle");
  });

  it("drops empty steps", () => {
    expect(parseAgentWalkthroughSteps([{}, { title: "x" }, null])).toEqual([]);
  });
});
