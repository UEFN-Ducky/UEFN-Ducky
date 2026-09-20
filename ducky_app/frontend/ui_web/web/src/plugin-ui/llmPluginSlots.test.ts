// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { emitLlmPluginSlot, subscribeLlmPluginSlot, type LlmPluginSlotDetail } from "./llmPluginSlots";

describe("llmPluginSlots", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("notifies listeners on open and close without a timer", () => {
    const seen: LlmPluginSlotDetail[] = [];
    const stop = subscribeLlmPluginSlot((d) => seen.push(d));
    const mount = document.createElement("div");
    emitLlmPluginSlot({
      surface: "picker",
      open: true,
      providerId: "ollama",
      pluginId: "ollama",
      model: "qwen3.8:latest",
      mount,
    });
    emitLlmPluginSlot({
      surface: "picker",
      open: false,
      providerId: "ollama",
      pluginId: "ollama",
      model: "qwen3.8:latest",
      mount: null,
    });
    stop();
    expect(seen.map((d) => d.open)).toEqual([true, false]);
    expect(seen[0]?.mount).toBe(mount);
    expect(seen[0]?.providerId).toBe("ollama");
    expect(seen[1]?.mount).toBeNull();
  });
});
