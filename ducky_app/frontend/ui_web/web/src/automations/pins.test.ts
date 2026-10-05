import { describe, expect, it } from "vitest";
import type { AutomationGraphNodeDto, AutomationNodeDto, AutomationSummaryDto } from "../types/panel";
import { NODE_HEIGHT, NODE_HEIGHT_COMPACT, NODE_TITLE_HEIGHT } from "./graphGeometry";
import { accepts, cleanType, firstFit, nodeLayout, nodePins, PIN_ROW, shortValue } from "./pins";

const node = (type: string, config: Record<string, unknown> = {}): AutomationGraphNodeDto => ({ id: "n", type, x: 0, y: 0, config });

describe("pins", () => {
  it("matches the backend's type rules", () => {
    expect(accepts("any", "image")).toBe(true);
    expect(accepts("json", "number")).toBe(true);
    expect(accepts("text", "number")).toBe(true);
    expect(accepts("text", "boolean")).toBe(true);
    expect(accepts("number", "text")).toBe(false);
    expect(accepts("images", "image")).toBe(true);
    expect(accepts("image", "images")).toBe(false);
    expect(accepts("file", "audio")).toBe(true);
    expect(accepts("audio", "video")).toBe(false);
    expect(cleanType("IMAGE")).toBe("image");
    expect(cleanType("nonsense")).toBe("any");
  });

  it("works pins out from a node's settings", () => {
    const ask: AutomationNodeDto = { type: "llm.ask", label: "Ask a model", inputs: [{ id: "prompt", label: "Prompt", type: "text" }], outputs: [{ id: "text", label: "Text", type: "text" }] };
    expect(nodePins(node("llm.ask"), ask)).toEqual({ exec: true, inputs: [{ id: "prompt", label: "Prompt", type: "text" }], outputs: [{ id: "text", label: "Text", type: "text" }] });
    expect(nodePins(node("logic.if"), { type: "logic.if", label: "If" }).inputs.map((pin) => pin.id)).toEqual(["value"]);
    expect(nodePins(node("logic.expression", { names: ["score", "name"] }), undefined).inputs.map((pin) => pin.id)).toEqual(["score", "name"]);
    expect(nodePins(node("flow.input", { inputs: [{ name: "who", type: "text" }, { name: "pic", type: "image" }] }), undefined).outputs)
      .toEqual([{ id: "who", label: "who", type: "text" }, { id: "pic", label: "pic", type: "image" }]);
    const fn = { id: "fn", name: "Greeter", enabled: true, signature: { inputs: [{ name: "who", default: "world", type: "text" }], outputs: ["greeting"], output_types: { greeting: "text" } } } as unknown as AutomationSummaryDto;
    const call = nodePins(node("workflow.call", { workflow_id: "fn" }), undefined, [fn]);
    expect(call.inputs).toEqual([{ id: "who", label: "who", type: "text", default: "world" }]);
    expect(call.outputs).toEqual([{ id: "greeting", label: "greeting", type: "text" }]);
    expect(nodePins(node("input.text"), { type: "input.text", label: "Text", exec: false, outputs: [{ id: "value", label: "Text", type: "text" }] }).exec).toBe(false);
  });

  it("reads a custom code node's pins from what its code declared (config.pins), like pins.py", () => {
    const meta: AutomationNodeDto = { type: "code.js", label: "Custom code", group: "Code", inputs: [{ id: "catalog", label: "Catalog", type: "text" }] };
    const code = node("code.js", { pins: {
      exec: true,
      inputs: [{ id: "command", type: "text", label: "Command", required: true }, { id: "odd", type: "weird" }, { id: "command", type: "number" }, { type: "text" }],
      outputs: [{ id: "exit_code", type: "number", label: "Exit code" }],
    } });
    expect(nodePins(code, meta)).toEqual({
      exec: true,
      inputs: [{ id: "command", label: "Command", type: "text", required: true }, { id: "odd", label: "odd", type: "any" }],
      outputs: [{ id: "exit_code", label: "Exit code", type: "number" }],
    });
    // A value node (kind "value") has no white pins; only exec: true makes a step.
    expect(nodePins(node("code.js", { pins: { exec: false, inputs: [], outputs: [{ id: "v", type: "json" }] } }), meta).exec).toBe(false);
    expect(nodePins(node("code.js", { pins: { inputs: [], outputs: [] } }), meta).exec).toBe(false);
    // Not checked yet: no pins, white pins as the catalog says.
    expect(nodePins(node("code.js"), meta)).toEqual({ exec: true, inputs: [], outputs: [] });
  });

  it("makes a pin per name: Make List inputs, Extract data outputs", () => {
    expect(nodePins(node("list.make"), undefined).inputs.map((pin) => pin.id)).toEqual(["a", "b"]);
    expect(nodePins(node("list.make", { names: ["x", "y", "z"] }), undefined).inputs.map((pin) => pin.id)).toEqual(["x", "y", "z"]);
    const extract: AutomationNodeDto = { type: "llm.extract", label: "Extract data", outputs: [{ id: "data", label: "Data", type: "json" }] };
    expect(nodePins(node("llm.extract", { names: ["name", "level", "data"] }), extract).outputs.map((pin) => pin.id)).toEqual(["data", "name", "level"]);
  });

  it("grows a card a row per pin, and keeps thin cards one size", () => {
    const pins = { exec: true, inputs: [{ id: "a", label: "a", type: "text" as const }, { id: "b", label: "b", type: "text" as const }], outputs: [{ id: "r", label: "r", type: "any" as const }] };
    const full = nodeLayout(pins);
    expect(full.rows).toBe(2);
    expect(full.height).toBeGreaterThan(NODE_TITLE_HEIGHT + 2 * PIN_ROW);
    expect(full.inputY.b - full.inputY.a).toBe(PIN_ROW);
    expect(full.outputY.r).toBe(full.inputY.a);
    expect(nodeLayout(pins, false, 64).height).toBe(full.height + 64);
    const thin = nodeLayout(pins, true);
    expect(thin.height).toBe(NODE_HEIGHT_COMPACT);
    expect(thin.inputY.b).toBe(NODE_HEIGHT_COMPACT / 2);
    expect(nodeLayout({ exec: true, inputs: [], outputs: [] }).height).toBe(NODE_HEIGHT);
  });

  it("finds the first pin a wire fits and shortens values for the card", () => {
    const inputs = [{ id: "n", label: "n", type: "number" as const }, { id: "t", label: "t", type: "text" as const }];
    expect(firstFit(inputs, "text", true)?.id).toBe("t");
    expect(firstFit(inputs, "image", true)).toBeUndefined();
    expect(shortValue(true)).toBe("Yes");
    expect(shortValue("a".repeat(30))).toHaveLength(22);
    expect(shortValue([1, 2])).toBe("2 items");
    expect(shortValue({ path: "C:/x/duck.png", name: "duck.png" })).toBe("duck.png");
  });
});
