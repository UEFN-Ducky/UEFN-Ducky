import { describe, expect, it } from "vitest";
import type { AutomationGraphDto, AutomationGraphNodeDto, AutomationNodeDto, CodeCheckDto, WorkflowNodeCodeDto } from "../types/panel";
import {
  BLANK_CODE, BLANK_PINS, blankCodeConfig, convertNode, dropWires, pinDiff, pinDiffEmpty, pinDiffText, revertNode, wiresDropped, wireText, withCheck,
} from "./codeNode";
import { nodePins } from "./pins";

const ask: AutomationNodeDto = {
  type: "llm.ask", label: "Ask a model", group: "Text & AI",
  inputs: [{ id: "prompt", label: "Prompt", type: "text" }, { id: "image", label: "Image", type: "image" }],
  outputs: [{ id: "text", label: "Text", type: "text" }],
};
const askNode = (): AutomationGraphNodeDto => ({ id: "a", type: "llm.ask", label: "Ask", x: 0, y: 0, config: { model: "m1", inputs: { prompt: "Hi" }, spend: true } });

function graphWith(node: AutomationGraphNodeDto): AutomationGraphDto {
  return {
    nodes: [
      { id: "q", type: "input.text", x: 0, y: 0, config: {} },
      node,
      { id: "p", type: "util.preview", x: 0, y: 0, config: {} },
    ],
    edges: [
      { source: "q", target: "a", kind: "main" },
      { source: "q", target: "a", kind: "data", source_pin: "value", target_pin: "prompt" },
      { source: "q", target: "a", kind: "data", source_pin: "value", target_pin: "image" },
      { source: "a", target: "p", kind: "data", source_pin: "text", target_pin: "value" },
    ],
  };
}

function info(pins = { exec: true, inputs: [{ id: "prompt", label: "Prompt", type: "text" as const }], outputs: [{ id: "text", label: "Text", type: "text" as const }] }): WorkflowNodeCodeDto {
  return {
    kind: "builtin", code: "// generated\nexport const node = {};\nexport default async function run() { return {}; }\n", code_sha: "sha-gen",
    pins, settings_spec: [], uses: { tools: [], builtins: ["llm.ask"] }, problems: [], convertible: true, reason: "", approved: true,
  };
}

describe("custom code nodes", () => {
  it("start from the blank template and its pins", () => {
    const config = blankCodeConfig();
    expect(config.code).toBe(BLANK_CODE);
    expect(config.pins).toEqual(BLANK_PINS);
    expect(BLANK_CODE.startsWith("// @ts-check\nexport const node = {")).toBe(true);
    expect(BLANK_CODE).toContain("export default async function run(input, ducky) {");
    expect(nodePins({ id: "c", type: "code.js", x: 0, y: 0, config }, undefined)).toEqual(BLANK_PINS);
  });

  it("convert a built-in keeping its typed-in inputs, Spend switch and the wires its pins still fit", () => {
    const node = askNode();
    const converted = convertNode(node, info());
    expect(converted.type).toBe("code.js");
    expect(converted.label).toBe("Ask");
    expect(converted.config).toEqual(expect.objectContaining({
      code: info().code, code_sha: "sha-gen", inputs: { prompt: "Hi" }, spend: true, settings: {},
      based_on: { type: "llm.ask", config: node.config, code_sha: "sha-gen" },
    }));
    expect(node.config.inputs).toEqual({ prompt: "Hi" });  // the original is not touched
    const graph = graphWith(node);
    // The generated code declares no image input, so that one wire would drop; the others stay.
    const dropped = wiresDropped(graph, "a", nodePins(converted, undefined));
    expect(dropped).toEqual([graph.edges[2]]);
    expect(wireText(dropped[0], (id) => ({ q: "Question", a: "Ask" })[id] || id)).toBe("Question.value → Ask.image");
    expect(dropWires(graph, dropped).edges).toEqual([graph.edges[0], graph.edges[1], graph.edges[3]]);
    // Same pins as the built-in: nothing drops.
    const same = convertNode(node, info({ exec: true, inputs: ask.inputs!, outputs: ask.outputs! }));
    expect(wiresDropped(graph, "a", nodePins(same, undefined))).toEqual([]);
  });

  it("revert to the built-in with the settings it had, dropping wires to pins only the code had", () => {
    const converted = convertNode(askNode(), info());
    const coded = withCheck(converted, { ok: true, problems: [], pins: { exec: true, inputs: [{ id: "prompt", label: "Prompt", type: "text" }], outputs: [{ id: "text", label: "Text", type: "text" }, { id: "extra", label: "Extra", type: "json" }] }, settings_spec: [], uses: { tools: [], builtins: [] }, code_sha: "x" });
    const graph = graphWith(coded);
    graph.edges.push({ source: "a", target: "p", kind: "data", source_pin: "extra", target_pin: "value" });
    const back = revertNode(coded)!;
    expect(back).toEqual(expect.objectContaining({ id: "a", type: "llm.ask", label: "Ask", config: askNode().config }));
    expect(wiresDropped(graph, "a", nodePins(back, ask))).toEqual([graph.edges[4]]);
    expect(revertNode({ id: "b", type: "code.js", x: 0, y: 0, config: blankCodeConfig() })).toBeNull();  // a blank one has no built-in
  });

  it("keep the last good pins when a check fails", () => {
    const node: AutomationGraphNodeDto = { id: "c", type: "code.js", x: 0, y: 0, config: blankCodeConfig() };
    const good: CodeCheckDto = {
      ok: true, problems: [], code_sha: "s1", uses: { tools: ["ducky_terminal_run"], builtins: [] },
      pins: { exec: true, inputs: [{ id: "command", label: "Command", type: "text" }], outputs: [{ id: "exit_code", label: "Exit code", type: "number" }] },
      settings_spec: [{ id: "timeout", label: "Timeout (s)", type: "number", default: 300 }],
    };
    const checked = withCheck(node, good);
    expect(checked.config.pins).toEqual(good.pins);
    expect(checked.config.settings_spec).toEqual(good.settings_spec);
    expect(checked.config.uses).toEqual(good.uses);
    const broken: CodeCheckDto = { ok: false, problems: [{ line: 3, col: 12, message: "Unexpected token", severity: "error" }], code_sha: "s2", pins: { exec: false, inputs: [], outputs: [] }, settings_spec: [], uses: { tools: [], builtins: [] } };
    const after = withCheck(checked, broken);
    expect(after.config.pins).toEqual(good.pins);
    expect(after.config.settings_spec).toEqual(good.settings_spec);
    expect(after.config.uses).toEqual(good.uses);
    expect(after.config.problems).toEqual(broken.problems);
    expect(after.config.code_sha).toBeUndefined();  // the server writes the hash when it saves
  });

  it("say what changes on the card in plain words", () => {
    const before = { exec: true, inputs: [{ id: "text", label: "Text", type: "text" as const }], outputs: [{ id: "text", label: "Text", type: "text" as const }, { id: "n", label: "N", type: "number" as const }] };
    const after = { exec: true, inputs: [{ id: "text", label: "Text", type: "text" as const }, { id: "name", label: "Name", type: "text" as const }], outputs: [{ id: "n", label: "N", type: "json" as const }] };
    const diff = pinDiff(before, after);
    expect(pinDiffEmpty(diff)).toBe(false);
    expect(pinDiffText(diff, 1)).toEqual(["+ input “name” (Text)", "− output “text”", "output “n”: Number → Data", "1 wire will be disconnected"]);
    expect(pinDiffText(pinDiff(before, before), 2)).toEqual(["2 wires will be disconnected"]);
    expect(pinDiffEmpty(pinDiff(before, before))).toBe(true);
    expect(pinDiffText(pinDiff(before, { ...before, exec: false }))).toEqual(["Becomes a value node (no white pins)"]);
  });
});
