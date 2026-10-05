// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { AutomationGraphDto, AutomationGraphNodeDto, AutomationNodeDto, WorkflowNodeCodeDto } from "../types/panel";
import { CodeTab, type CodeTabProps } from "./CodeTab";
import { blankCodeConfig } from "./codeNode";
import { nodePins } from "./pins";

const api = vi.hoisted(() => ({
  get_workflow_node_code: vi.fn(),
  check_workflow_node_code: vi.fn(),
  test_workflow_node: vi.fn(),
  approve_workflow_node_code: vi.fn(),
  workflow_code_api: vi.fn(),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));

const ask: AutomationNodeDto = {
  type: "llm.ask", label: "Ask a model", group: "Text & AI",
  inputs: [{ id: "prompt", label: "Prompt", type: "text" }, { id: "image", label: "Image", type: "image" }],
  outputs: [{ id: "text", label: "Text", type: "text" }],
};
const byType = new Map<string, AutomationNodeDto>([["llm.ask", ask], ["input.text", { type: "input.text", label: "Text", group: "Inputs" }], ["code.js", { type: "code.js", label: "Custom code", group: "Code" }]]);
const GENERATED = "// Built-in Ask a model\nexport const node = { kind: \"step\" };\nexport default async function run(input, ducky) {\n  return await ducky.builtin(\"llm.ask\", input, {});\n}\n";

function builtinInfo(patch: Partial<WorkflowNodeCodeDto> = {}): WorkflowNodeCodeDto {
  return {
    ok: true, kind: "builtin", code: GENERATED, code_sha: "gen1",
    pins: { exec: true, inputs: [{ id: "prompt", label: "Prompt", type: "text" }], outputs: [{ id: "text", label: "Text", type: "text" }] },
    settings_spec: [], uses: { tools: [], builtins: ["llm.ask"] }, problems: [], convertible: true, reason: "", approved: true, ...patch,
  };
}

function graphOf(node: AutomationGraphNodeDto): AutomationGraphDto {
  return {
    nodes: [{ id: "q", type: "input.text", label: "Question", x: 0, y: 0, config: {} }, node],
    edges: [
      { source: "q", target: node.id, kind: "data", source_pin: "value", target_pin: "prompt" },
      { source: "q", target: node.id, kind: "data", source_pin: "value", target_pin: "image" },
    ],
  };
}

function setup(node: AutomationGraphNodeDto, patch: Partial<CodeTabProps> = {}) {
  const props: CodeTabProps = {
    workflowId: "w1", node, graph: graphOf(node), byType, workflows: [], pins: nodePins(node, byType.get(node.type)),
    readOnly: false, locked: false, team: false, wide: false, onWide: vi.fn(),
    onNodeChange: vi.fn(), onNodeReplace: vi.fn(), onCodeSession: vi.fn(), onRunNode: vi.fn(),
    ...patch,
  };
  const view = render(<CodeTab {...props} />);
  return { props, view };
}

const builtin = (): AutomationGraphNodeDto => ({ id: "a", type: "llm.ask", label: "Ask", x: 0, y: 0, config: { model: "m1", inputs: { prompt: "Hi" } } });
const custom = (code = "// @ts-check\nexport const node = {};\nexport default async function run(input, ducky) {\n  return {};\n}\n"): AutomationGraphNodeDto => ({
  id: "c", type: "code.js", label: "My code", x: 0, y: 0, config: { ...blankCodeConfig(), code, code_sha: "sha1" },
});

beforeEach(() => {
  api.check_workflow_node_code.mockResolvedValue({ ok: true, problems: [], pins: { exec: true, inputs: [{ id: "text", label: "Text", type: "text" }], outputs: [{ id: "text", label: "Text", type: "text" }] }, settings_spec: [], uses: { tools: [], builtins: [] }, code_sha: "s" });
  api.workflow_code_api.mockResolvedValue({ ok: true, dts: "declare module \"ducky\" { export interface Ducky { log(...values: any[]): void } }" });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("Code tab of a built-in", () => {
  it("shows its generated code read-only, and Edit as custom code says why it can't when it can't", async () => {
    api.get_workflow_node_code.mockResolvedValue(builtinInfo());
    const { view } = setup(builtin());
    const box = await screen.findByRole("textbox", { name: /Code of Ask a model/ }) as HTMLTextAreaElement;
    expect(box.value).toBe(GENERATED);
    expect(box.readOnly).toBe(true);
    expect(screen.getByText(/Built-in · Ask a model/)).toBeTruthy();
    expect(screen.getByText(/the code this step runs with its current settings/)).toBeTruthy();
    expect(api.get_workflow_node_code).toHaveBeenCalledWith("w1", "a");
    const convert = () => screen.getByRole("button", { name: /Edit as custom code/ }) as HTMLButtonElement;
    expect(convert().disabled).toBe(false);

    const cases: Array<[Partial<CodeTabProps>, RegExp]> = [
      [{ locked: true }, /locked/],
      [{ readOnly: true }, /read-only/],
      [{ team: true }, /Local workflows/],
    ];
    for (const [patch, why] of cases) {
      view.rerender(<CodeTab {...setupProps(builtin(), patch)} />);
      expect(convert().disabled).toBe(true);
      expect(convert().title).toMatch(why);
    }
  });

  it("can't convert a flow step and says so", async () => {
    api.get_workflow_node_code.mockResolvedValue(builtinInfo({ kind: "flow", convertible: false, reason: "Branch chooses a route, so it stays a built-in." }));
    setup(builtin());
    await screen.findByRole("textbox", { name: /Code of/ });
    const button = screen.getByRole("button", { name: /Edit as custom code/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toBe("Branch chooses a route, so it stays a built-in.");
  });

  it("asks first, lists the data wires that would drop, then converts in the draft", async () => {
    api.get_workflow_node_code.mockResolvedValue(builtinInfo());
    const { props } = setup(builtin());
    await screen.findByRole("textbox", { name: /Code of/ });
    fireEvent.click(screen.getByRole("button", { name: /Edit as custom code/ }));
    const dialog = screen.getByRole("dialog", { name: "Edit as custom code" });
    expect(dialog.textContent).toContain("This node becomes Custom code in this workflow only. The built-in stays the same everywhere else.");
    // The generated code declares no image input: that wire would be disconnected.
    expect(within(dialog).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["Question.value → Ask.image"]);
    expect(props.onNodeReplace).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Convert" }));
    expect(props.onNodeReplace).toHaveBeenCalledTimes(1);
    const [next, label] = vi.mocked(props.onNodeReplace).mock.calls[0];
    expect(label).toBe("Edit as custom code");
    expect(next).toEqual(expect.objectContaining({ id: "a", type: "code.js", label: "Ask" }));
    expect(next.config).toEqual(expect.objectContaining({ code: GENERATED, inputs: { prompt: "Hi" }, based_on: { type: "llm.ask", config: builtin().config, code_sha: "gen1" } }));
  });

  it("cancel leaves it as it is", async () => {
    api.get_workflow_node_code.mockResolvedValue(builtinInfo());
    const { props } = setup(builtin());
    await screen.findByRole("textbox", { name: /Code of/ });
    fireEvent.click(screen.getByRole("button", { name: /Edit as custom code/ }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(props.onNodeReplace).not.toHaveBeenCalled();
  });
});

function setupProps(node: AutomationGraphNodeDto, patch: Partial<CodeTabProps> = {}): CodeTabProps {
  return {
    workflowId: "w1", node, graph: graphOf(node), byType, workflows: [], pins: nodePins(node, byType.get(node.type)),
    readOnly: false, locked: false, team: false, wide: false, onWide: vi.fn(),
    onNodeChange: vi.fn(), onNodeReplace: vi.fn(), onCodeSession: vi.fn(), onRunNode: vi.fn(), ...patch,
  };
}

describe("Code tab of a custom code node", () => {
  it("puts typed code into the draft as one undo step per focus, then checks it", async () => {
    const node = custom();
    api.get_workflow_node_code.mockResolvedValue({ ...builtinInfo(), kind: "custom", code: node.config.code, code_sha: "sha1" });
    const { props } = setup(node);
    const box = screen.getByRole("textbox", { name: "Code" }) as HTMLTextAreaElement;
    expect(box.value).toBe(node.config.code);
    fireEvent.focus(box);
    expect(props.onCodeSession).toHaveBeenCalledWith("begin", "c");
    fireEvent.change(box, { target: { value: `${node.config.code}// more\n` } });
    await waitFor(() => expect(props.onNodeChange).toHaveBeenCalled());
    // Opening it changed nothing: the check found the pins the node already has.
    expect(props.onNodeChange).toHaveBeenCalledTimes(1);
    const [next, label] = vi.mocked(props.onNodeChange).mock.calls[0];
    expect(label).toBe("Edit code");
    expect(next.config.code).toBe(`${node.config.code}// more\n`);
    await waitFor(() => expect(api.check_workflow_node_code).toHaveBeenCalledWith(`${node.config.code}// more\n`));
    fireEvent.blur(box);
    expect(props.onCodeSession).toHaveBeenLastCalledWith("end", "c");
  });

  it("Run unsaved code sends the code in the editor and shows the line that failed", async () => {
    const node = custom();
    api.get_workflow_node_code.mockResolvedValue({ ...builtinInfo(), kind: "custom", code: node.config.code, code_sha: "sha1", last_inputs: { text: "from last run", other: 1 } });
    api.test_workflow_node.mockResolvedValue({ ok: false, outputs: {}, log: "Got hi", tool_calls: [{ name: "ducky_terminal_run", args: { command: "dir" }, dry_run: true }], error: { line: 3, col: 5, message: "boom" }, ms: 12 });
    setup(node, { pins: { exec: true, inputs: [{ id: "text", label: "Text", type: "text" }], outputs: [{ id: "text", label: "Text", type: "text" }] } });
    const draft = "// @ts-check\nexport const node = {};\nthrow new Error('boom');\n";
    fireEvent.change(screen.getByRole("textbox", { name: "Code" }), { target: { value: draft } });
    await waitFor(() => expect((screen.getByRole("textbox", { name: "Text" }) as HTMLTextAreaElement).value).toBe("from last run"));
    expect(screen.getByRole("switch", { name: "Dry run" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /Run unsaved code/ }));
    await waitFor(() => expect(api.test_workflow_node).toHaveBeenCalledWith("w1", "c", draft, { text: "from last run" }, {}, true));
    const result = await screen.findByRole("status", { name: "Test result" });
    expect(within(result).getByRole("button", { name: "Line 3: boom" })).toBeTruthy();
    expect(result.textContent).toContain("Failed in 12 ms");
    expect(result.textContent).toContain("ducky_terminal_run");
    expect(result.textContent).toContain("dry run, not made");
    expect(result.textContent).toContain("Got hi");
    // The error also counts as a problem on the chip.
    expect(screen.getByRole("button", { name: /1 problem/ })).toBeTruthy();
  });

  it("asks for a Review when an agent changed the code, and Review approves it", async () => {
    const node = custom();
    api.get_workflow_node_code.mockResolvedValue({ ...builtinInfo(), kind: "custom", code: node.config.code, code_sha: "agent-sha", approved: false });
    api.approve_workflow_node_code.mockResolvedValue({ ok: true });
    setup(node);
    const review = await screen.findByRole("button", { name: /Review/ });
    expect(screen.getByText(/An agent changed this code/)).toBeTruthy();
    await act(async () => { fireEvent.click(review); });
    expect(api.approve_workflow_node_code).toHaveBeenCalledWith("w1", "c", "agent-sha");
    await waitFor(() => expect(screen.queryByRole("button", { name: /Review/ })).toBeNull());
  });

  it("no Review once it is approved", async () => {
    const node = custom();
    api.get_workflow_node_code.mockResolvedValue({ ...builtinInfo(), kind: "custom", code: node.config.code, code_sha: "sha1", approved: true });
    setup(node);
    await waitFor(() => expect(api.get_workflow_node_code).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByRole("button", { name: /Review/ })).toBeNull();
  });

  it("shows the pins the code declares against the saved ones, and the wires that would drop", async () => {
    const node = custom();
    api.get_workflow_node_code.mockResolvedValue({ ...builtinInfo(), kind: "custom", code: node.config.code, code_sha: "sha1",
      pins: { exec: true, inputs: [{ id: "prompt", label: "Prompt", type: "text" }, { id: "image", label: "Image", type: "image" }], outputs: [{ id: "text", label: "Text", type: "text" }] } });
    setup(node, { pins: { exec: true, inputs: [{ id: "prompt", label: "Prompt", type: "text" }], outputs: [{ id: "text", label: "Text", type: "text" }] } });
    await waitFor(() => expect(screen.getByText("1 wire will be disconnected")).toBeTruthy());
    expect(screen.getByText("− input “image”")).toBeTruthy();
  });

  it("revert asks first and lists the wires the built-in can't take", async () => {
    const based = { ...custom(), id: "a" };
    based.config = { ...based.config, based_on: { type: "llm.ask", config: { model: "m1" }, code_sha: "gen1" }, pins: { exec: true, inputs: [{ id: "prompt", label: "Prompt", type: "text" }, { id: "extra", label: "Extra", type: "text" }], outputs: [] } };
    api.get_workflow_node_code.mockResolvedValue({ ...builtinInfo(), kind: "custom", code: based.config.code, code_sha: "sha1", based_on: based.config.based_on as WorkflowNodeCodeDto["based_on"], based_on_code: GENERATED });
    const graph = graphOf(based);
    graph.edges.push({ source: "q", target: "a", kind: "data", source_pin: "value", target_pin: "extra" });
    const { props } = setup(based, { graph });
    expect(screen.getByText(/made from Ask a model/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Revert to built-in/ }));
    const dialog = screen.getByRole("dialog", { name: "Revert to built-in" });
    expect(within(dialog).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["Question.value → My code.extra"]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Revert" }));
    expect(props.onNodeReplace).toHaveBeenCalledWith(expect.objectContaining({ id: "a", type: "llm.ask", config: { model: "m1" } }), "Revert to built-in");
  });

  it("Compare shows the changes from the built-in's code", async () => {
    const based = custom("// mine\nexport default async function run() { return {}; }\n");
    based.config = { ...based.config, based_on: { type: "llm.ask", config: {}, code_sha: "gen1" } };
    api.get_workflow_node_code.mockResolvedValue({ ...builtinInfo(), kind: "custom", code: based.config.code, code_sha: "sha1", based_on: based.config.based_on as WorkflowNodeCodeDto["based_on"], based_on_code: GENERATED });
    setup(based);
    const compare = await screen.findByRole("button", { name: /Compare with built-in/ });
    await waitFor(() => expect((compare as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(compare);
    const diff = screen.getByLabelText("Changes from the built-in");
    expect(diff.querySelector(".is-added")?.textContent).toContain("// mine");
    expect(diff.querySelector(".is-removed")?.textContent).toContain("Built-in Ask a model");
  });
});
