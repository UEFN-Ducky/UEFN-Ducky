// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { emitLlmPluginSlot, LlmPluginSlot, subscribeLlmPluginSlot, type LlmPluginSlotDetail } from "./llmPluginSlots";

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

  it("keeps the picker slot open when only the model changes", () => {
    const seen: boolean[] = [];
    const stop = subscribeLlmPluginSlot((d) => {
      if (d.surface === "picker") seen.push(d.open);
    });
    const { rerender, unmount } = render(
      <div>
        <LlmPluginSlot surface="picker" open providerId="anthropic" pluginId="anthropic" model="a" />
      </div>,
    );
    rerender(
      <div>
        <LlmPluginSlot surface="picker" open providerId="anthropic" pluginId="anthropic" model="b" />
      </div>,
    );
    expect(seen.includes(false)).toBe(false);
    expect(seen.some(Boolean)).toBe(true);
    unmount();
    stop();
  });
});
