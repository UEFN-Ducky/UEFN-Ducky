// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AutomationsView } from "./AutomationsView";
import { applyWorkflowEvent, resetWorkflowRunsForTests } from "../hooks/workflowRunsByChat";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { setUnsavedWorkflow } from "./unsavedWorkflow";
import type { AutomationDto, WorkflowOwnerDto, WorkflowOwnersDto } from "../types/panel";

const api = vi.hoisted(() => ({ stop_workflow: vi.fn(), list_workflow_versions: vi.fn(), get_workflow_version: vi.fn(), list_workflows: vi.fn(), list_workflow_nodes: vi.fn(), get_workflow: vi.fn(), save_workflow: vi.fn(), run_workflow: vi.fn(), delete_workflow: vi.fn(), workflow_owners: vi.fn(), workflow_sync: vi.fn(), copy_workflow: vi.fn(), set_workflow_run_here: vi.fn(), import_local_workflows: vi.fn(), workflow_open_web: vi.fn(), list_recent_projects: vi.fn(), set_project_root: vi.fn(), list_agent_profiles: vi.fn(), list_all_conversations: vi.fn(), get_mcp_tools_catalog: vi.fn(), get_workflow_tools_catalog: vi.fn(), set_workflow_folder: vi.fn(), move_workflow_folder: vi.fn(), add_workflow_folder: vi.fn(), copy_workflow_folder: vi.fn(), use_workflow_template: vi.fn(), clear_workflow_runs: vi.fn(), run_workflow_node: vi.fn(), get_workflow_node_code: vi.fn(), check_workflow_node_code: vi.fn(), workflow_code_api: vi.fn(), test_workflow_node: vi.fn(), approve_workflow_node_code: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("./AutomationTemplatePicker", () => ({
  AutomationTemplatePicker: ({ open, owners, ownerId, onOwnerChange, onSelect, folder, onFolderChange, saveFolder }: {
    open: boolean;
    owners?: { id: string; kind: string; label: string; readOnly?: boolean }[];
    ownerId?: string;
    onOwnerChange?: (id: string) => void;
    onSelect: (template: { id: string; name: string; shape?: string; graph: { nodes: never[]; edges: never[] } } | null) => void;
    folder?: string;
    onFolderChange?: (folder: string) => void;
    saveFolder?: { ownerId: string; path: string } | null;
  }) => open ? (
    <>
      {(owners || []).map((owner) => (
        <button key={owner.id} type="button" aria-pressed={owner.id === ownerId} disabled={!!owner.readOnly} onClick={() => onOwnerChange?.(owner.id)}>
          {`Save in ${owner.kind === "team" ? `Team · ${owner.label}` : "Local"}`}
        </button>
      ))}
      {saveFolder ? <p>{`Keep ${saveFolder.ownerId}/${saveFolder.path} as a template`}</p> : null}
      <p>{`Goes in “${folder || ""}”`}</p>
      <button type="button" onClick={() => onFolderChange?.("Tests")}>Put it in Tests</button>
      <button type="button" onClick={() => onSelect({ id: "custom:kit12345", name: "Kit", shape: "bundle", graph: { nodes: [], edges: [] } })}>Use folder template</button>
      <button type="button" onClick={() => onSelect(null)}>Create workflow</button>
    </>
  ) : null,
}));

const LOCAL: WorkflowOwnerDto = { id: "local", kind: "local", label: "Local", state: "ok", readOnly: false, reason: "" };
let TEAM: WorkflowOwnerDto;
let owners: WorkflowOwnersDto;
let saved: AutomationDto;
let daily: AutomationDto;
beforeEach(() => {
  class TestPointerEvent extends MouseEvent {
    pointerId: number;
    pointerType: string;
    constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; this.pointerType = init.pointerType || "mouse"; }
  }
  vi.stubGlobal("PointerEvent", TestPointerEvent);
  HTMLElement.prototype.setPointerCapture = vi.fn();
  window.localStorage.clear();  // where-you-were and panel widths are kept per PC
  // Drags here check exact distances; snapping to the grid has its own test.
  window.localStorage.setItem("ducky.workflows.view.v1", JSON.stringify({ snap: false }));
  document.elementsFromPoint = vi.fn(() => []);
  TEAM = { id: "teamT", kind: "team", label: "Alpha Studio", state: "ok", readOnly: false, reason: "", slug: "alpha", sync: { state: "ok", pending: 0, syncedAt: null, members: 3 } };
  owners = { ok: true, owners: [LOCAL, TEAM], signedIn: true, teamsEnabled: true, localImport: 0 };
  saved = { id: "p", name: "Example", enabled: true, owner: LOCAL, graph: {
    nodes: [
      { id: "s", type: "start.chat", label: "Chat", x: 0, y: 0, config: {} },
      { id: "a", type: "flow.wait", label: "Pause", description: "Pause description", x: 280, y: 0, config: {} },
      { id: "b", type: "flow.wait", label: "Continue", x: 560, y: 0, config: {} },
    ], edges: [{ source: "s", target: "a", kind: "main" }],
  } };
  daily = { id: "d", name: "Daily check", enabled: true, owner: TEAM, run_here: false, graph: { nodes: [{ id: "timer", type: "start.cron", x: 0, y: 0, config: {} }], edges: [] } };
  api.list_workflow_versions.mockResolvedValue({ ok: true, versions: [] });
  api.get_workflow_version.mockResolvedValue({ ok: false });
  api.get_mcp_tools_catalog.mockResolvedValue({ tools: [] });
  api.get_workflow_tools_catalog.mockResolvedValue({ tools: [] });
  api.list_workflows.mockImplementation(async () => ({ workflows: [
    { id: "p", name: "Example", enabled: true, owner: saved.owner, trigger: { kind: "chat", label: "Chat" } },
    { id: "d", name: "Daily check", enabled: true, owner: daily.owner, run_here: daily.run_here, trigger: { kind: "schedule", label: "Every 5m" } },
  ] }));
  api.workflow_owners.mockImplementation(async () => structuredClone(owners));
  api.workflow_sync.mockResolvedValue({ ok: true, started: true });
  api.list_workflow_nodes.mockResolvedValue({ nodes: [{ type: "start.chat", label: "Chat", role: "starter", group: "Starting" }, { type: "start.cron", label: "Schedule", role: "starter", group: "Starting" }, { type: "flow.wait", label: "Wait", group: "Logic", config_fields: [{ id: "seconds", label: "Seconds", type: "number" }] }] });
  api.get_workflow.mockImplementation(async (id: string) => ({ workflow: structuredClone(id === "d" ? daily : saved) }));
  api.save_workflow.mockImplementation(async (doc: AutomationDto, owner?: string) => {
    if (!doc.id) return { workflow: { ...structuredClone(doc), id: "new-workflow", owner: owner === "teamT" ? TEAM : LOCAL } };
    if (doc.id === "d") { daily = structuredClone(doc); return { workflow: daily }; }
    saved = structuredClone(doc);
    return { workflow: saved };
  });
  api.run_workflow.mockResolvedValue({ ok: true, steps: [] });
  api.delete_workflow.mockResolvedValue({ ok: true });
  api.copy_workflow.mockImplementation(async (id: string, owner: string) => ({ workflow: { ...structuredClone(id === "d" ? daily : saved), owner: owner === "teamT" ? TEAM : LOCAL } }));
  api.set_workflow_run_here.mockImplementation(async (_id: string, on: boolean) => ({ workflow: { ...structuredClone(daily), run_here: on } }));
  api.import_local_workflows.mockResolvedValue({ ok: true, moved: 2 });
  api.list_agent_profiles.mockResolvedValue({ profiles: [{ id: "artist", name: "My Artist", ducky_style: "artist" }], template_profiles: [{ id: "artist", name: "Original Artist" }, { id: "coder", name: "Verse Coder", ducky_style: "hacker" }], blank_profile_id: "__blank__" });
  api.list_all_conversations.mockResolvedValue([{ id: "existing", title: "Build my island", ducky_name: "Level Designer", project_name: "Island Two" }, { id: "hub", title: "Group hub", is_group: true }]);
  api.list_recent_projects.mockResolvedValue([
    { path: "C:/Projects/FirstIsland", name: "First Island", slug: "first", active: true },
    { path: "C:/Projects/SecondIsland", name: "Second Island", slug: "second", active: false },
  ]);
});
afterEach(() => { cleanup(); resetWorkflowRunsForTests(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it("restores concurrent run controls on opening and stops only the chosen run", async () => {
  api.stop_workflow.mockResolvedValue({ ok: true, stopped: true });
  act(() => {
    for (const run of ["first", "second"]) {
      applyWorkflowEvent({ type: "workflow_run", id: "p", run, state: "started", conv: "chat", name: "Example" });
      applyWorkflowEvent({ type: "workflow_step", id: "p", run, node: "a", state: "running", label: "Pause" });
    }
  });
  await open();
  expect(screen.getByRole("button", { name: "Stop", exact: true })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Test", exact: true }).hasAttribute("disabled")).toBe(false);
  fireEvent.change(screen.getByLabelText("Workflow run"), { target: { value: "first" } });
  fireEvent.click(screen.getByRole("button", { name: "Stop", exact: true }));
  await waitFor(() => expect(api.stop_workflow).toHaveBeenCalledWith("p", "first"));
  act(() => applyWorkflowEvent({ type: "workflow_run", id: "p", run: "first", state: "stopped" }));
  fireEvent.change(screen.getByLabelText("Workflow run"), { target: { value: "second" } });
  expect(screen.getByRole("button", { name: "Stop", exact: true })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Test", exact: true }));
  await waitFor(() => expect(api.run_workflow).toHaveBeenCalledWith("p"));
});

function renderView() {
  return render(<ConfirmModalProvider><AutomationsView /></ConfirmModalProvider>);
}
async function open(name = "Example") {
  const view = renderView();
  fireEvent.click(await screen.findByText(name));
  if (name === "Example") await screen.findByRole("button", { name: "Connect from Pause" });
  else await screen.findByDisplayValue(name);
  return view;
}
async function save() { fireEvent.click(screen.getByRole("button", { name: "Save", exact: true })); await waitFor(() => expect(api.save_workflow).toHaveBeenCalled()); }
/** Click a card: it becomes the selection and its details slide in on the right. */
function editNode(id = "a") {
  fireEvent.pointerDown(document.querySelector(`[data-aw-node="${id}"] .aw-node-card`)!, { button: 0, pointerId: 9 });
  fireEvent.pointerUp(document.querySelector(".aw-board")!, { button: 0, pointerId: 9 });
}
const details = () => screen.queryByRole("complementary", { name: "Details" });
/** The details panel's tabs, one per selected item (not a node's Settings / Code). */
const selectedTabs = () => within(screen.getByRole("tablist", { name: "Selected items" })).getAllByRole("tab");
/** Details header: Edit, type into the text itself, then Save (or leave it editing). */
function editText(fields: { name?: string; description?: string }, save = true) {
  const panel = details()!;
  fireEvent.click(within(panel).getByRole("button", { name: "Edit" }));
  if (fields.name !== undefined) within(panel).getByRole("textbox", { name: /name$/ }).textContent = fields.name;
  if (fields.description !== undefined) within(panel).getByRole("textbox", { name: "Description" }).textContent = fields.description;
  if (save) fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
}
function dropdown(name: string) { fireEvent.click(screen.getByRole("button", { name, exact: true })); }
/** The app's confirm dialog: press its main button ("Delete (Enter)" and so on). */
async function confirmIt(label: string) {
  const name = new RegExp(`^${label} \\(`);
  fireEvent.click(await screen.findByRole("button", { name }));
  await waitFor(() => expect(screen.queryByRole("button", { name })).toBeNull());
  await act(async () => {});  // what was confirmed runs right after the dialog closes
}
function zoomBy(label: "Zoom in" | "Zoom out" | "10%" | "25%") {
  if (!screen.queryByRole("menu", { name: "Zoom" })) dropdown("Zoom");  // it stays open between picks
  fireEvent.click(within(screen.getByRole("menu", { name: "Zoom" })).getByLabelText(label));
}
function wire(from: string, to: string, cancel = false) {
  const source = screen.getByRole("button", { name: from });
  const target = screen.getByRole("button", { name: to });
  vi.mocked(document.elementsFromPoint).mockReturnValue([target]);
  fireEvent.pointerDown(source, { button: 0, pointerId: 1 });
  if (cancel) fireEvent.pointerCancel(source, { pointerId: 1 });
  else fireEvent.pointerUp(source, { pointerId: 1, clientX: 600, clientY: 80 });
}

describe("workflow editor interactions", () => {
  async function openAgentNode(config: Record<string, unknown> = {}) {
    saved.graph.nodes[1].type = "pipeline.agent";
    saved.graph.nodes[1].config = config;
    api.list_workflow_nodes.mockResolvedValue({ nodes: [
      { type: "start.chat", label: "Chat", role: "starter", group: "Starting" },
      { type: "pipeline.agent", label: "Agent", group: "Agents", config_fields: [{ id: "ducky", label: "Assign ducky", type: "ducky" }] },
    ] });
    await open();
    editNode();
  }
  it("lists existing duckies across projects, saved profiles and templates without duplicates", async () => {
    await openAgentNode();
    dropdown("Assign ducky");
    await screen.findByRole("radio", { name: "My Artist" });
    expect(screen.getByRole("radio", { name: "Verse Coder" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Level Designer — Build my island (Island Two)" })).toBeTruthy();
    expect(screen.queryByRole("radio", { name: "Original Artist" })).toBeNull();
    expect(screen.queryByRole("radio", { name: "Group hub" })).toBeNull();
    expect(screen.queryByText("Node appearance")).toBeNull();
    expect(screen.queryByLabelText("Label")).toBeNull();
    expect(api.list_all_conversations).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole("radio", { name: "Level Designer — Build my island (Island Two)" }));
    await save();
    expect(saved.graph.nodes[1].config.ducky).toBe("chat:existing");
    fireEvent.click(screen.getByText("Example"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Assign ducky" })).toBeNull());
    editNode();
    dropdown("Assign ducky");
    await waitFor(() => expect((screen.getByRole("radio", { name: "Level Designer — Build my island (Island Two)" }) as HTMLInputElement).checked).toBe(true));
  });
  it("saves create-at-run-time explicitly so it overrides an older profile assignment", async () => {
    await openAgentNode({ profile_id: "artist", prompt: "Build", model: "saved-model" });
    dropdown("Assign ducky");
    await waitFor(() => expect((screen.getByRole("radio", { name: "My Artist" }) as HTMLInputElement).checked).toBe(true));
    fireEvent.click(screen.getByRole("radio", { name: "Create new when workflow runs" }));
    await save();
    expect(saved.graph.nodes[1].config).toEqual({ profile_id: "artist", prompt: "Build", model: "saved-model", ducky: "__new__" });
  });
  it("shows legacy profile names as a named selection and saves template choices", async () => {
    await openAgentNode({ ducky: "My Artist" });
    dropdown("Assign ducky");
    await waitFor(() => expect((screen.getByRole("radio", { name: "My Artist" }) as HTMLInputElement).checked).toBe(true));
    fireEvent.click(screen.getByRole("radio", { name: "Verse Coder" }));
    await save();
    expect(saved.graph.nodes[1].config.ducky).toBe("coder");
  });
  it("preserves unavailable assignments and still lists profiles when chat loading fails", async () => {
    api.list_all_conversations.mockRejectedValue(new Error("offline"));
    await openAgentNode({ ducky: "chat:missing" });
    dropdown("Assign ducky");
    await screen.findByText("Some duckies could not be loaded — reopen to retry");
    expect(screen.getByRole("radio", { name: "My Artist" })).toBeTruthy();
    expect((screen.getByRole("radio", { name: "Saved assignment — chat:missing" }) as HTMLInputElement).checked).toBe(true);
    api.list_all_conversations.mockResolvedValue([{ id: "existing", title: "Build my island", ducky_name: "Level Designer", project_name: "Island Two" }]);
    dropdown("Assign ducky");
    dropdown("Assign ducky");
    await screen.findByRole("radio", { name: "Level Designer — Build my island (Island Two)" });
    await save();
    expect(saved.graph.nodes[1].config.ducky).toBe("chat:missing");
  });
  it("defaults to creating a ducky at run time even with an empty library", async () => {
    api.list_agent_profiles.mockResolvedValue({ profiles: [], template_profiles: [], blank_profile_id: "__blank__" });
    api.list_all_conversations.mockResolvedValue([]);
    await openAgentNode();
    dropdown("Assign ducky");
    expect((screen.getByRole("radio", { name: "Create new when workflow runs" }) as HTMLInputElement).checked).toBe(true);
  });
  async function openProjectNode(project = "") {
    saved.graph.nodes[1].type = "uefn.open_project";
    saved.graph.nodes[1].config = { project, timeout: 180 };
    api.list_workflow_nodes.mockResolvedValue({ nodes: [
      { type: "start.chat", label: "Chat", role: "starter", group: "Starting" },
      { type: "uefn.open_project", label: "Open UEFN project", group: "UEFN", config_fields: [{ id: "project", label: "UEFN project", type: "project" }] },
    ] });
    await open();
    editNode();
  }
  it("selects a saved UEFN project, preserves it on reload, and does not switch the active project while editing", async () => {
    await openProjectNode();
    dropdown("UEFN project");
    await screen.findByRole("radio", { name: /^Second Island/ });
    expect((screen.getByRole("radio", { name: "Current project (at run time)" }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: /^Second Island/ }));
    await save();
    expect(saved.graph.nodes[1].config).toEqual({ project: "C:/Projects/SecondIsland", timeout: 180 });
    expect(api.set_project_root).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Example"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "UEFN project" })).toBeNull());
    editNode();
    dropdown("UEFN project");
    await waitFor(() => expect((screen.getByRole("radio", { name: /^Second Island/ }) as HTMLInputElement).checked).toBe(true));
  });
  it("preserves a previously typed project path when it is absent from saved projects", async () => {
    await openProjectNode("D:/Existing/Island.uefnproject");
    dropdown("UEFN project");
    await screen.findByRole("radio", { name: /^Second Island/ });
    expect((screen.getByRole("radio", { name: "Saved project — D:/Existing/Island.uefnproject" }) as HTMLInputElement).checked).toBe(true);
    await save();
    expect(saved.graph.nodes[1].config.project).toBe("D:/Existing/Island.uefnproject");
  });
  it("keeps the saved target on list errors and retries when the dropdown is reopened", async () => {
    api.list_recent_projects.mockRejectedValue(new Error("offline"));
    await openProjectNode("C:/Projects/SecondIsland");
    dropdown("UEFN project");
    await screen.findByText("Could not load projects — reopen to retry");
    expect((screen.getByRole("radio", { name: "Saved project — C:/Projects/SecondIsland" }) as HTMLInputElement).checked).toBe(true);
    api.list_recent_projects.mockResolvedValue([{ path: "C:/Projects/SecondIsland", name: "Second Island" }]);
    dropdown("UEFN project");
    dropdown("UEFN project");
    await screen.findByRole("radio", { name: /^Second Island/ });
  });
  it("explains an empty project list and retains the current-project default", async () => {
    api.list_recent_projects.mockResolvedValue([]);
    await openProjectNode();
    dropdown("UEFN project");
    await screen.findByText("No saved projects — add one in Ducky’s project menu");
    expect((screen.getByRole("radio", { name: "Current project (at run time)" }) as HTMLInputElement).checked).toBe(true);
  });
  it("connects output to input, prevents duplicates, and saves the graph", async () => {
    await open();
    wire("Connect from Pause", "Connect to Continue");
    wire("Connect from Pause", "Connect to Continue");
    await save();
    expect(saved.graph.edges).toHaveLength(2);
    expect(saved.graph.edges[1]).toEqual({ source: "a", target: "b", kind: "main" });
  });
  it("supports reverse dragging from input to output", async () => {
    await open();
    wire("Connect to Continue", "Connect from Pause");
    await save();
    expect(saved.graph.edges[1]).toEqual({ source: "a", target: "b", kind: "main" });
  });
  it("rejects same-direction connections and cancels interrupted drags", async () => {
    await open();
    wire("Connect from Pause", "Connect from Continue");
    wire("Connect from Pause", "Connect to Continue", true);
    await save();
    expect(saved.graph.edges).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Connect to Chat input" })).toBeNull();
  });
  it("orders the top bar from Delete to Play, with a green or red light for on and off", async () => {
    await open();
    const bar = screen.getByRole("toolbar", { name: "Workflow actions" });
    const labels = [...bar.querySelectorAll(".aw-toolbar-actions > button, .aw-toolbar-actions .choice-dropdown-trigger")].map((el) => el.getAttribute("aria-label"));
    expect(labels).toEqual(["Delete", "Undo", "Redo", "History", "Move or copy", "Duplicate", "Save", "Enabled", "Test"]);
    expect(bar.querySelector(".aw-light")?.classList.contains("is-on")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Enabled" }));
    expect(bar.querySelector(".aw-light")?.classList.contains("is-on")).toBe(false);
  });
  it("keeps the add-node menu to a search and the node groups, each with its count", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Add nodes" }));
    const menu = screen.getByRole("dialog", { name: "Add node" });
    expect(within(menu).queryByRole("button", { name: "Arrange nodes" })).toBeNull();
    expect(within(menu).queryByRole("button", { name: "Fit graph" })).toBeNull();
    expect([...menu.querySelectorAll(".aw-acc summary")].map((el) => [el.querySelector(".aw-acc-name")?.textContent, el.querySelector(".aw-acc-count")?.textContent])).toEqual([["Starting", "2"], ["Logic", "1"]]);
  });
  it("keeps every card the same size with nothing to expand, resize or delete on it", async () => {
    await open();
    const card = document.querySelector('[data-aw-node="a"]')!;
    expect(card.querySelectorAll("button")).toHaveLength(2);  // the two ports
    expect(card.querySelector(".aw-node-title strong")?.textContent).toBe("Pause");
    expect(card.querySelector(".aw-node-title .aw-node-icon")).toBeNull();
    expect(card.querySelector(".aw-node-mark .aw-node-icon")).toBeTruthy();  // the icon is a big mark in the card's background
    expect(card.querySelector(".aw-node-sub")?.textContent).toBe("Pause description");
    expect(details()).toBeNull();
  });
  it("hides card descriptions at overview zoom and restores them", async () => {
    const { container } = await open();
    for (let i = 0; i < 3; i++) zoomBy("Zoom out");
    expect(container.querySelector(".aw-board")?.classList.contains("is-overview")).toBe(false);  // 58%: still full cards
    zoomBy("Zoom out");
    expect(container.querySelector(".aw-board")?.classList.contains("is-overview")).toBe(true);
    expect(screen.queryByText("Pause description")).toBeNull();
    for (let i = 0; i < 4; i++) zoomBy("Zoom in");
    expect(screen.getByText("Pause description")).toBeTruthy();
    fireEvent.contextMenu(container.querySelector(".aw-board")!, { clientX: 200, clientY: 200 });
    expect(screen.getByRole("dialog", { name: "Add node" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Collapse all nodes" })).toBeNull();
  });
  it("selects a connection without deleting it and edits its route in the details panel", async () => {
    const { container } = await open();
    fireEvent.click(container.querySelector(".aw-wire")!);
    expect(details()?.textContent).toContain("Chat input → Pause");
    dropdown("Connection route");
    fireEvent.click(screen.getByRole("radio", { name: "False", exact: true }));
    await save();
    expect(saved.graph.edges).toEqual([{ source: "s", target: "a", kind: "false" }]);
    api.save_workflow.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await confirmIt("Remove");
    await save();
    expect(saved.graph.edges).toEqual([]);
    expect(details()).toBeNull();
  });
  it("names a Repeat until's loop wire Each try", async () => {
    saved.graph.nodes[0].type = "flow.repeat";
    const { container } = await open();
    fireEvent.click(container.querySelector(".aw-wire")!);
    dropdown("Connection route");
    expect(screen.queryByRole("radio", { name: "Each item", exact: true })).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Each try", exact: true }));
    await save();
    expect(saved.graph.edges).toEqual([{ source: "s", target: "a", kind: "each" }]);
  });
  it("animates a pulse along every wire and moves the grid with the view", async () => {
    const { container } = await open();
    const wire = container.querySelector(".aw-edge .aw-wire")!;
    const motion = container.querySelector(".aw-edge .aw-edge-arrow animateMotion")!;
    expect(motion.getAttribute("path")).toBe(wire.getAttribute("d"));
    expect(motion.getAttribute("repeatCount")).toBe("indefinite");
    const board = container.querySelector(".aw-board") as HTMLElement;
    expect(board.style.getPropertyValue("--aw-pan-x")).toBe("280px");
    zoomBy("Zoom in");
    expect(board.style.getPropertyValue("--aw-grid")).toBe("calc(var(--wf-grid-size) * 1.2)");
    expect(container.querySelector(".aw-edge")?.classList.contains("aw-edge--from-starter")).toBe(true);  // colored by the node it leaves
    expect(container.querySelector('[data-aw-node="a"] .aw-port--in')?.classList.contains("is-linked")).toBe(true);
    expect(container.querySelector('[data-aw-node="a"] .aw-port--out')?.classList.contains("is-linked")).toBe(false);
  });
  it.each([0, 1, 2])("pans empty canvas with mouse button %s", async (button) => {
    const { container } = await open();
    const board = container.querySelector(".aw-board")!;
    fireEvent.pointerDown(board, { button, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(board, { clientX: 180, clientY: 150 });
    fireEvent.pointerUp(board, { button, clientX: 180, clientY: 150 });
    expect((container.querySelector(".aw-world") as HTMLElement).style.transform).toBe("translate(360px, 210px) scale(1)");
    if (button === 2) {
      fireEvent.contextMenu(board, { clientX: 180, clientY: 150 });
      expect(screen.queryByRole("dialog", { name: "Add node" })).toBeNull();
    }
  });
  it("keeps the selection and its details while the canvas is dragged, and clears them on a plain click", async () => {
    const { container } = await open();
    const board = container.querySelector(".aw-board")!;
    const isSelected = () => document.querySelector('[data-aw-node="a"]')?.classList.contains("is-selected");
    editNode();
    fireEvent.pointerDown(board, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(board, { clientX: 180, clientY: 150 });
    fireEvent.pointerUp(board, { button: 0, clientX: 180, clientY: 150 });
    expect((container.querySelector(".aw-world") as HTMLElement).style.transform).toBe("translate(360px, 210px) scale(1)");
    expect(isSelected()).toBe(true);
    expect(details()).toBeTruthy();
    fireEvent.pointerDown(board, { button: 0, clientX: 300, clientY: 300 });
    fireEvent.pointerUp(board, { button: 0, clientX: 300, clientY: 300 });
    expect(isSelected()).toBe(false);
    expect(details()).toBeNull();
    fireEvent.click(container.querySelector(".aw-wire")!);
    fireEvent.pointerDown(board, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(board, { clientX: 140, clientY: 100 });
    fireEvent.pointerUp(board, { button: 0, clientX: 140, clientY: 100 });
    expect(details()?.textContent).toContain("Connection");
  });
  it("drops a wire on empty canvas to add a node that is already connected", async () => {
    await open();
    vi.mocked(document.elementsFromPoint).mockReturnValue([]);
    const out = screen.getByRole("button", { name: "Connect from Continue" });
    fireEvent.pointerDown(out, { button: 0, pointerId: 1 });
    fireEvent.pointerUp(out, { pointerId: 1, clientX: 900, clientY: 300 });
    const menu = screen.getByRole("dialog", { name: "Add node" });
    expect(menu.textContent).toContain("Add a connected node");
    expect([...menu.querySelectorAll(".aw-tile strong")].map((el) => el.textContent)).toEqual(["Wait"]);  // no starts after an output
    fireEvent.click(menu.querySelector(".aw-tile")!);
    await save();
    const added = saved.graph.nodes.at(-1)!;
    expect(added.type).toBe("flow.wait");
    expect(saved.graph.edges.at(-1)).toEqual({ source: "b", target: added.id, kind: "main" });
    expect([added.x, added.y]).toEqual([620, 140 - 52]);  // its input sits where the wire was dropped
    fireEvent.pointerDown(out, { button: 0, pointerId: 2 });
    fireEvent.pointerUp(out, { pointerId: 2, clientX: 1085, clientY: 212 });  // let go on the port itself
    expect(screen.queryByRole("dialog", { name: "Add node" })).toBeNull();
  });
  it("tries the node list again when it could not load, instead of showing nothing", async () => {
    const nodes = await api.list_workflow_nodes();
    api.list_workflow_nodes.mockResolvedValueOnce(undefined);
    await open();
    await waitFor(() => expect(api.list_workflow_nodes).toHaveBeenCalledTimes(2));  // the first try came back empty
    api.list_workflow_nodes.mockResolvedValue(nodes);
    fireEvent.click(screen.getByRole("button", { name: "Add nodes" }));
    const menu = screen.getByRole("dialog", { name: "Add node" });
    await waitFor(() => expect(menu.textContent).toContain("Wait"));
    expect(menu.textContent).not.toContain("No matching nodes");
  });
  it("comes back to the open workflow and where you were looking after the tab is hidden", async () => {
    const { container, unmount } = await open();
    const board = container.querySelector(".aw-board")!;
    fireEvent.pointerDown(board, { button: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(board, { clientX: 150, clientY: 130 });
    fireEvent.pointerUp(board, { button: 1, clientX: 150, clientY: 130 });
    fireEvent.click(screen.getByRole("button", { name: "Workflows", exact: true }));  // fold the list too
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem("ducky.workflows.view.v1") || "{}").cameras?.p).toEqual({ x: 330, y: 190, zoom: 1 }));
    unmount();
    renderView();
    expect(await screen.findByDisplayValue("Example")).toBeTruthy();
    await waitFor(() => expect((document.querySelector(".aw-world") as HTMLElement).style.transform).toBe("translate(330px, 190px) scale(1)"));
    expect(document.querySelector(".aw-root")?.classList.contains("is-list-collapsed")).toBe(true);
  });
  it("colors a node from its panel, and its wires follow", async () => {
    await open();
    editNode("s");
    expect(screen.queryByRole("radio", { name: "Purple" })).toBeNull();  // colors show only while editing
    fireEvent.click(within(details()!).getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("radio", { name: "Purple" }));
    await waitFor(() => expect(saved.graph.nodes[0].color).toBe("purple"));
    expect(document.querySelector('[data-aw-node="s"]')?.classList.contains("aw-tint--purple")).toBe(true);
    expect(document.querySelector(".aw-edge")?.classList.contains("aw-tint--purple")).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: "By kind" }));
    await waitFor(() => expect(saved.graph.nodes[0]).not.toHaveProperty("color"));
  });
  it("has a Select tool that boxes nodes and a Hand tool that moves around, both clicking nodes", async () => {
    const { container } = await open();
    const board = container.querySelector(".aw-board")!;
    const world = () => (container.querySelector(".aw-world") as HTMLElement).style.transform;
    const selected = () => [...document.querySelectorAll(".aw-node.is-selected")].map((el) => el.getAttribute("data-aw-node"));
    expect(screen.getByRole("button", { name: "Hand tool" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Select tool" }));
    fireEvent.pointerDown(board, { button: 0, pointerId: 3, clientX: 260, clientY: 140 });
    fireEvent.pointerMove(board, { pointerId: 3, clientX: 810, clientY: 260 });
    fireEvent.pointerUp(board, { pointerId: 3, clientX: 810, clientY: 260 });
    expect(selected()).toEqual(["s", "a"]);
    expect(world()).toBe("translate(280px, 160px) scale(1)");  // boxing never pans
    editNode("b");  // cards still click and drag
    expect(selected()).toEqual(["b"]);
    fireEvent.keyDown(board, { key: "h" });
    expect(screen.getByRole("button", { name: "Hand tool" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.pointerDown(board, { button: 0, pointerId: 4, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(board, { pointerId: 4, clientX: 140, clientY: 120 });
    fireEvent.pointerUp(board, { pointerId: 4, clientX: 140, clientY: 120 });
    expect(world()).toBe("translate(320px, 180px) scale(1)");
    expect(selected()).toEqual(["b"]);
    fireEvent.keyDown(board, { key: "v" });
    fireEvent.keyDown(board, { key: " " });  // holding Space is Hand for a moment
    expect(screen.getByRole("button", { name: "Hand tool" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyUp(window, { key: " " });
    expect(screen.getByRole("button", { name: "Select tool" }).getAttribute("aria-pressed")).toBe("true");
  });
  it("opens the add-node menu out of where you held, and above + when + is pressed", async () => {
    const { container } = await open();
    const board = container.querySelector(".aw-board")!;
    fireEvent.pointerDown(board, { button: 0, pointerId: 5, clientX: 300, clientY: 200 });
    await new Promise((resolve) => setTimeout(resolve, 650));
    const held = await screen.findByRole("dialog", { name: "Add node" });
    expect(held.style.getPropertyValue("--aw-spawn-origin")).toBe("0px 0px");
    expect(held.style.top).toBe("200px");
    fireEvent.pointerUp(board, { pointerId: 5, clientX: 300, clientY: 200 });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Add nodes" }));
    const above = screen.getByRole("dialog", { name: "Add node" });
    expect(above.classList.contains("is-from-below")).toBe(true);
    expect(above.style.bottom).not.toBe("");
    expect(above.style.top).toBe("");
  });
  it("supports touch panning, pinch zoom and cancellation", async () => {
    const { container } = await open();
    const board = container.querySelector(".aw-board")!;
    const touch = { pointerType: "touch", button: 0 };
    fireEvent.pointerDown(board, { ...touch, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(board, { ...touch, pointerId: 1, clientX: 120, clientY: 140 });
    expect((container.querySelector(".aw-world") as HTMLElement).style.transform).toBe("translate(300px, 200px) scale(1)");
    fireEvent.pointerDown(board, { ...touch, pointerId: 2, clientX: 220, clientY: 140 });
    fireEvent.pointerMove(board, { ...touch, pointerId: 2, clientX: 320, clientY: 140 });
    expect((container.querySelector(".aw-world") as HTMLElement).style.transform).toContain("scale(2)");
    fireEvent.pointerCancel(board, { ...touch, pointerId: 2 });
    const stopped = (container.querySelector(".aw-world") as HTMLElement).style.transform;
    fireEvent.pointerMove(board, { ...touch, pointerId: 1, clientX: 600, clientY: 600 });
    expect((container.querySelector(".aw-world") as HTMLElement).style.transform).toBe(stopped);
    expect(saved.graph.edges).toHaveLength(1);
  });
  it("opens the node menu with a touch-friendly button and preserves regular right-click", async () => {
    const { container } = await open();
    fireEvent.click(screen.getByRole("button", { name: "Add nodes" }));
    expect(screen.getByRole("dialog", { name: "Add node" })).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Filter nodes" }));
    fireEvent.keyDown(window, { key: "Escape" });
    const board = container.querySelector(".aw-board")!;
    fireEvent.pointerDown(board, { button: 2, clientX: 180, clientY: 150 });
    fireEvent.pointerUp(board, { button: 2, clientX: 180, clientY: 150 });
    fireEvent.contextMenu(board, { clientX: 180, clientY: 150 });
    const menu = screen.getByRole("dialog", { name: "Add node" });
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Filter nodes" }));
    fireEvent.change(document.activeElement!, { target: { value: "wait" } });
    expect(menu.querySelectorAll(".aw-tile")).toHaveLength(1);
    expect(menu.querySelector(".aw-tile")?.textContent).toContain("Wait");
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Filter nodes" }));
  });
  it("uses distinct start/end roles, renames old Finish nodes and hides end outputs", async () => {
    saved.graph.nodes[2].type = "pipeline.finish";
    saved.graph.nodes[2].label = "Finish";
    const { container } = await open();
    expect(screen.getByText("Chat input")).toBeTruthy();
    expect(screen.getByText("Return to user")).toBeTruthy();
    expect(container.querySelector('[data-aw-node="s"]')?.classList.contains("aw-node--starter")).toBe(true);
    expect(container.querySelector('[data-aw-node="b"]')?.classList.contains("aw-node--end")).toBe(true);
    expect(screen.queryByRole("button", { name: "Connect from Return to user" })).toBeNull();
    wire("Connect to Pause", "Connect to Return to user");
    await save();
    expect(saved.graph.edges).toHaveLength(1);
  });
  it("reloads the canvas when chat saves the graph", async () => {
    renderView();
    window.dispatchEvent(new CustomEvent("ducky:focus-graph", { detail: { id: "p" } }));
    await waitFor(() => expect(api.get_workflow).toHaveBeenCalledWith("p"));
    expect(await screen.findByDisplayValue("Example")).toBeTruthy();
  });
  it("clears the open graph when chat deletes it", async () => {
    await open();
    expect(screen.getByDisplayValue("Example")).toBeTruthy();
    window.dispatchEvent(new CustomEvent("ducky:graph-deleted", { detail: { id: "p" } }));
    await waitFor(() => expect(screen.queryByDisplayValue("Example")).toBeNull());
  });
  it("shows the assigned ducky's real artwork", async () => {
    await openAgentNode({ ducky: "artist" });
    await waitFor(() => expect(document.querySelector('[data-aw-node="a"] .aw-node-icon img')?.getAttribute("src")).toContain("Artist.png"));
  });
});

describe("Workflows folders by owner", () => {
  it("hides the workflow toolbar until a workflow is selected and after it is removed", async () => {
    renderView();
    await screen.findByText("Example");
    expect(screen.queryByRole("toolbar", { name: "Workflow actions" })).toBeNull();
    fireEvent.click(screen.getByText("Example"));
    await screen.findByDisplayValue("Example");
    expect(screen.getByRole("toolbar", { name: "Workflow actions" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete", exact: true }));
    await screen.findByText("Delete “Example”?");  // always asks first
    expect(api.delete_workflow).not.toHaveBeenCalled();
    await confirmIt("Delete");
    await waitFor(() => expect(api.delete_workflow).toHaveBeenCalledWith("p"));
    await waitFor(() => expect(screen.queryByRole("toolbar", { name: "Workflow actions" })).toBeNull());
  });
  it("lists each workflow in its owner's folder with what starts it, and labels the open one", async () => {
    renderView();
    const local = await screen.findByRole("region", { name: "Local" });
    const team = screen.getByRole("region", { name: "Team · Alpha Studio" });
    expect(local.textContent).toContain("Example");
    expect(within(local).getByRole("img", { name: "Chat" }).textContent).toBe("");  // an icon, not the word
    expect(local.textContent).not.toContain("Only on this PC");
    expect(local.querySelector(".aw-folder-status")).toBeNull();
    expect(team.textContent).toContain("Daily check");
    expect(within(team).getByRole("img", { name: "Every 5m" })).toBeTruthy();
    expect(team.textContent).not.toContain("Not synced yet");  // no sync line under the team…
    expect(screen.getByRole("button", { name: "Team · Alpha Studio", exact: true }).getAttribute("title")).toContain("Not synced yet");  // …it is in the tooltip
    expect(document.querySelector(".aw-folder-status")).toBeNull();
    expect(team.querySelector(".aw-trigger")?.getAttribute("title")).toContain("not on this PC");
    fireEvent.click(screen.getByText("Daily check"));
    await screen.findByDisplayValue("Daily check");
    expect(document.querySelector(".aw-owner-chip")).toBeNull();  // only the name in the toolbar
    expect(document.querySelector(".aw-name")?.getAttribute("title")).toContain("Team Alpha Studio");
    fireEvent.click(screen.getByText("Example"));
    await screen.findByDisplayValue("Example");
    expect(document.querySelector(".aw-name")?.getAttribute("title")).toContain("Local workflows");
    expect(local.querySelector(".aw-list-state")?.getAttribute("aria-label")).toBe("On");  // a lit dot, not the word
    expect(document.querySelectorAll(".aw-list-row.is-active")).toHaveLength(1);
  });
  it("collapses the entire list without losing the graph or folder states", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Team · Alpha Studio", exact: true }));
    const toggle = screen.getByRole("button", { name: "Workflows", exact: true });
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(document.querySelector(".aw-root")?.classList.contains("is-list-collapsed")).toBe(true);
    expect(screen.queryByRole("button", { name: "New workflow in Local" })).toBeNull();
    expect(screen.queryByRole("separator", { name: "Resize workflow list" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Local", exact: true })).toBeNull();
    expect(screen.getByDisplayValue("Example")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Connect from Pause" })).toBeTruthy();
    fireEvent.click(toggle);
    expect(screen.queryByRole("button", { name: "New workflow", exact: true })).toBeNull();  // each section has its own +
    expect(screen.getByRole("button", { name: "New workflow in Local" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Local", exact: true }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: "Team · Alpha Studio", exact: true }).getAttribute("aria-expanded")).toBe("false");
  });
  it("creates a workflow in the folder whose + was used", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "New workflow in Team · Alpha Studio" }));
    fireEvent.click(screen.getByRole("button", { name: "Create workflow" }));
    await waitFor(() => expect(api.save_workflow).toHaveBeenCalledWith(expect.objectContaining({ id: "", name: "Untitled" }), "teamT"));
    await screen.findByDisplayValue("Untitled");
    expect(document.querySelector(".aw-name")?.getAttribute("title")).toContain("Team Alpha Studio");
    fireEvent.click(screen.getByRole("button", { name: "New workflow in Local" }));
    fireEvent.click(screen.getByRole("button", { name: "Save in Local" }));
    fireEvent.click(screen.getByRole("button", { name: "Create workflow" }));
    await waitFor(() => expect(api.save_workflow).toHaveBeenLastCalledWith(expect.objectContaining({ id: "" }), "local"));
    fireEvent.click(screen.getByRole("button", { name: "New workflow in Local" }));
    fireEvent.click(screen.getByRole("button", { name: "Save in Team · Alpha Studio" }));
    fireEvent.click(screen.getByRole("button", { name: "Create workflow" }));
    await waitFor(() => expect(api.save_workflow).toHaveBeenLastCalledWith(expect.objectContaining({ id: "" }), "teamT"));
  });
  it("runs and duplicates a read-only team workflow but never edits or pushes it", async () => {
    TEAM.readOnly = true;
    TEAM.reason = "Only members with Manage automations can change team workflows.";
    daily.owner = TEAM;
    await open("Daily check");
    expect(screen.getByRole("note").textContent).toContain("Manage automations");
    for (const name of ["Save", "Delete", "Enabled", "Undo"]) {
      expect(screen.getByRole("button", { name, exact: true }).hasAttribute("disabled")).toBe(true);
    }
    expect(screen.queryByRole("button", { name: "New workflow in Team · Alpha Studio" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Test", exact: true }));
    await waitFor(() => expect(api.run_workflow).toHaveBeenCalledWith("d"));
    expect(api.save_workflow).not.toHaveBeenCalled();
    dropdown("Duplicate");
    fireEvent.click(await screen.findByRole("radio", { name: "Local" }));
    await waitFor(() => expect(api.save_workflow).toHaveBeenCalledWith(expect.objectContaining({ id: "", name: "Daily check copy" }), "local"));
  });
  it("moves a Local workflow to a team, and asks before moving one out of a team", async () => {
    await open();
    dropdown("Move or copy");
    fireEvent.click(await screen.findByRole("radio", { name: "Move to Team · Alpha Studio" }));
    await waitFor(() => expect(api.copy_workflow).toHaveBeenCalledWith("p", "teamT", true));
    fireEvent.click(screen.getByText("Daily check"));
    await screen.findByDisplayValue("Daily check");
    dropdown("Move or copy");
    fireEvent.click(await screen.findByRole("radio", { name: "Move to Local" }));
    await screen.findByText("Move out of Alpha Studio?");
    expect(api.copy_workflow).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /^Move \(/ }));
    await waitFor(() => expect(api.copy_workflow).toHaveBeenLastCalledWith("d", "local", true));
  });
  it("asks before deleting a team workflow for everyone", async () => {
    await open("Daily check");
    fireEvent.click(screen.getByRole("button", { name: "Delete", exact: true }));
    await screen.findByText("Delete for everyone in Alpha Studio?");
    expect(api.delete_workflow).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^Delete \(/ }));
    await waitFor(() => expect(api.delete_workflow).toHaveBeenCalledWith("d"));
  });
  it("switches Run on this PC for a scheduled team workflow", async () => {
    await open("Daily check");
    const toggle = screen.getByRole("button", { name: "Run on this PC" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle);
    await waitFor(() => expect(api.set_workflow_run_here).toHaveBeenCalledWith("d", true));
    await waitFor(() => expect(screen.getByRole("button", { name: "Run on this PC" }).getAttribute("aria-pressed")).toBe("true"));
    fireEvent.click(screen.getByText("Example"));
    await screen.findByDisplayValue("Example");
    expect(screen.queryByRole("button", { name: "Run on this PC" })).toBeNull();
  });
  it("updates a team online from its folder", async () => {
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Update Team · Alpha Studio online" }));
    await waitFor(() => expect(api.workflow_sync).toHaveBeenCalledWith(true, "teamT", true));
    expect(screen.queryByRole("button", { name: "Update Local online" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Open Team · Alpha Studio on the web" })).toBeNull();
  });
  it("asks the Store for teams once when opened and syncs them while open", async () => {
    renderView();
    await screen.findByText("Example");
    await waitFor(() => expect(api.workflow_owners).toHaveBeenCalledWith(true));
    await waitFor(() => expect(api.workflow_sync).toHaveBeenCalledWith(false));
    expect(api.workflow_owners.mock.calls.filter((call) => call[0] === true)).toHaveLength(1);
  });
  it("offers signed-out workflows to the account and says how to share", async () => {
    owners.localImport = 2;
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: /Bring in/ }));
    await waitFor(() => expect(api.import_local_workflows).toHaveBeenCalled());
    cleanup();
    api.workflow_sync.mockClear();
    owners = { ok: true, owners: [LOCAL], signedIn: false, teamsEnabled: false, localImport: 0 };
    renderView();
    expect(await screen.findByText("Sign in to share workflows with a team.")).toBeTruthy();
    expect(api.workflow_sync).not.toHaveBeenCalled();
  });
  it("opens a chat-linked workflow and ignores deletes of other workflows", async () => {
    await open();
    await act(async () => { window.dispatchEvent(new CustomEvent("ducky:focus-graph", { detail: { id: "d" } })); });
    await screen.findByDisplayValue("Daily check");
    await act(async () => { window.dispatchEvent(new CustomEvent("ducky:graph-deleted", { detail: { id: "p" } })); });
    expect(screen.getByDisplayValue("Daily check")).toBeTruthy();
  });
  it("keeps a delayed save from replacing the newly selected workflow", async () => {
    await open();
    let finish!: (value: { workflow: AutomationDto }) => void;
    api.save_workflow.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Save", exact: true }));
    fireEvent.click(screen.getByText("Daily check"));
    await screen.findByDisplayValue("Daily check");
    await act(async () => { finish({ workflow: saved }); });
    expect(screen.getByDisplayValue("Daily check")).toBeTruthy();
  });
});

describe("details panel", () => {
  const canvas = () => document.querySelector('.aw-board')!;
  const ctrlClick = (id: string) => {
    fireEvent.pointerDown(document.querySelector(`.aw-node[data-aw-node="${id}"]`)!, { button: 0, ctrlKey: true, pointerId: 7 });
    fireEvent.pointerUp(canvas(), { button: 0, ctrlKey: true, pointerId: 7 });
  };

  it("slides in with the clicked node's details and closes with the selection", async () => {
    await open();
    editNode();
    const panel = details()!;
    expect(panel.querySelector(".aw-insp-name")?.textContent).toBe("Pause");
    expect(panel.querySelector(".aw-insp-desc")?.textContent).toBe("Pause description");
    expect(within(panel).queryByRole("textbox")).toBeNull();  // written out, not in boxes, until Edit
    expect(within(panel).getByRole("button", { name: "Edit" })).toBeTruthy();
    expect(screen.getByRole("spinbutton", { name: "Seconds" })).toBeTruthy();
    expect(screen.queryByRole("tablist", { name: "Selected items" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Close details" }));
    expect(details()).toBeNull();
    editNode();
    fireEvent.keyDown(canvas(), { key: "Escape" });
    expect(details()).toBeNull();
  });
  it("shows a tab for each selected item and opens the last one picked", async () => {
    await open();
    ctrlClick("s"); ctrlClick("a");
    const tabs = selectedTabs();
    expect(tabs.map((tab) => tab.lastElementChild?.textContent)).toEqual(["Chat input", "Pause"]);
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("2 selected")).toBeTruthy();
    fireEvent.click(tabs[0]);
    expect(details()?.querySelector(".aw-insp-name")?.textContent).toBe("Chat input");
    fireEvent.keyDown(screen.getByRole("tablist", { name: "Selected items" }), { key: "ArrowRight" });
    expect(details()?.querySelector(".aw-insp-name")?.textContent).toBe("Pause");
  });
  it("never scrolls the page to show a tab while the panel slides in", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    try {
      await open();
      ctrlClick("s"); ctrlClick("a"); ctrlClick("b");
      fireEvent.click(selectedTabs()[0]);
      fireEvent.keyDown(screen.getByRole("tablist", { name: "Selected items" }), { key: "ArrowLeft" });
      expect(selectedTabs()[2].getAttribute("aria-selected")).toBe("true");
      expect(scroll).not.toHaveBeenCalled();
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });
  it("keeps the run log button with the canvas controls, next to +", async () => {
    await open();
    const controls = document.querySelector(".aw-canvas-controls")!;
    const buttons = [...controls.querySelectorAll("button")].map((button) => button.getAttribute("aria-label"));
    expect(buttons).toEqual(["Select tool", "Hand tool", "Run log", "Add nodes", "Fit view", "Zoom", "Outline", "Help"]);  // the outline, then the tours menu, on the right
    expect(screen.getByRole("button", { name: "Zoom" }).querySelector("svg")).toBeNull();  // just the percent
    const toggle = screen.getByRole("button", { name: "Run log" });
    expect(document.querySelector(".aw-log-dock")?.classList.contains("is-open")).toBe(false);
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(document.querySelector(".aw-log-dock")?.classList.contains("is-open")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Hide run log" }));
    expect(document.querySelector(".aw-log-dock")?.getAttribute("aria-hidden")).toBe("true");
  });
  it("clears the run log on screen and on this PC", async () => {
    api.run_workflow.mockResolvedValue({ ok: true, steps: [{ label: "Pause", ok: true }] });
    api.clear_workflow_runs.mockResolvedValue({ ok: true });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Test", exact: true }));
    await screen.findByText("Pause ok");
    expect(screen.getByRole("button", { name: "Run log" }).textContent).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: "Clear log" }));
    await waitFor(() => expect(api.clear_workflow_runs).toHaveBeenCalledWith("p"));
    expect(screen.queryByText("Pause ok")).toBeNull();
    expect(screen.getByRole("button", { name: "Run log" }).textContent).toBe("");
    expect((screen.getByRole("button", { name: "Clear log" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("resizes the list and the details panel by dragging their edges, and remembers it", async () => {
    window.localStorage.removeItem("ducky.workflows.panelWidths.v1");
    await open();
    const root = document.querySelector(".aw-root") as HTMLElement;
    const listEdge = screen.getByRole("separator", { name: "Resize workflow list" });
    fireEvent.pointerDown(listEdge, { button: 0, clientX: 230 });
    expect(root.classList.contains("is-resizing")).toBe(true);
    fireEvent.pointerMove(listEdge, { clientX: 330 });
    fireEvent.pointerUp(listEdge, { clientX: 330 });
    expect(root.classList.contains("is-resizing")).toBe(false);
    expect(root.style.getPropertyValue("--aw-list-w")).toBe("320px");
    fireEvent.pointerDown(listEdge, { button: 0, clientX: 330 });
    fireEvent.pointerMove(listEdge, { clientX: 2000 });
    fireEvent.pointerUp(listEdge, { clientX: 2000 });
    expect(root.style.getPropertyValue("--aw-list-w")).toBe("480px");  // capped
    editNode();
    const panelEdge = screen.getByRole("separator", { name: "Resize details panel" });
    fireEvent.keyDown(panelEdge, { key: "ArrowLeft" });  // the panel is on the right: left widens it
    expect(root.style.getPropertyValue("--aw-insp-w")).toBe("356px");
    expect(JSON.parse(window.localStorage.getItem("ducky.workflows.panelWidths.v1") || "{}")).toEqual({ list: 480, inspector: 356 });
    cleanup();
    await open();
    expect((document.querySelector(".aw-root") as HTMLElement).style.getPropertyValue("--aw-list-w")).toBe("480px");
  });
  it("deletes from the panel or with the Delete key, and Ctrl+Z brings it back", async () => {
    await open();
    editNode("b");
    fireEvent.click(screen.getByRole("button", { name: "Delete node" }));
    await screen.findByText("Delete “Continue”?");
    expect(document.querySelector('[data-aw-node="b"]')).toBeTruthy();  // not until you say so
    await confirmIt("Delete");
    await waitFor(() => expect(document.querySelector('[data-aw-node="b"]')).toBeNull());
    expect(details()).toBeNull();
    ctrlClick("s"); ctrlClick("a");
    fireEvent.keyDown(canvas(), { key: "Delete" });
    expect(document.querySelectorAll(".aw-node")).toHaveLength(0);
    fireEvent.keyDown(canvas(), { key: "z", ctrlKey: true });
    await waitFor(() => expect(saved.graph.nodes.map((node) => node.id)).toEqual(["s", "a"]));
    expect(saved.graph.edges).toEqual([{ source: "s", target: "a", kind: "main" }]);
  });
  it("edits the name and description where they are written and saves them together", async () => {
    await open();
    editNode("s");
    expect(details()!.querySelector(".aw-insp-settings")).toBeNull();  // Chat input has nothing else to set
    editText({ name: "Image input", description: "Send your island concept" });
    await waitFor(() => expect(saved.graph.nodes[0]).toEqual(expect.objectContaining({ label: "Image input", description: "Send your island concept" })));
    expect(api.save_workflow).toHaveBeenCalledTimes(1);  // one save for both
    expect(document.querySelector('[data-aw-node="s"] .aw-node-title strong')?.textContent).toBe("Image input");
    expect(details()?.querySelector(".aw-insp-desc")?.textContent).toBe("Send your island concept");
    fireEvent.click(within(details()!).getByRole("button", { name: "Edit" }));
    const name = within(details()!).getByRole("textbox", { name: "Node name" });
    name.textContent = "Image in";
    fireEvent.keyDown(name, { key: "Enter" });  // Enter in the name saves too
    await waitFor(() => expect(saved.graph.nodes[0].label).toBe("Image in"));
    expect(screen.queryByLabelText("Label")).toBeNull();
  });
  it("cancels with Escape and rejects empty names while allowing empty descriptions", async () => {
    await open();
    editNode();
    editText({ name: "Changed" }, false);
    fireEvent.keyDown(within(details()!).getByRole("textbox", { name: "Node name" }), { key: "Escape" });
    expect(details()?.querySelector(".aw-insp-name")?.textContent).toBe("Pause");
    expect(details()).toBeTruthy();  // Escape in the text only undoes the editing
    editText({ name: " " });
    expect(screen.getByRole("alert").textContent).toContain("needs a name");
    expect(api.save_workflow).not.toHaveBeenCalled();
    fireEvent.click(within(details()!).getByRole("button", { name: "Cancel" }));
    editText({ description: "" });
    await waitFor(() => expect(saved.graph.nodes[1].description).toBe(""));
  });
  it("keeps newer settings edits when a name save completes late", async () => {
    await open();
    let finish!: (result: { workflow: AutomationDto }) => void;
    api.save_workflow.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    editNode();
    editText({ name: "Wait for UEFN" });
    await waitFor(() => expect(api.save_workflow).toHaveBeenCalledTimes(1));
    const submitted = structuredClone(api.save_workflow.mock.calls[0][0]);
    fireEvent.change(screen.getByRole("spinbutton", { name: "Seconds" }), { target: { value: "5" } });
    await act(async () => { finish({ workflow: submitted }); });
    expect((screen.getByRole("spinbutton", { name: "Seconds" }) as HTMLInputElement).value).toBe("5");
    api.save_workflow.mockClear();
    await save();
    expect(saved.graph.nodes[1].config.seconds).toBe(5);
    expect(saved.graph.nodes[1].label).toBe("Wait for UEFN");
  });
  it("shows failed name saves and allows retrying without losing edits", async () => {
    await open();
    api.save_workflow.mockRejectedValueOnce(new Error("offline"));
    editNode();
    editText({ name: "Wait for UEFN" });
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Save", exact: true }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(saved.graph.nodes[1].label).toBe("Wait for UEFN");
  });
  it("says why a save was refused (too much code) instead of only asking to retry", async () => {
    await open();
    api.save_workflow.mockResolvedValueOnce({ ok: false, error: "This workflow holds 302 KB of code; the most is 256 KB." });
    editNode();
    editText({ name: "Wait for UEFN" });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Could not save changes. This workflow holds 302 KB of code; the most is 256 KB.");
  });
});

describe("workflow multi-selection and groups", () => {
  const canvas = () => document.querySelector('.aw-board')!;
  const node = (id: string) => document.querySelector(`.aw-node[data-aw-node="${id}"]`)!;
  const selected = () => [...document.querySelectorAll('.aw-node.is-selected')].map((el) => el.getAttribute('data-aw-node'));
  const ctrlClick = (id: string) => {
    fireEvent.pointerDown(node(id), { button: 0, ctrlKey: true, pointerId: 7 });
    fireEvent.pointerUp(canvas(), { button: 0, ctrlKey: true, pointerId: 7 });
  };
  const shortcut = (shiftKey = false) => fireEvent.keyDown(canvas(), { key: 'g', ctrlKey: true, shiftKey });
  async function makeGroup() {
    await open();
    ctrlClick('s'); ctrlClick('a'); shortcut();
    await waitFor(() => expect(saved.graph.groups?.[0].node_ids).toEqual(['s', 'a']));
  }

  it("toggles Ctrl-click selection, including on node titles, without editing anything", async () => {
    await open();
    ctrlClick('s'); ctrlClick('a');
    expect(selected()).toEqual(['s', 'a']);
    ctrlClick('s');
    expect(selected()).toEqual(['a']);
    const title = node('b').querySelector('.aw-node-title')!;
    fireEvent.pointerDown(title, { button: 0, ctrlKey: true });
    fireEvent.click(title, { ctrlKey: true });
    expect(selected()).toEqual(['a', 'b']);
    expect(selectedTabs().map((tab) => tab.lastElementChild?.textContent)).toEqual(['Pause', 'Continue']);
    expect(api.save_workflow).not.toHaveBeenCalled();
  });

  it("box-selects backwards at non-default zoom without panning or moving nodes", async () => {
    await open();
    zoomBy("Zoom out");
    const transform = (document.querySelector('.aw-world') as HTMLElement).style.transform;
    const zoom = 1 / 1.2;
    const start = { button: 0, ctrlKey: true, shiftKey: true, pointerId: 4, clientX: 280 + 520 * zoom, clientY: 160 + 100 * zoom };
    const end = { ...start, clientX: 280 - 20 * zoom, clientY: 160 - 20 * zoom };
    fireEvent.pointerDown(canvas(), start);
    fireEvent.pointerMove(canvas(), end);
    expect(document.querySelector('.aw-selection-box')).toBeTruthy();
    expect(selected()).toEqual(['s', 'a']);
    fireEvent.pointerUp(canvas(), end);
    expect(document.querySelector('.aw-selection-box')).toBeNull();
    expect((document.querySelector('.aw-world') as HTMLElement).style.transform).toBe(transform);
    shortcut();
    await waitFor(() => expect(saved.graph.groups?.[0].node_ids).toEqual(['s', 'a']));
    expect(saved.graph.nodes.map(({ x, y }) => [x, y])).toEqual([[0, 0], [280, 0], [560, 0]]);
  });

  it.each(['pointercancel', 'Escape', 'blur'])("cancels box selection on %s and restores the prior selection", async (end) => {
    await open(); ctrlClick('b');
    fireEvent.pointerDown(canvas(), { button: 0, ctrlKey: true, shiftKey: true, pointerId: 4, clientX: 260, clientY: 140 });
    fireEvent.pointerMove(canvas(), { pointerId: 4, clientX: 810, clientY: 260 });
    expect(selected()).toEqual(['s', 'a', 'b']);
    if (end === 'pointercancel') fireEvent.pointerCancel(canvas(), { pointerId: 4 });
    else if (end === 'blur') fireEvent.blur(window);
    else fireEvent.keyDown(canvas(), { key: 'Escape' });
    expect(selected()).toEqual(['b']);
    expect(document.querySelector('.aw-selection-box')).toBeNull();
  });

  it("drags all selected nodes together while leaving unselected nodes alone", async () => {
    await open(); ctrlClick('s'); ctrlClick('a');
    fireEvent.pointerDown(node('a').querySelector('.aw-node-body')!, { button: 0, pointerId: 3, clientX: 580, clientY: 180 });
    fireEvent.pointerMove(canvas(), { pointerId: 3, clientX: 660, clientY: 220 });
    fireEvent.pointerUp(canvas(), { pointerId: 3, clientX: 660, clientY: 220 });
    expect(selected()).toEqual(['s', 'a']);
    await save();
    expect(saved.graph.nodes.map(({ x, y }) => [x, y])).toEqual([[80, 40], [360, 40], [560, 0]]);
  });

  it("persists a group, renames it in the panel, and retains it on reload", async () => {
    await makeGroup();
    expect(document.querySelector('.aw-group-title')?.textContent).toBe('Group');
    // The new group opens in the panel, ahead of its nodes.
    expect(selectedTabs().map((tab) => tab.lastElementChild?.textContent)).toEqual(['Group', 'Chat input', 'Pause']);
    fireEvent.click(within(details()!).getByRole('button', { name: 'Edit' }));
    const input = within(details()!).getByRole('textbox', { name: 'Group name' });
    input.textContent = 'Island setup';
    fireEvent.keyDown(input, { key: 'g', ctrlKey: true, shiftKey: true });
    expect(saved.graph.groups).toHaveLength(1);
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(saved.graph.groups?.[0].name).toBe('Island setup'));
    fireEvent.click(screen.getByText('Example'));
    await screen.findByRole('button', { name: 'Select group Island setup' });
    expect(selected()).toEqual([]);
  });

  it("removes only a selected member, shrinks the box, then removes an empty group", async () => {
    await makeGroup();
    expect((document.querySelector('.aw-group') as HTMLElement).style.width).toBe('568px');
    fireEvent.pointerDown(node('a').querySelector('.aw-node-body')!, { button: 0, pointerId: 3 });
    fireEvent.pointerUp(canvas(), { button: 0, pointerId: 3 });
    expect(selected()).toEqual(['a']);
    shortcut(true);
    await waitFor(() => expect(saved.graph.groups?.[0].node_ids).toEqual(['s']));
    expect((document.querySelector('.aw-group') as HTMLElement).style.width).toBe('288px');
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Select group Group' }), { button: 0, pointerId: 5 });
    fireEvent.pointerUp(canvas(), { pointerId: 5 });
    shortcut(true);
    await waitFor(() => expect(saved.graph.groups).toEqual([]));
    expect(document.querySelector('.aw-group')).toBeNull();
    expect(saved.graph.nodes).toHaveLength(3);
    expect(saved.graph.edges).toHaveLength(1);
  });

  it("moves a group as a unit, updates its box, and ungroups all members", async () => {
    await makeGroup();
    const group = screen.getByRole('button', { name: 'Select group Group' });
    fireEvent.pointerDown(group, { button: 0, pointerId: 5, clientX: 260, clientY: 140 });
    fireEvent.pointerMove(canvas(), { pointerId: 5, clientX: 300, clientY: 200 });
    fireEvent.pointerUp(canvas(), { pointerId: 5, clientX: 300, clientY: 200 });
    expect((group as HTMLElement).style.left).toBe('16px');
    expect((group as HTMLElement).style.top).toBe('36px');
    shortcut(true);
    await waitFor(() => expect(saved.graph.groups).toEqual([]));
    expect(saved.graph.nodes.map(({ x, y }) => [x, y])).toEqual([[40, 60], [320, 60], [560, 0]]);
  });

  it("shrinks group names with the canvas but keeps them readable, and cleans membership when a node is deleted", async () => {
    await makeGroup();
    zoomBy("Zoom out");
    expect((document.querySelector('.aw-group-title') as HTMLElement).style.transform).toBe('scale(1)');  // smaller with the canvas
    editNode('a');
    fireEvent.click(screen.getByRole('button', { name: 'Delete node' }));
    await confirmIt("Delete");
    await save();
    expect(saved.graph.groups?.[0].node_ids).toEqual(['s']);
    expect((document.querySelector('.aw-group') as HTMLElement).style.width).toBe('288px');
    zoomBy("10%");
    expect((document.querySelector('.aw-group-title') as HTMLElement).style.transform).toBe('scale(6.25)');  // still ~10px on screen
  });

  it("colours, opens and deletes a group from its tab", async () => {
    await makeGroup();
    fireEvent.click(within(details()!).getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Gold' }));
    await waitFor(() => expect(saved.graph.groups?.[0].color).toBe('amber'));
    expect(document.querySelector('.aw-group')?.classList.contains('aw-group-color--amber')).toBe(true);
    fireEvent.click(screen.getByRole('radio', { name: 'Plain' }));
    await waitFor(() => expect(saved.graph.groups?.[0]).not.toHaveProperty('color'));
    fireEvent.click(screen.getByRole('button', { name: 'Ungroup' }));
    await waitFor(() => expect(saved.graph.groups).toEqual([]));
    expect(saved.graph.nodes).toHaveLength(3);
    fireEvent.keyDown(canvas(), { key: 'Escape' });
    ctrlClick('s'); ctrlClick('a'); shortcut();
    await waitFor(() => expect(saved.graph.groups).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: 'Delete 2 nodes' }));
    await confirmIt("Delete");
    await save();
    expect(saved.graph.nodes.map((item) => item.id)).toEqual(['b']);
    expect(saved.graph.groups).toEqual([]);
  });

  it("reports group save failures and keeps the group for a retry", async () => {
    await open();
    api.save_workflow.mockRejectedValueOnce(new Error('offline'));
    ctrlClick('s'); ctrlClick('a'); shortcut();
    await screen.findByRole('alert');
    expect(document.querySelector('.aw-group')).toBeTruthy();
    await save();
    await waitFor(() => expect(saved.graph.groups?.[0].node_ids).toEqual(['s', 'a']));
  });
});


describe("workflow history controls", () => {
  it("shows only the workflow name and supports keyboard undo/redo after saving", async () => {
    await open();
    expect(screen.queryByLabelText("Workflow description")).toBeNull();
    expect(screen.getByRole("button", { name: "Undo", exact: true }).hasAttribute("disabled")).toBe(true);
    const name = screen.getByRole("textbox", { name: "Workflow name" });
    fireEvent.focus(name);
    fireEvent.change(name, { target: { value: "Ren" } });
    fireEvent.change(name, { target: { value: "Renamed" } });
    fireEvent.blur(name);
    await waitFor(() => expect(saved.name).toBe("Renamed"));
    const board = document.querySelector(".aw-board")!;
    fireEvent.keyDown(board, { key: "z", ctrlKey: true });
    await waitFor(() => expect(saved.name).toBe("Example"));
    fireEvent.keyDown(board, { key: "y", ctrlKey: true });
    await waitFor(() => expect(saved.name).toBe("Renamed"));
    fireEvent.keyDown(name, { key: "z", ctrlKey: true });
    expect((name as HTMLInputElement).value).toBe("Renamed");
  });

  it("undoes an entire drag once and invalidates redo after a new edit", async () => {
    await open();
    const node = document.querySelector('[data-aw-node="a"] .aw-node-body')!;
    const board = document.querySelector(".aw-board")!;
    fireEvent.pointerDown(node, { button: 0, pointerId: 1, clientX: 600, clientY: 200 });
    fireEvent.pointerMove(board, { pointerId: 1, clientX: 620, clientY: 200 });
    fireEvent.pointerMove(board, { pointerId: 1, clientX: 640, clientY: 200 });
    fireEvent.pointerUp(board, { pointerId: 1, clientX: 640, clientY: 200 });
    fireEvent.keyDown(board, { key: "z", ctrlKey: true });
    await waitFor(() => expect(api.save_workflow).toHaveBeenCalled());
    expect(saved.graph.nodes[1].x).toBe(280);
    expect(screen.getByRole("button", { name: "Undo", exact: true }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Enabled", exact: true }));
    expect(screen.getByRole("button", { name: "Redo", exact: true }).hasAttribute("disabled")).toBe(true);
  });

  it("restores saved versions as undoable new saves, retaining run history", async () => {
    saved.runs = [{ ok: true, steps: [] }];
    const old = { ...structuredClone(saved), name: "Earlier", graph: { ...saved.graph, groups: [{ id: "g", name: "Setup", node_ids: ["a", "b"] }] } };
    api.list_workflow_versions.mockResolvedValue({ ok: true, versions: [{ id: "v1", name: "Earlier", saved_at: 100, node_count: 3 }] });
    api.get_workflow_version.mockResolvedValue({ ok: true, workflow: old });
    await open();
    dropdown("History");
    fireEvent.click(await screen.findByRole("radio", { name: /Version 1 · Earlier/ }));
    await waitFor(() => expect(saved.name).toBe("Earlier"));
    expect(saved.graph.groups?.[0].name).toBe("Setup");
    expect(saved.runs).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Undo", exact: true }));
    await waitFor(() => expect(saved.name).toBe("Example"));
    expect(saved.graph.groups).toBeUndefined();
  });

  it("starts a fresh history for another workflow", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Enabled", exact: true }));
    fireEvent.click(screen.getByText("Daily check"));
    await screen.findByDisplayValue("Daily check");
    expect(screen.getByRole("button", { name: "Undo", exact: true }).hasAttribute("disabled")).toBe(true);
  });
});

describe("locked nodes and groups", () => {
  const canvas = () => document.querySelector('.aw-board')!;
  const node = (id: string) => document.querySelector(`.aw-node[data-aw-node="${id}"]`)!;
  const drag = (el: Element, pointerId: number) => {
    fireEvent.pointerDown(el, { button: 0, pointerId, clientX: 600, clientY: 200 });
    fireEvent.pointerMove(canvas(), { pointerId, clientX: 680, clientY: 260 });
    fireEvent.pointerUp(canvas(), { pointerId, clientX: 680, clientY: 260 });
  };

  it("locks a node from its details: it shows a padlock, won't move or change, and says why", async () => {
    await open();
    editNode("a");
    expect(document.querySelector(".aw-lockbar")).toBeNull();  // the lock is an icon in the details header
    fireEvent.click(screen.getByRole("button", { name: "Lock node" }));
    await waitFor(() => expect(saved.graph.nodes[1].locked).toBe(true));
    expect(node("a").classList.contains("is-locked")).toBe(true);
    expect(node("a").querySelector('[aria-label="Locked"]')).toBeTruthy();
    expect(screen.getByRole("button", { name: "Unlock node" }).getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector(".aw-insp-state")?.getAttribute("title")).toContain("can't be moved or changed");
    expect(within(details()!).queryByRole("button", { name: "Edit" })).toBeNull();  // nothing to edit while locked
    expect(screen.getByRole("button", { name: "Delete node" }).hasAttribute("disabled")).toBe(true);
    expect(screen.queryByRole("button", { name: "Node icon" })).toBeNull();  // its icon can't change either
    drag(node("a").querySelector(".aw-node-card")!, 3);
    expect(document.querySelector(".aw-toast")?.textContent).toContain("Pause is locked");
    fireEvent.keyDown(canvas(), { key: "Delete" });
    expect(node("a")).toBeTruthy();
    wire("Connect from Pause", "Connect to Continue");
    await save();
    expect(saved.graph.nodes[1]).toEqual(expect.objectContaining({ x: 280, y: 0, locked: true }));
    expect(saved.graph.edges).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Unlock node" }));
    await waitFor(() => expect(saved.graph.nodes[1]).not.toHaveProperty("locked"));
    drag(node("a").querySelector(".aw-node-card")!, 4);
    await save();
    expect(saved.graph.nodes[1]).toEqual(expect.objectContaining({ x: 360, y: 60 }));
  });
  it("locks a whole group: its nodes can't move and their details say which group to unlock", async () => {
    await open();
    fireEvent.pointerDown(node("s"), { button: 0, ctrlKey: true, pointerId: 7 });
    fireEvent.pointerUp(canvas(), { button: 0, ctrlKey: true, pointerId: 7 });
    fireEvent.pointerDown(node("a"), { button: 0, ctrlKey: true, pointerId: 7 });
    fireEvent.pointerUp(canvas(), { button: 0, ctrlKey: true, pointerId: 7 });
    fireEvent.keyDown(canvas(), { key: "g", ctrlKey: true });
    await waitFor(() => expect(saved.graph.groups).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Lock all" }));
    await waitFor(() => expect(saved.graph.groups?.[0].locked).toBe(true));
    expect(saved.graph.nodes.some((item) => item.locked)).toBe(false);  // the group's lock covers its nodes
    expect(screen.getByRole("button", { name: "Unlock all" }).getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector(".aw-group")?.classList.contains("is-locked")).toBe(true);
    drag(screen.getByRole("button", { name: "Select group Group" }), 5);
    expect(document.querySelector(".aw-toast")?.textContent).toContain("Group is locked");
    editNode("s");
    expect(document.querySelector(".aw-insp-state")?.getAttribute("title")).toContain("Group, which is locked");
    expect(screen.getByRole("button", { name: "Unlock node" }).hasAttribute("disabled")).toBe(true);  // unlock the group instead
    await save();
    expect(saved.graph.nodes.map(({ x, y }) => [x, y])).toEqual([[0, 0], [280, 0], [560, 0]]);
  });
});

describe("icons, fitting and agent focus", () => {
  const canvas = () => document.querySelector('.aw-board')!;
  const world = () => (document.querySelector('.aw-world') as HTMLElement).style.transform;
  /** jsdom lays nothing out: give the board a real size so fitting has room. */
  const size = () => {
    Object.defineProperty(canvas(), "clientWidth", { configurable: true, value: 1400 });
    Object.defineProperty(canvas(), "clientHeight", { configurable: true, value: 900 });
  };

  it("picks a node's own icon from its details and goes back to the default", async () => {
    await open();
    editNode("a");
    expect(screen.queryByRole("button", { name: "Node icon" })).toBeNull();  // only while editing
    fireEvent.click(within(details()!).getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Node icon" }));
    fireEvent.click(screen.getByRole("button", { name: "Icon 🎯" }));
    await waitFor(() => expect(saved.graph.nodes[1].icon).toBe("🎯"));
    expect(document.querySelector('[data-aw-node="a"] .aw-node-icon')?.textContent).toBe("🎯");
    fireEvent.click(screen.getByRole("button", { name: "Node icon" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Any emoji" }), { target: { value: "🦖" } });
    fireEvent.click(screen.getByRole("button", { name: "Use" }));
    await waitFor(() => expect(saved.graph.nodes[1].icon).toBe("🦖"));
    fireEvent.click(screen.getByRole("button", { name: "Node icon" }));
    fireEvent.click(screen.getByRole("button", { name: "Back to the default icon" }));
    await waitFor(() => expect(saved.graph.nodes[1]).not.toHaveProperty("icon"));
  });

  it("gives a group its own icon on its title", async () => {
    await open();
    for (const id of ["s", "a"]) {
      fireEvent.pointerDown(document.querySelector(`.aw-node[data-aw-node="${id}"]`)!, { button: 0, ctrlKey: true, pointerId: 7 });
      fireEvent.pointerUp(canvas(), { button: 0, ctrlKey: true, pointerId: 7 });
    }
    fireEvent.click(screen.getByRole("button", { name: "Group selection" }));
    await waitFor(() => expect(saved.graph.groups).toHaveLength(1));
    fireEvent.click(within(screen.getByRole("tablist", { name: "Selected items" })).getByRole("tab", { name: "Group" }));
    fireEvent.click(within(details()!).getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Group icon" }));
    fireEvent.click(screen.getByRole("button", { name: "Icon 🏆" }));
    await waitFor(() => expect(saved.graph.groups?.[0].icon).toBe("🏆"));
    expect(document.querySelector(".aw-group-title")?.textContent).toContain("🏆");
  });

  it("fits what is selected with F: one node zooms in on it, nothing selected fits everything", async () => {
    await open();
    size();
    editNode("b");
    fireEvent.keyDown(canvas(), { key: "f" });
    expect(world()).toContain("scale(1.5)");  // one node: zoomed in on it
    expect(screen.getByRole("button", { name: "Fit view" }).getAttribute("title")).toBe("Zoom to the selected node (F)");
    fireEvent.keyDown(canvas(), { key: "Escape" });
    fireEvent.keyDown(canvas(), { key: "f" });
    expect(world()).not.toContain("scale(1.5)");
    expect(screen.getByRole("button", { name: "Fit view" }).getAttribute("title")).toBe("Fit the whole workflow (F)");
  });

  it("zooms out past 25%, and thins the cards when far out", async () => {
    await open();
    zoomBy("10%");
    expect(world()).toContain("scale(0.1)");
    for (let i = 0; i < 40; i++) fireEvent.wheel(canvas(), { deltaY: 100, clientX: 400, clientY: 300 });
    expect(world()).toContain("scale(0.05)");
    expect(canvas().classList.contains("is-overview")).toBe(true);
  });

  it("opens details without moving the canvas controls", async () => {
    await open();
    expect(document.querySelector(".aw-root")?.classList.contains("has-inspector")).toBe(false);
    editNode("a");
    expect(document.querySelector(".aw-root")?.classList.contains("has-inspector")).toBe(true);
    const css = (await import("node:fs")).readFileSync(`${process.cwd()}/src/theme/styles/automations.css`, "utf8");
    expect(css).not.toMatch(/\.has-inspector\s*\{[^}]*--aw-right-edge/);  // the panel slides over; nothing is pushed
  });

  it("brings what an agent changed into view, selects it when asked and shows its caption", async () => {
    await open();
    size();
    window.dispatchEvent(new CustomEvent("ducky:focus-graph", { detail: { id: "p", nodes: ["b"], select: true, note: "This waits before it continues" } }));
    await waitFor(() => expect(details()).toBeTruthy());
    expect(document.querySelector('[data-aw-node="b"]')?.classList.contains("is-selected")).toBe(true);
    expect(document.querySelector('[data-aw-node="b"]')?.classList.contains("is-flash")).toBe(true);
    expect(document.querySelector(".aw-toast--note")?.textContent).toContain("This waits before it continues");
    expect(world()).toContain("scale(1.5)");
  });
});

describe("snap, background and panel zoom", () => {
  const canvas = () => document.querySelector('.aw-board')!;

  it("snaps a dragged node to the grid, and can be turned off", async () => {
    window.localStorage.clear();
    await open();
    dropdown("Zoom");
    const snap = within(screen.getByRole("menu", { name: "Zoom" })).getByRole("menuitemcheckbox", { name: "Snap to grid" });
    expect(snap.getAttribute("aria-checked")).toBe("true");  // on by default
    fireEvent.keyDown(window, { key: "Escape" });
    const drag = (dx: number, dy: number, pointerId: number) => {
      fireEvent.pointerDown(document.querySelector('[data-aw-node="a"] .aw-node-card')!, { button: 0, pointerId, clientX: 600, clientY: 200 });
      fireEvent.pointerMove(canvas(), { pointerId, clientX: 600 + dx, clientY: 200 + dy });
      fireEvent.pointerUp(canvas(), { pointerId, clientX: 600 + dx, clientY: 200 + dy });
    };
    drag(37, 21, 3);
    await save();
    expect(saved.graph.nodes[1]).toEqual(expect.objectContaining({ x: 320, y: 16 }));  // 317, 21 → the 16px grid
    dropdown("Zoom");
    fireEvent.click(within(screen.getByRole("menu", { name: "Zoom" })).getByRole("menuitemcheckbox", { name: "Snap to grid" }));
    const off = within(screen.getByRole("menu", { name: "Zoom" })).getByRole("menuitemcheckbox", { name: "Snap to grid" });  // the menu stays open
    expect(off.getAttribute("aria-checked")).toBe("false");
    fireEvent.keyDown(window, { key: "Escape" });
    drag(5, 3, 4);
    await save();
    expect(saved.graph.nodes[1]).toEqual(expect.objectContaining({ x: 325, y: 19 }));
    expect(JSON.parse(window.localStorage.getItem("ducky.workflows.view.v1")!).snap).toBe(false);
  });

  it("switches the canvas background between squares, dots and nothing", async () => {
    await open();
    expect(canvas().classList.contains("is-grid-squares")).toBe(true);
    dropdown("Zoom");
    const menu = screen.getByRole("menu", { name: "Zoom" });
    expect(within(menu).getByLabelText("Squares").getAttribute("aria-checked")).toBe("true");
    fireEvent.click(within(menu).getByLabelText("Dots"));
    expect(canvas().classList.contains("is-grid-dots")).toBe(true);
    expect(screen.getByRole("menu", { name: "Zoom" })).toBe(menu);  // stays open while you pick
    fireEvent.click(within(menu).getByLabelText("Nothing"));
    expect(canvas().classList.contains("is-grid-none")).toBe(true);
  });

  it("zooms one panel with Ctrl + scroll and leaves the others alone", async () => {
    await open();
    const root = document.querySelector<HTMLElement>(".aw-root")!;
    const list = screen.getByRole("complementary", { name: "Workflows" });
    fireEvent.wheel(list.querySelector(".aw-list-row")!, { deltaY: -200, ctrlKey: true });
    expect(root.style.getPropertyValue("--aw-z-list")).toBe("1.2");
    expect(root.style.getPropertyValue("--aw-z-toolbar")).toBe("1");
    expect(document.querySelector(".aw-panel-zoom")?.textContent).toBe("120%");
    fireEvent.wheel(screen.getByRole("toolbar", { name: "Workflow actions" }), { deltaY: 100, ctrlKey: true });
    expect(root.style.getPropertyValue("--aw-z-toolbar")).toBe("0.9");
    fireEvent.wheel(list.querySelector(".aw-list-row")!, { deltaY: 100 });  // no Ctrl: a normal scroll
    expect(root.style.getPropertyValue("--aw-z-list")).toBe("1.2");
    expect(JSON.parse(window.localStorage.getItem("ducky.workflows.panelZoom.v1")!)).toMatchObject({ list: 1.2, toolbar: 0.9 });
  });
});

describe("dragging and the list's hover card", () => {
  const canvas = () => document.querySelector('.aw-board')!;

  it("moves a node you drag without selecting it; a plain click selects it", async () => {
    await open();
    const card = () => document.querySelector('[data-aw-node="b"] .aw-node-card')!;
    fireEvent.pointerDown(card(), { button: 0, pointerId: 3, clientX: 600, clientY: 200 });
    fireEvent.pointerMove(canvas(), { pointerId: 3, clientX: 680, clientY: 260 });
    fireEvent.pointerUp(canvas(), { pointerId: 3, clientX: 680, clientY: 260 });
    await save();
    expect(saved.graph.nodes[2]).toEqual(expect.objectContaining({ x: 640, y: 60 }));
    expect(document.querySelector('[data-aw-node="b"]')?.classList.contains("is-selected")).toBe(false);
    expect(details()).toBeNull();  // no details opened by a drag
    fireEvent.pointerDown(card(), { button: 0, pointerId: 4, clientX: 600, clientY: 200 });
    fireEvent.pointerUp(canvas(), { pointerId: 4, clientX: 600, clientY: 200 });
    expect(document.querySelector('[data-aw-node="b"]')?.classList.contains("is-selected")).toBe(true);
    expect(details()).toBeTruthy();
  });

  it("shows a card with the workflow in miniature when a row is hovered, and opens it from there", async () => {
    renderView();
    const row = (await screen.findByText("Example")).closest(".aw-list-row")!;
    fireEvent.mouseEnter(row.parentElement!);
    const card = await screen.findByRole("tooltip");
    await waitFor(() => expect(card.querySelectorAll(".aw-mini-card")).toHaveLength(3));  // one per node
    expect(card.textContent).toContain("Example");
    expect(card.querySelectorAll(".aw-mini-wire")).toHaveLength(1);
    fireEvent.click(within(card).getByRole("button", { name: /Example/ }));
    await screen.findByDisplayValue("Example");
  });
});

describe("node search", () => {
  it("finds nodes by every word typed, in their names and descriptions", async () => {
    api.list_workflow_nodes.mockResolvedValue({ nodes: [
      { type: "start.cron", label: "Schedule", role: "starter", group: "Starting", description: "Fires on a timer while the panel runs." },
      { type: "flow.wait", label: "Wait", group: "Logic", description: "Pause before the next step." },
    ] });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Add nodes" }));
    const menu = screen.getByRole("dialog", { name: "Add node" });
    const names = () => [...menu.querySelectorAll(".aw-tile strong")].map((el) => el.textContent);
    await waitFor(() => expect(names()).toHaveLength(2));
    const all = names();
    fireEvent.change(screen.getByRole("textbox", { name: "Filter nodes" }), { target: { value: "next pause" } });
    expect(names()).toEqual(["Wait"]);  // words from the description, in any order
    fireEvent.change(screen.getByRole("textbox", { name: "Filter nodes" }), { target: { value: "timer schedule" } });
    expect(names()).toEqual(["Schedule"]);  // the name and the description together
    fireEvent.change(screen.getByRole("textbox", { name: "Filter nodes" }), { target: { value: "zzz nothing" } });
    expect(names()).toEqual([]);
    fireEvent.change(screen.getByRole("textbox", { name: "Filter nodes" }), { target: { value: "" } });
    expect(names()).toEqual(all);
  });
});

describe("several workflows, live runs, outline and team sync", () => {
  it("picks several workflows with Ctrl and Shift + click and deletes them after one question", async () => {
    api.list_workflows.mockImplementation(async () => ({ workflows: [
      { id: "p", name: "Example", enabled: true, owner: LOCAL, trigger: { kind: "chat", label: "Chat" } },
      { id: "q", name: "Second", enabled: false, owner: LOCAL, trigger: { kind: "manual", label: "Manual" } },
      { id: "r", name: "Third", enabled: true, owner: LOCAL, trigger: { kind: "manual", label: "Manual" } },
    ] }));
    renderView();
    fireEvent.click(await screen.findByText("Example"));
    await screen.findByDisplayValue("Example");
    const row = (name: string) => screen.getByText(name).closest(".aw-list-row")!;
    fireEvent.click(row("Third"), { ctrlKey: true });
    expect([...document.querySelectorAll(".aw-list-row.is-picked")].map((el) => el.textContent)).toEqual(["Example", "Third"]);
    fireEvent.click(row("Second"), { shiftKey: true });
    expect(document.querySelectorAll(".aw-list-row.is-picked")).toHaveLength(2);  // Third back to Second
    fireEvent.click(row("Example"), { ctrlKey: true });
    fireEvent.contextMenu(row("Second"));
    const menu = screen.getByRole("menu");
    expect([...menu.querySelectorAll('[role^="menuitem"]')].map((el) => el.textContent?.trim())).toEqual(["Duplicate 3", "Turn on 1", "Turn off 2", "Delete 3 workflows"]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Delete 3 workflows" }));
    await screen.findByText("Delete 3 workflows?");
    expect(screen.getByText(/“Second”.*“Third”.*You can't undo this\./)).toBeTruthy();  // it names what goes
    await confirmIt("Delete 3");
    await waitFor(() => expect(api.delete_workflow.mock.calls.map(([id]) => id).sort()).toEqual(["p", "q", "r"]));
  });

  it("lights up the running step and its wire, logs it live, and stops the run at once", async () => {
    let finish: (value: unknown) => void = () => {};
    api.run_workflow.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    api.stop_workflow = vi.fn(async () => ({ ok: true, stopped: true }));
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(api.run_workflow).toHaveBeenCalledWith("p"));
    const push = (event: Record<string, unknown>) => act(() => { window.__uefnPanelPush?.({ id: "p", run: "r1", ...event } as never); });
    push({ type: "workflow_run", state: "started" });
    push({ type: "workflow_step", node: "s", state: "running", label: "Chat input" });
    push({ type: "workflow_step", node: "s", state: "ok" });
    push({ type: "workflow_step", node: "a", state: "running", from: "s", label: "Pause" });
    expect(document.querySelector('[data-aw-node="a"]')?.classList.contains("is-run-running")).toBe(true);
    expect(document.querySelector('[data-aw-node="s"]')?.classList.contains("is-run-ok")).toBe(true);
    expect(document.querySelector(".aw-edge")?.classList.contains("is-live")).toBe(true);  // the wire it is coming along
    expect(document.querySelector(".aw-log-dock")?.textContent).toContain("Pause · running…");
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(api.stop_workflow).toHaveBeenCalledWith("p", "r1"));
    push({ type: "workflow_step", node: "a", state: "stopped" });
    push({ type: "workflow_run", state: "stopped" });
    await act(async () => { finish({ ok: false, error: "Stopped", steps: [{ ok: false, label: "Pause", error: "Stopped" }] }); });
    await screen.findByRole("button", { name: "Test" });
    expect(document.querySelector('[data-aw-node="a"]')?.classList.contains("is-run-stopped")).toBe(true);
  });

  it("glows the current step, greys finished ones, greens the wires taken, and the details follow and say what is happening", async () => {
    saved.graph.edges.push({ source: "a", target: "b", kind: "main" });
    await open();
    const push = (event: Record<string, unknown>) => act(() => { window.__uefnPanelPush?.({ id: "p", run: "release", ...event } as never); });
    push({ type: "workflow_run", state: "started" });
    push({ type: "workflow_step", node: "s", state: "running", label: "Chat" });
    await waitFor(() => expect(details()?.textContent).toContain("Running"));  // nothing was picked: the details follow the run
    push({ type: "workflow_step", node: "s", state: "ok" });
    push({ type: "workflow_step", node: "a", state: "running", from: "s", label: "Pause" });
    const node = (id: string) => document.querySelector(`[data-aw-node="${id}"]`)!;
    const wire = (id: string) => document.querySelector(`[data-aw-wire="${id}:main"]`)!;
    expect(node("a").classList.contains("is-run-running")).toBe(true);
    expect(node("s").classList.contains("is-run-ok")).toBe(true);
    expect(wire("s>a").classList.contains("is-live")).toBe(true);
    await waitFor(() => expect(within(details()!).getByLabelText("This run").textContent).toMatch(/Running · \d+s/));
    expect(details()?.textContent).toContain("Pause");
    push({ type: "workflow_step", node: "a", state: "ok" });
    push({ type: "workflow_step", node: "b", state: "running", from: "a", label: "Continue" });
    push({ type: "workflow_step", node: "b", state: "error", error: "Command failed (exit code 2)." });
    expect(wire("s>a").classList.contains("is-passed")).toBe(true);
    expect(wire("a>b").classList.contains("is-passed")).toBe(true);
    await waitFor(() => expect(details()?.textContent).toContain("Command failed (exit code 2)."));
    expect(within(details()!).getByLabelText("This run").textContent).toMatch(/Failed after \d+s/);
  });

  it("keeps a code step's log apart from its terminal output", async () => {
    await open();
    const push = (event: Record<string, unknown>) => act(() => { window.__uefnPanelPush?.({ id: "p", run: "logs", ...event } as never); });
    push({ type: "workflow_run", state: "started" });
    push({ type: "workflow_step", node: "a", state: "running", from: "s", label: "Pause" });
    push({ type: "workflow_output", node: "a", session_id: "t1", output: "Compiling plugin..." });
    push({ type: "workflow_output", node: "a", source: "log", session_id: "", output: "doubling 21" });
    push({ type: "workflow_output", node: "a", session_id: "t1", output: "Compiling plugin... done" });
    const card = document.querySelector('[data-aw-node="a"]')!;
    expect(card.textContent).toContain("Compiling plugin... done");
    await waitFor(() => expect(details()?.textContent).toContain("doubling 21"));
    expect(within(details()!).getByLabelText("Log").textContent).toContain("doubling 21");
    expect(within(details()!).getByLabelText("Terminal output").textContent).toContain("Compiling plugin... done");
  });

  it("leaves the details alone when you picked another node during a run", async () => {
    await open();
    fireEvent.pointerDown(document.querySelector('[data-aw-node="b"] .aw-node-card')!, { button: 0 });
    fireEvent.pointerUp(document.querySelector('[data-aw-node="b"] .aw-node-card')!, { button: 0 });
    await waitFor(() => expect(details()?.textContent).toContain("Continue"));
    const push = (event: Record<string, unknown>) => act(() => { window.__uefnPanelPush?.({ id: "p", run: "r2", ...event } as never); });
    push({ type: "workflow_run", state: "started" });
    push({ type: "workflow_step", node: "a", state: "running", from: "s", label: "Pause" });
    expect(details()?.textContent).toContain("Continue");
    expect(within(details()!).queryByLabelText("This run")).toBeNull();
  });

  it("shows terminal output on the running node and in its details before completion", async () => {
    await open();
    const push = (event: Record<string, unknown>) => act(() => { window.__uefnPanelPush?.({ id: "p", run: "terminal-run", ...event } as never); });
    push({ type: "workflow_run", state: "started" });
    push({ type: "workflow_step", node: "a", state: "running" });
    push({ type: "workflow_output", node: "a", session_id: "fresh", output: "\u001b[32mCompiling plugin...\u001b[0m\r\n" });
    const card = document.querySelector('[data-aw-node="a"]')!;
    expect(card.textContent).toContain("Compiling plugin...");
    expect(card.textContent).not.toContain("\u001b");
    expect(card.classList.contains("is-run-running")).toBe(true);
    fireEvent.pointerDown(card.querySelector(".aw-node-card")!, { button: 0 });
    fireEvent.pointerUp(card.querySelector(".aw-node-card")!, { button: 0 });
    await waitFor(() => expect(details()?.textContent).toContain("Compiling plugin..."));
    push({ type: "workflow_output", node: "a", output: "Upload completed", session_id: "fresh" });
    expect(card.textContent).toContain("Upload completed");
    push({ type: "workflow_output", run: "older-run", node: "a", output: "stale output" });
    expect(card.textContent).not.toContain("stale output");
  });

  it("output and steps of a run it never saw start (a Code-tab Test) leave the toolbar on Test", async () => {
    await open();
    const push = (event: Record<string, unknown>) => act(() => { window.__uefnPanelPush?.({ id: "p", run: "draft-1a2b3c4d", ...event } as never); });
    push({ type: "workflow_output", node: "a", source: "log", session_id: "", output: "Got hi" });
    push({ type: "workflow_step", node: "a", state: "running", label: "Pause" });
    expect(screen.getByRole("button", { name: "Test" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    const card = document.querySelector('[data-aw-node="a"]')!;
    expect(card.classList.contains("is-run-running")).toBe(false);
    expect(card.textContent).not.toContain("Got hi");
  });

  it("lists every group and node in the outline and brings the picked one into view", async () => {
    saved.graph.groups = [{ id: "g", name: "Setup", node_ids: ["s", "a"] }];
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Outline" }));
    const outline = screen.getByRole("dialog", { name: "Outline" });
    expect([...outline.querySelectorAll(".aw-outline-name")].map((el) => el.textContent)).toEqual(["Setup", "Chat input", "Pause", "Continue"]);
    fireEvent.click(within(outline).getByText("Setup"));
    expect(screen.queryByRole("dialog", { name: "Outline" })).toBeNull();
    expect(document.querySelector('[data-aw-node="s"]')?.classList.contains("is-selected")).toBe(true);
    expect(details()).toBeTruthy();
  });

  it("syncs a team once when opened and then only after a save, never on a timer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      saved = { ...structuredClone(daily) };
      api.get_workflow.mockImplementation(async () => ({ ok: true, workflow: structuredClone(saved) }));
      api.save_workflow.mockImplementation(async (doc: AutomationDto) => ({ ok: true, workflow: { ...structuredClone(doc), owner: TEAM } }));
      renderView();
      fireEvent.click(await screen.findByText("Daily check"));
      await screen.findByDisplayValue("Daily check");
      await waitFor(() => expect(api.workflow_sync).toHaveBeenCalledTimes(1));
      await act(async () => { vi.advanceTimersByTime(5 * 60_000); });
      expect(api.workflow_sync).toHaveBeenCalledTimes(1);  // no polling
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(api.workflow_sync).toHaveBeenLastCalledWith(true, "teamT", true));
      expect(api.workflow_sync).toHaveBeenCalledTimes(2);  // open, then Save updates the team now
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("nested groups from the details panel", () => {
  const canvas = () => document.querySelector('.aw-board')!;
  const node = (id: string) => document.querySelector(`.aw-node[data-aw-node="${id}"]`)!;
  const ctrlClick = (id: string) => {
    fireEvent.pointerDown(node(id), { button: 0, ctrlKey: true, pointerId: 7 });
    fireEvent.pointerUp(canvas(), { button: 0, ctrlKey: true, pointerId: 7 });
  };

  it("groups a group with another node from the panel, draws it inside, then opens one level", async () => {
    await open();
    expect(details()).toBeNull();
    ctrlClick("s"); ctrlClick("a");
    fireEvent.click(screen.getByRole("button", { name: "Group selection" }));
    await waitFor(() => expect(saved.graph.groups).toEqual([expect.objectContaining({ name: "Group", node_ids: ["s", "a"] })]));
    const inner = saved.graph.groups![0].id;
    fireEvent.pointerDown(screen.getByRole("button", { name: "Select group Group" }), { button: 0, pointerId: 5 });
    fireEvent.pointerUp(canvas(), { pointerId: 5 });
    ctrlClick("b");
    fireEvent.click(screen.getByRole("button", { name: "Group selection" }));
    await waitFor(() => expect(saved.graph.groups).toHaveLength(2));
    const outer = saved.graph.groups!.find((group) => group.id !== inner)!;
    expect(outer).toEqual(expect.objectContaining({ name: "Group 2", node_ids: ["b"] }));
    expect(saved.graph.groups!.find((group) => group.id === inner)!.parent_id).toBe(outer.id);
    const outerBox = document.querySelector(`[data-aw-group="${outer.id}"]`) as HTMLElement;
    const innerBox = document.querySelector(`[data-aw-group="${inner}"]`) as HTMLElement;
    expect(innerBox.classList.contains("is-nested")).toBe(true);
    expect(parseFloat(outerBox.style.top)).toBeLessThanOrEqual(parseFloat(innerBox.style.top) - 44 - 24);  // room for the inner title
    expect(outerBox.compareDocumentPosition(innerBox) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();  // inner paints on top
    fireEvent.click(screen.getByRole("button", { name: "Ungroup", exact: true }));
    await waitFor(() => expect(saved.graph.groups).toEqual([{ id: inner, name: "Group", node_ids: ["s", "a"] }]));
  });
});

describe("reusable workflows", () => {
  const fn = { id: "fn", name: "Greeter", enabled: true, owner: LOCAL, trigger: { kind: "function" as const, label: "Function" },
    signature: { inputs: [{ name: "who", default: "world" }], outputs: ["greeting"] } };
  beforeEach(() => {
    api.list_workflows.mockImplementation(async () => ({ workflows: [
      { id: "p", name: "Example", enabled: true, owner: saved.owner, trigger: { kind: "chat", label: "Chat" } }, fn,
    ] }));
    api.list_workflow_nodes.mockResolvedValue({ nodes: [
      { type: "start.chat", label: "Chat", role: "starter", group: "Starting" },
      { type: "flow.input", label: "Inputs", role: "starter", group: "Functions", config_fields: [{ id: "inputs", label: "Inputs", type: "params" }] },
      { type: "workflow.call", label: "Run workflow", group: "Functions", config_fields: [{ id: "workflow_id", label: "Workflow", type: "workflow" }] },
      { type: "flow.wait", label: "Wait", group: "Logic" },
    ] });
  });

  it("offers each reusable workflow as a node that runs it", async () => {
    await open();
    expect(screen.getByRole("img", { name: "Function" }).getAttribute("title")).toContain("Run workflow");
    fireEvent.click(screen.getByRole("button", { name: "Add nodes" }));
    const menu = screen.getByRole("dialog", { name: "Add node" });
    const tile = [...menu.querySelectorAll(".aw-tile")].find((el) => el.textContent?.includes("Greeter"))!;
    expect(tile.textContent).toContain("Takes who · returns greeting");
    fireEvent.click(tile);
    await save();
    const added = saved.graph.nodes.find((item) => item.type === "workflow.call")!;
    expect(added).toEqual(expect.objectContaining({ label: "Greeter", config: { workflow_id: "fn", args: {} } }));
  });

  it("fills the called workflow's inputs, lists what it returns and opens it", async () => {
    saved.graph.nodes[1] = { id: "a", type: "workflow.call", label: "Greeter", x: 280, y: 0, config: { workflow_id: "fn", args: {} } };
    renderView();
    fireEvent.click(await screen.findByText("Example"));
    await screen.findByRole("button", { name: "Connect from Greeter" });
    editNode();
    const who = screen.getByRole("textbox", { name: "who" });
    expect(who.getAttribute("placeholder")).toBe("Default: world");
    fireEvent.change(who, { target: { value: "{{name}}" } });
    expect(details()?.textContent).toContain("Next steps can also use greeting as fields.");
    await save();
    expect(saved.graph.nodes[1].config).toEqual({ workflow_id: "fn", args: {}, inputs: { who: "{{name}}" } });
    fireEvent.click(screen.getByRole("button", { name: "Open Greeter" }));
    await waitFor(() => expect(api.get_workflow).toHaveBeenLastCalledWith("fn"));
  });

  it("opens the workflow a Run workflow node runs on double-click, like an Unreal function", async () => {
    saved.graph.nodes[1] = { id: "a", type: "workflow.call", label: "Greeter", x: 280, y: 0, config: { workflow_id: "fn", args: {} } };
    renderView();
    fireEvent.click(await screen.findByText("Example"));
    await screen.findByRole("button", { name: "Connect from Greeter" });
    fireEvent.doubleClick(document.querySelector('[data-aw-node="a"] .aw-node-card')!);
    await waitFor(() => expect(api.get_workflow).toHaveBeenLastCalledWith("fn"));
  });

  it("edits an Inputs node's list", async () => {
    saved.graph.nodes[0] = { id: "s", type: "flow.input", label: "Inputs", x: 0, y: 0, config: { inputs: [{ name: "who", default: "world" }] } };
    await open();
    editNode("s");
    fireEvent.click(screen.getByRole("button", { name: "Add input" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name 2" }), { target: { value: "score max" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Default 2" }), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Remove who" }));
    await save();
    expect(saved.graph.nodes[0].config.inputs).toEqual([{ name: "score_max", default: "10" }]);
  });

  it("shows the called workflow's steps and the returned values in the run log", async () => {
    api.run_workflow.mockResolvedValue({ ok: true, outputs: { greeting: "Hello" }, steps: [
      { label: "Greeter", ok: true, substeps: [{ label: "Inputs", ok: true }, { label: "Return", ok: true }] },
    ] });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Test", exact: true }));
    await screen.findByText("Returned: greeting = Hello");
    expect([...document.querySelectorAll(".aw-log-substeps li")].map((li) => li.textContent)).toEqual(["Inputs ok", "Return ok"]);
  });
});

describe("folders in the Workflows list", () => {
  beforeEach(() => {
    window.localStorage.clear();
    api.list_workflows.mockImplementation(async () => ({ workflows: [
      { id: "p", name: "Example", enabled: true, owner: LOCAL, folder: "Tests/Smoke", trigger: { kind: "chat", label: "Chat" } },
      { id: "d", name: "Daily check", enabled: true, owner: TEAM, run_here: false, trigger: { kind: "schedule", label: "Every 5m" } },
    ] }));
    api.set_workflow_folder.mockImplementation(async (id: string, folder: string) => ({ ok: true, workflow: { ...structuredClone(saved), id, folder } }));
    api.move_workflow_folder.mockResolvedValue({ ok: true, moved: 1 });
    api.add_workflow_folder.mockImplementation(async (_owner: string, path: string) => ({ ok: true, folders: [path] }));
    api.copy_workflow_folder.mockResolvedValue({ ok: true, folder: "Tests", moved: 0, outside: [], workflows: [{ key: "p", id: "p2", name: "Example" }] });
  });
  const transfer = () => ({ setData: vi.fn(), getData: vi.fn(), effectAllowed: "", dropEffect: "", types: ["text/plain"] });
  const folderRow = (name: string) => screen.getByRole("button", { name: `Folder ${name}` }).closest(".aw-tree-folder")!;

  it("nests folders inside the owner and collapses them", async () => {
    renderView();
    const tests = await screen.findByRole("button", { name: "Folder Tests" });
    expect(tests.textContent).toContain("1");
    expect(screen.getByRole("button", { name: "Folder Smoke" })).toBeTruthy();
    expect(document.getElementById(tests.getAttribute("aria-controls")!)!.contains(screen.getByText("Example"))).toBe(true);
    fireEvent.click(tests);
    expect(screen.getByText("Example").closest("[hidden]")).toBeTruthy();
  });

  it("makes an empty folder the host keeps, and files a workflow by dragging", async () => {
    renderView();
    await screen.findByText("Example");
    fireEvent.click(screen.getByRole("button", { name: "New folder in Local" }));
    const input = screen.getByRole("textbox", { name: "New folder name" });
    fireEvent.change(input, { target: { value: " QA " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByRole("button", { name: "Folder QA" })).toBeTruthy();
    expect(screen.getByText("Empty. Drag workflows here.")).toBeTruthy();
    await waitFor(() => expect(api.add_workflow_folder).toHaveBeenCalledWith("local", "QA"));
    expect(window.localStorage.getItem("ducky.workflows.emptyFolders.v1")).toBeNull();  // the host keeps it, not this browser
    const dataTransfer = transfer();
    fireEvent.dragStart(screen.getByText("Example").closest("button")!, { dataTransfer });
    expect(dataTransfer.setData).toHaveBeenCalledWith("application/x-ducky-workflow-list", "workflow");
    fireEvent.dragOver(folderRow("QA"), { dataTransfer });
    expect(folderRow("QA").classList.contains("is-drop-target")).toBe(true);
    fireEvent.drop(folderRow("QA"), { dataTransfer });
    await waitFor(() => expect(api.set_workflow_folder).toHaveBeenCalledWith("p", "QA"));
    // Its old folder stays until removed; a team folder never takes a Local workflow.
    expect(await screen.findByRole("button", { name: "Folder Smoke" })).toBeTruthy();
    fireEvent.dragStart(screen.getByText("Example").closest("button")!, { dataTransfer });
    fireEvent.dragOver(screen.getByRole("button", { name: "Team · Alpha Studio" }).parentElement!, { dataTransfer });
    fireEvent.drop(screen.getByRole("button", { name: "Team · Alpha Studio" }).parentElement!, { dataTransfer });
    expect(api.set_workflow_folder).toHaveBeenCalledTimes(1);
  });

  it("renames, moves and removes folders with the workflows in them", async () => {
    renderView();
    await screen.findByText("Example");
    fireEvent.click(screen.getByRole("button", { name: "Rename Tests" }));
    const input = screen.getByRole("textbox", { name: "Folder name" });
    fireEvent.change(input, { target: { value: "Checks" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(api.move_workflow_folder).toHaveBeenCalledWith("local", "Tests", "Checks"));
    fireEvent.click(screen.getByRole("button", { name: "Remove folder Smoke" }));
    await confirmIt("Remove");
    await waitFor(() => expect(api.move_workflow_folder).toHaveBeenLastCalledWith("local", "Tests/Smoke", "Tests"));
    const dataTransfer = transfer();
    fireEvent.dragStart(folderRow("Smoke"), { dataTransfer });
    fireEvent.dragOver(screen.getByRole("button", { name: "Local" }).parentElement!, { dataTransfer });
    fireEvent.drop(screen.getByRole("button", { name: "Local" }).parentElement!, { dataTransfer });
    await waitFor(() => expect(api.move_workflow_folder).toHaveBeenLastCalledWith("local", "Tests/Smoke", "Smoke"));
  });

  it("shows every folder the host keeps, empty ones and a team's too", async () => {
    owners = { ...owners, owners: [{ ...LOCAL, folders: ["Tests", "Tests/Smoke", "Later"] }, { ...TEAM, folders: ["Shared/Empty"] }] };
    renderView();
    expect(await screen.findByRole("button", { name: "Folder Later" })).toBeTruthy();
    const team = screen.getByRole("region", { name: "Team · Alpha Studio" });
    expect(within(team).getByRole("button", { name: "Folder Shared" })).toBeTruthy();
    expect(within(team).getByRole("button", { name: "Folder Empty" })).toBeTruthy();
  });

  it("hands empty folders an older build kept in this browser to the host once", async () => {
    window.localStorage.setItem("ducky.workflows.emptyFolders.v1", JSON.stringify({ local: ["Old/Empty"], teamGone: ["Elsewhere"] }));
    renderView();
    await waitFor(() => expect(api.add_workflow_folder).toHaveBeenCalledWith("local", "Old/Empty"));
    expect(api.add_workflow_folder).not.toHaveBeenCalledWith("teamGone", "Elsewhere");
    expect(JSON.parse(window.localStorage.getItem("ducky.workflows.emptyFolders.v1") || "{}")).toEqual({ teamGone: ["Elsewhere"] });
  });

  it("copies a whole folder to a team after saying what goes, and moves one back", async () => {
    renderView();
    await screen.findByText("Example");
    fireEvent.contextMenu(screen.getByRole("button", { name: "Folder Tests" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy folder to Team · Alpha Studio" }));
    expect(await screen.findByText("Copy “Tests” to Team · Alpha Studio?")).toBeTruthy();
    expect(screen.getByText(/1 workflow and 1 folder go along, nested as they are\..*Every member of Alpha Studio gets the copy\./)).toBeTruthy();
    expect(api.copy_workflow_folder).not.toHaveBeenCalled();
    await confirmIt("Copy");
    await waitFor(() => expect(api.copy_workflow_folder).toHaveBeenCalledWith("local", "Tests", "teamT", "", false));

    api.copy_workflow_folder.mockResolvedValueOnce({ ok: true, folder: "Tests", moved: 1, outside: ["elsewhere"], workflows: [{ key: "p", id: "p", name: "Example" }] });
    fireEvent.contextMenu(screen.getByRole("button", { name: "Folder Smoke" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Move folder to Team · Alpha Studio" }));
    expect(await screen.findByText("Move “Smoke” to Team · Alpha Studio?")).toBeTruthy();
    await confirmIt("Move");
    await waitFor(() => expect(api.copy_workflow_folder).toHaveBeenLastCalledWith("local", "Tests/Smoke", "teamT", "", true));
    expect(await screen.findByText(/1 Run workflow step in “Smoke” calls workflows outside it/)).toBeTruthy();
  });

  it("offers only a copy out of a team this member can't change", async () => {
    owners = { ...owners, owners: [LOCAL, { ...TEAM, readOnly: true, reason: "Only members with Manage automations can change team workflows." }] };
    api.list_workflows.mockImplementation(async () => ({ workflows: [
      { id: "d", name: "Daily check", enabled: true, owner: { ...TEAM, readOnly: true }, folder: "Ops", trigger: { kind: "schedule", label: "Every 5m" } },
    ] }));
    renderView();
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Folder Ops" }));
    const items = [...screen.getByRole("menu").querySelectorAll('[role^="menuitem"]')].map((el) => el.textContent?.trim());
    expect(items).toEqual(["Read-only", "Copy folder to Local", "Save folder as template"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy folder to Local" }));
    expect(await screen.findByText("Copy “Ops” to Local?")).toBeTruthy();
    await confirmIt("Copy");
    await waitFor(() => expect(api.copy_workflow_folder).toHaveBeenCalledWith("teamT", "Ops", "local", "", false));
  });

  it("creates a workflow in a folder and moves the open one from the toolbar", async () => {
    renderView();
    await screen.findByText("Example");
    fireEvent.click(screen.getByRole("button", { name: "New workflow in Smoke" }));
    fireEvent.click(screen.getByRole("button", { name: "Create workflow" }));
    await waitFor(() => expect(api.save_workflow).toHaveBeenCalledWith(expect.objectContaining({ id: "", folder: "Tests/Smoke" }), "local"));
    fireEvent.click(screen.getByText("Example"));
    await screen.findByRole("button", { name: "Connect from Pause" });
    dropdown("Move or copy");
    expect(screen.queryByRole("radio", { name: "Move to Tests/Smoke" })).toBeNull();  // where it already is
    fireEvent.click(await screen.findByRole("radio", { name: "Move to Tests" }));
    await waitFor(() => expect(api.set_workflow_folder).toHaveBeenCalledWith("p", "Tests"));
    await save();
    expect(api.save_workflow.mock.calls.at(-1)![0]).not.toHaveProperty("folder");  // saving a graph never refiles it
  });
});

describe("folder templates in the Workflows list", () => {
  beforeEach(() => {
    api.list_workflows.mockImplementation(async () => ({ workflows: [
      { id: "p", name: "Example", enabled: true, owner: LOCAL, folder: "Tests/Smoke", trigger: { kind: "chat", label: "Chat" } },
    ] }));
    api.use_workflow_template.mockResolvedValue({ ok: true, shape: "bundle", folder: "Tests/Kit", owner: "local", main: "p", outside: ["@gone"],
      workflows: [{ key: "main", id: "p", name: "Example" }] });
  });

  it("opens Save folder as template for the folder right-clicked", async () => {
    renderView();
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Folder Tests" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Save folder as template" }));
    expect(await screen.findByText("Keep local/Tests as a template")).toBeTruthy();
  });

  it("makes a folder template's whole tree where it is asked to go, then opens its main workflow", async () => {
    renderView();
    await screen.findByText("Example");
    fireEvent.click(screen.getByRole("button", { name: "New workflow in Smoke" }));
    expect(screen.getByText("Goes in “Tests/Smoke”")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Put it in Tests" }));
    fireEvent.click(screen.getByRole("button", { name: "Use folder template" }));
    await waitFor(() => expect(api.use_workflow_template).toHaveBeenCalledWith("custom:kit12345", "local", "Tests", ""));
    expect(api.save_workflow).not.toHaveBeenCalled();
    await waitFor(() => expect(api.get_workflow).toHaveBeenCalledWith("p"));
    expect(await screen.findByText(/1 Run workflow step in “Kit” calls workflows that are not in it/)).toBeTruthy();
  });
});

describe("right-click menus in the Workflows list", () => {
  const items = () => [...screen.getByRole("menu").querySelectorAll('[role^="menuitem"]')].map((el) => el.textContent?.trim());

  it("renames, switches off, duplicates and deletes a workflow from its menu", async () => {
    renderView();
    fireEvent.contextMenu(await screen.findByText("Example"));
    expect(items()).toEqual(["Open", "Rename", "Duplicate", "On", "Delete"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Rename workflow" });
    fireEvent.change(input, { target: { value: "Renamed example" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(api.save_workflow).toHaveBeenCalledWith(expect.objectContaining({ id: "p", name: "Renamed example" })));
    expect(api.save_workflow.mock.calls.at(-1)![0]).not.toHaveProperty("folder");  // renaming never refiles it
    fireEvent.contextMenu(screen.getByText("Example"));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "On" }));
    await waitFor(() => expect(api.save_workflow).toHaveBeenLastCalledWith(expect.objectContaining({ id: "p", enabled: false })));
    fireEvent.contextMenu(screen.getByText("Example"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    await waitFor(() => expect(api.save_workflow).toHaveBeenLastCalledWith(expect.objectContaining({ id: "", name: "Renamed example copy", folder: "" }), "local"));
    fireEvent.contextMenu(screen.getByText("Example"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await screen.findByText("Delete “Example”?");
    expect(api.delete_workflow).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^Delete \(/ }));
    await waitFor(() => expect(api.delete_workflow).toHaveBeenCalledWith("p"));
  });

  it("renames the open workflow through the editor so the toolbar follows", async () => {
    await open();
    fireEvent.contextMenu(screen.getAllByText("Example").find((el) => el.closest(".aw-list-row"))!);
    expect(items()).toEqual(["Rename", "Duplicate", "On", "Delete"]);  // it is already open
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Rename workflow" }), { target: { value: "Island check" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Rename workflow" }), { key: "Enter" });
    await waitFor(() => expect(saved.name).toBe("Island check"));
    expect(screen.getByDisplayValue("Island check")).toBeTruthy();
  });

  it("offers folder and owner actions, and draws a line for each folder level", async () => {
    window.localStorage.clear();
    api.list_workflows.mockImplementation(async () => ({ workflows: [
      { id: "p", name: "Example", enabled: true, owner: LOCAL, folder: "Tests/Smoke", trigger: { kind: "chat", label: "Chat" } },
    ] }));
    api.set_workflow_folder.mockImplementation(async (id: string, folder: string) => ({ ok: true, workflow: { ...structuredClone(saved), id, folder } }));
    renderView();
    await screen.findByText("Example");
    const levels = [...document.querySelectorAll<HTMLElement>(".aw-tree-children")].map((ul) => ul.style.getPropertyValue("--aw-level"));
    expect(levels).toEqual(["1", "2"]);  // one step per level under Local
    fireEvent.contextMenu(screen.getByText("Example"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Move out of folder" }));
    await waitFor(() => expect(api.set_workflow_folder).toHaveBeenCalledWith("p", "Tests"));
    fireEvent.contextMenu(screen.getByRole("button", { name: "Folder Smoke" }));
    expect(items()).toEqual(["New workflow here", "New folder inside", "Rename", "Copy folder to Team · Alpha Studio", "Move folder to Team · Alpha Studio", "Save folder as template", "Remove folder (keeps its workflows)"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    expect((screen.getByRole("textbox", { name: "Folder name" }) as HTMLInputElement).value).toBe("Smoke");
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Folder name" }), { key: "Escape" });
    fireEvent.contextMenu(screen.getByRole("button", { name: "Local", exact: true }));
    expect(items()).toEqual(["New workflow", "New folder"]);
  });
});

describe("group to reusable workflow", () => {
  const canvas = () => document.querySelector('.aw-board')!;
  const node = (id: string) => document.querySelector(`.aw-node[data-aw-node="${id}"]`)!;
  const ctrlClick = (id: string) => {
    fireEvent.pointerDown(node(id), { button: 0, ctrlKey: true, pointerId: 7 });
    fireEvent.pointerUp(canvas(), { button: 0, ctrlKey: true, pointerId: 7 });
  };
  async function groupAndSelect(ids: string[]) {
    await open();
    ids.forEach(ctrlClick);
    fireEvent.click(screen.getByRole("button", { name: "Group selection" }));
    await waitFor(() => expect(saved.graph.groups).toHaveLength(1));
    fireEvent.pointerDown(screen.getByRole("button", { name: "Select group Group" }), { button: 0, pointerId: 5 });
    fireEvent.pointerUp(canvas(), { pointerId: 5 });
  }

  it("moves the group into a new workflow and runs it from where the group was", async () => {
    saved.graph.edges.push({ source: "a", target: "b", kind: "main" });
    await groupAndSelect(["a", "b"]);
    fireEvent.click(screen.getByRole("button", { name: "Make reusable" }));
    await waitFor(() => expect(saved.graph.nodes.some((item) => item.type === "workflow.call")).toBe(true));
    const made = api.save_workflow.mock.calls.find(([doc]) => !doc.id)![0];
    expect(made).toEqual(expect.objectContaining({ name: "Example · Group", folder: "" }));
    expect(made.graph.nodes.map((item: { type: string }) => item.type)).toEqual(["flow.input", "flow.wait", "flow.wait"]);
    expect(api.save_workflow.mock.calls.find(([doc]) => !doc.id)![1]).toBe("local");
    const call = saved.graph.nodes.find((item) => item.type === "workflow.call")!;
    expect(call.config).toEqual({ workflow_id: "new-workflow", args: {}, share: true });
    expect(saved.graph.nodes.map((item) => item.id)).toEqual(["s", call.id]);
    expect(saved.graph.edges).toEqual([{ source: "s", target: call.id, kind: "main" }]);
    expect(saved.graph.groups).toEqual([]);
  });

  it("explains why a group with a start can't move", async () => {
    await groupAndSelect(["s", "a"]);
    fireEvent.click(screen.getByRole("button", { name: "Make reusable" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Starts");
    expect(api.save_workflow.mock.calls.some(([doc]) => !doc.id)).toBe(false);
  });
});

describe("image nodes", () => {
  const url = "http://127.0.0.1:4199/workflow-media/sig/tok/duck.png";
  beforeEach(() => {
    saved.graph = {
      nodes: [
        { id: "q", type: "input.text", x: 0, y: 0, config: { value: "a duck" } },
        { id: "gen", type: "image.generate", x: 300, y: 0, config: {} },
        { id: "show", type: "util.preview", x: 600, y: 0, config: {} },
      ],
      edges: [
        { source: "q", target: "gen", kind: "data", source_pin: "text", target_pin: "prompt" },
        { source: "gen", target: "show", kind: "data", source_pin: "image", target_pin: "value" },
      ],
    };
    api.list_workflow_nodes.mockResolvedValue({ nodes: [
      { type: "input.text", label: "Input Text", group: "Inputs", role: "input", exec: false, outputs: [{ id: "text", label: "Text", type: "text" }] },
      { type: "image.generate", label: "Text to Image", group: "Images", exec: false, paid: true,
        inputs: [{ id: "prompt", label: "Prompt", type: "text", required: true }], outputs: [{ id: "image", label: "Image", type: "image" }],
        config_fields: [{ id: "backend", label: "Backend", type: "backend" }, { id: "spend", label: "Spend credits", type: "boolean" }],
        backends: [{ id: "gemini25flash", label: "Gemini 2.5 Flash Image", plugin: "3D AI Studio", credits: 5, available: true }] },
      { type: "util.preview", label: "Preview", group: "Utility", role: "end", exec: false, inputs: [{ id: "value", label: "Value", type: "any" }] },
    ] });
  });
  async function openPipe() {
    renderView();
    fireEvent.click(await screen.findByText("Example"));
    await waitFor(() => expect(document.querySelector('[data-aw-node="gen"] .aw-node-thumb')).toBeTruthy());
  }

  it("shows the picture an image node made on its card and in Preview", async () => {
    api.run_workflow.mockResolvedValue({ ok: true, steps: [], node_outputs: {
      gen: { image: { kind: "image", path: "C:/runs/duck.png", name: "duck.png", url } },
      show: { value: { kind: "image", path: "C:/runs/duck.png", name: "duck.png", url } },
    } });
    await openPipe();
    expect(document.querySelector('[data-aw-node="gen"] .aw-node-thumb')?.textContent).toContain("Press play to make one");
    fireEvent.click(screen.getByRole("button", { name: "Test", exact: true }));
    await waitFor(() => expect(document.querySelector('[data-aw-node="gen"] .aw-node-thumb img')?.getAttribute("src")).toBe(url));
    expect(document.querySelector('[data-aw-node="show"] .aw-node-thumb img')?.getAttribute("src")).toBe(url);
    const height = parseFloat((document.querySelector('[data-aw-node="gen"]') as HTMLElement).style.height);
    expect(height).toBeGreaterThan(150);  // room for the picture under the pin rows
  });

  it("runs one node from its details and shows what it made", async () => {
    api.run_workflow_node.mockResolvedValue({ ok: true, steps: [{ label: "Text to Image", ok: true }], node_outputs: {
      gen: { image: { kind: "image", path: "C:/runs/again.png", name: "again.png", url: url.replace("duck", "again") } },
    } });
    await openPipe();
    editNode("gen");
    fireEvent.click(within(details()!).getByRole("button", { name: "Run this node" }));
    await waitFor(() => expect(api.run_workflow_node).toHaveBeenCalledWith("p", "gen", true));  // pressing play is the approval
    expect(api.save_workflow).not.toHaveBeenCalled();  // nothing unsaved here: a change made elsewhere isn't put back
    await waitFor(() => expect(document.querySelector('[data-aw-node="gen"] .aw-node-thumb img')?.getAttribute("src")).toContain("again.png"));
    expect(within(details()!).getByText("again.png")).toBeTruthy();  // Last run shows the file
  });

  it("shows the run live while one node runs, lists only inputs you can type, and folds Last run", async () => {
    let finish: (value: unknown) => void = () => undefined;
    api.run_workflow_node.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await openPipe();
    editNode("gen");
    expect(within(details()!).queryByText("Inputs")).toBeNull();  // its Prompt is wired: the wire shows it
    fireEvent.click(within(details()!).getByRole("button", { name: "Run this node" }));
    await waitFor(() => expect(document.querySelector(".aw-log-dock-body")?.textContent).toContain("Starting…"));
    expect(screen.getByLabelText("Running")).toBeTruthy();  // the Run log button says so too
    finish({ ok: true, steps: [{ label: "Text to Image", ok: true }], node_outputs: {
      gen: { image: { kind: "image", path: "C:/runs/again.png", name: "again.png", url } },
    } });
    await waitFor(() => expect(document.querySelector(".aw-log-dock-body")?.textContent).toContain("Text to Image ok"));
    expect(screen.queryByLabelText("Running")).toBeNull();
    const lastRun = document.querySelector(".aw-last-run") as HTMLDetailsElement;
    expect(lastRun && lastRun.open).toBe(false);
  });

  it("puts Try again and Use this on the card that made the picture", async () => {
    const keep = vi.fn().mockResolvedValue({ ok: true, steps: [{ label: "Save to card", ok: true }], node_outputs: {} });
    (api as unknown as { keep_workflow_preview: typeof keep }).keep_workflow_preview = keep;
    api.run_workflow_node.mockResolvedValue({ ok: true, steps: [], node_outputs: {
      gen: { image: { kind: "image", path: "C:/runs/duck.png", name: "duck.png", url } },
    } });
    await openPipe();
    const card = document.querySelector('[data-aw-node="gen"]') as HTMLElement;
    expect(within(card).queryByRole("button", { name: /Use this/ })).toBeNull();  // nothing made yet
    editNode("gen");
    fireEvent.click(within(details()!).getByRole("button", { name: "Run this node" }));
    await waitFor(() => expect(within(card).getByRole("button", { name: /Use this/ })).toBeTruthy());
    expect(card.querySelector(".aw-node-thumb img")?.getAttribute("src")).toBe(url);
    fireEvent.click(within(card).getByRole("button", { name: /Use this/ }));
    await waitFor(() => expect(keep).toHaveBeenCalledWith("p", "gen"));
    fireEvent.click(within(card).getByRole("button", { name: /Try again/ }));
    await waitFor(() => expect(api.run_workflow_node).toHaveBeenCalledTimes(2));
    expect(api.run_workflow_node).toHaveBeenLastCalledWith("p", "gen", true);
  });

  it("folds a node's Settings, and folded stays folded", async () => {
    await openPipe();
    editNode("gen");
    const fold = () => details()!.querySelector("details.aw-insp-settings") as HTMLDetailsElement;
    expect(fold().open).toBe(true);
    fold().open = false;
    fireEvent(fold(), new Event("toggle"));
    expect(window.localStorage.getItem("ducky.workflows.settingsOpen")).toBe("0");
  });

  it("keeps showing a card's last picture after a later run of it failed", async () => {
    saved.runs = [
      { ok: true, steps: [], node_outputs: { gen: { image: { kind: "image", path: "C:/runs/duck.png", name: "duck.png", url } } } },
      { ok: false, error: "Card picture: no key", steps: [], node_outputs: { q: { text: "a duck" } } },
    ];
    await openPipe();
    expect(document.querySelector('[data-aw-node="gen"] .aw-node-thumb img')?.getAttribute("src")).toBe(url);
  });

  it("loads a change made outside into the open canvas and never sends the old one back", async () => {
    saved.updated = 1;
    api.list_workflows.mockImplementation(async () => ({ workflows: [{ id: "p", name: "Example", enabled: true, owner: saved.owner, updated: saved.updated, trigger: { kind: "chat", label: "Chat" } }] }));
    await openPipe();
    saved = { ...structuredClone(saved), updated: 2, graph: { ...saved.graph, nodes: saved.graph.nodes.filter((node) => node.id !== "show") } };
    await act(async () => { window.__uefnPanelPush?.({ type: "graphs_changed" }); });
    await waitFor(() => expect(document.querySelector('[data-aw-node="show"]')).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Test", exact: true }));
    await waitFor(() => expect(api.run_workflow).toHaveBeenCalledWith("p"));
    expect(api.save_workflow).not.toHaveBeenCalled();
  });

  it("keeps the previewed picture or makes another from the Preview card", async () => {
    const shown = { show: { value: { kind: "image", path: "C:/runs/duck.png", name: "duck.png", url } } };  // reused, still on screen
    const keep = vi.fn().mockResolvedValue({ ok: true, steps: [{ label: "Save to card", ok: true }], node_outputs: shown });
    (api as unknown as { keep_workflow_preview: typeof keep }).keep_workflow_preview = keep;
    api.run_workflow.mockResolvedValue({ ok: true, steps: [], node_outputs: {
      gen: { image: { kind: "image", path: "C:/runs/duck.png", name: "duck.png", url } },
      show: { value: { kind: "image", path: "C:/runs/duck.png", name: "duck.png", url } },
    } });
    api.run_workflow_node.mockResolvedValue({ ok: true, steps: [], node_outputs: shown });
    await openPipe();
    expect(screen.queryByRole("button", { name: "Use this" })).toBeNull();  // nothing to keep yet
    fireEvent.click(screen.getByRole("button", { name: "Test", exact: true }));
    const card = await waitFor(() => { const el = document.querySelector('[data-aw-node="show"]') as HTMLElement; expect(within(el).getByRole("button", { name: /Use this/ })).toBeTruthy(); return el; });
    fireEvent.click(within(card).getByRole("button", { name: /Use this/ }));
    await waitFor(() => expect(keep).toHaveBeenCalledWith("p", "show"));
    fireEvent.click(within(card).getByRole("button", { name: /Try again/ }));
    await waitFor(() => expect(api.run_workflow_node).toHaveBeenCalledWith("p", "show", true));
  });
});

describe("no workflows yet", () => {
  it("offers one obvious New workflow and a few ready-made pipelines", async () => {
    api.list_workflows.mockResolvedValue({ workflows: [] });
    const templates = [
      { id: "builtin:pipe-prompt-image", name: "Prompt to picture", icon: "🖼️", category: "Images", kind: "builtin", ready: true, graph: { nodes: [{ id: "q", type: "input.text", x: 0, y: 0, config: {} }], edges: [] } },
      { id: "builtin:pipe-prompt-3d-uefn", name: "Prompt to 3D model in UEFN", icon: "🧊", category: "3D", kind: "builtin", ready: false, missing_plugins: ["meshy"], graph: { nodes: [], edges: [] } },
    ];
    (api as unknown as { list_workflow_templates: ReturnType<typeof vi.fn> }).list_workflow_templates = vi.fn().mockResolvedValue({ ok: true, templates });
    renderView();
    const hero = await screen.findByRole("region", { name: "Make your first workflow" });
    await within(hero).findByRole("button", { name: /Prompt to picture/ });
    expect(within(hero).queryByRole("button", { name: /Prompt to 3D model/ })).toBeNull();  // needs a plugin that isn't set up
    expect(screen.getAllByRole("button", { name: "New workflow" }).length).toBeGreaterThan(1);  // the hero and each empty section
    fireEvent.click(within(hero).getByRole("button", { name: /Prompt to picture/ }));
    await waitFor(() => expect(api.save_workflow).toHaveBeenCalledWith(expect.objectContaining({ name: "Prompt to picture", graph: templates[0].graph }), LOCAL.id));
  });

  it("keeps New workflow in view while nothing is open", async () => {
    renderView();
    await screen.findByText("Example");
    const foot = document.querySelector(".aw-list-foot")!;
    expect(within(foot as HTMLElement).getByRole("button", { name: "New workflow" })).toBeTruthy();
    fireEvent.click(screen.getByText("Example"));
    await screen.findByRole("button", { name: "Connect from Pause" });
    expect(document.querySelector(".aw-list-foot")).toBeNull();  // a workflow is open: the canvas has the room
  });
});

describe("custom code nodes", () => {
  const CODE = "// @ts-check\nexport const node = { kind: \"step\", inputs: [], outputs: [] };\nexport default async function run(input, ducky) {\n  return {};\n}\n";
  const canvas = () => document.querySelector('.aw-board')!;
  const catalog = [
    { type: "start.chat", label: "Chat", role: "starter", group: "Starting" },
    { type: "flow.wait", label: "Wait", group: "Logic", config_fields: [{ id: "seconds", label: "Seconds", type: "number" }] },
    { type: "code.js", label: "Custom code", role: "action", group: "Code", description: "Run your own JavaScript" },
  ];
  beforeEach(() => {
    api.list_workflow_nodes.mockResolvedValue({ nodes: catalog });
    api.check_workflow_node_code.mockResolvedValue({ ok: true, problems: [], pins: { exec: true, inputs: [], outputs: [] }, settings_spec: [], uses: { tools: [], builtins: [] }, code_sha: "s" });
    api.workflow_code_api.mockResolvedValue({ ok: true, dts: "declare module \"ducky\" { export interface Ducky {} }" });
    api.get_workflow_node_code.mockImplementation(async (_id: string, nodeId: string) => {
      const node = saved.graph.nodes.find((item) => item.id === nodeId);
      return node?.type === "code.js"
        ? { ok: true, kind: "custom", code: node.config.code, code_sha: "sha1", pins: node.config.pins, settings_spec: [], uses: { tools: [], builtins: [] }, problems: [], convertible: true, reason: "", approved: true }
        : { ok: true, kind: "builtin", code: "// generated", code_sha: "g", pins: { exec: true, inputs: [], outputs: [] }, settings_spec: [], uses: { tools: [], builtins: [] }, problems: [], convertible: true, reason: "", approved: true };
    });
  });
  function withCodeNode() {
    saved.graph.nodes.push({ id: "c", type: "code.js", label: "My code", x: 840, y: 0, config: { code: CODE, code_sha: "sha1", pins: { exec: true, inputs: [], outputs: [] }, settings: {}, inputs: {} } });
  }
  const codeTab = () => within(details()!).getByRole("tab", { name: /Code/ });

  it("adds a Custom code node from the palette's Code group, ready to run the blank template", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Add nodes" }));
    const menu = screen.getByRole("dialog", { name: "Add node" });
    await waitFor(() => expect([...menu.querySelectorAll(".aw-acc-name")].map((el) => el.textContent)).toContain("Code"));
    fireEvent.click(within(menu).getByRole("button", { name: /Custom code/ }));
    const added = document.querySelector('.aw-node .aw-code-badge')?.closest("[data-aw-node]");
    expect(added).toBeTruthy();
    expect(added!.querySelector(".aw-node-title strong")?.textContent).toBe("Custom code");
    await save();
    const node = saved.graph.nodes.find((item) => item.type === "code.js")!;
    expect(String(node.config.code)).toContain("export default async function run(input, ducky)");
    expect(node.config.pins).toEqual({ exec: true, inputs: [{ id: "text", type: "text", label: "Text" }], outputs: [{ id: "text", type: "text", label: "Text" }] });
  });

  it("keeps Custom code out of a team workflow's palette", async () => {
    await open("Daily check");
    fireEvent.click(screen.getByRole("button", { name: "Add nodes" }));
    const menu = screen.getByRole("dialog", { name: "Add node" });
    await waitFor(() => expect(menu.textContent).toContain("Wait"));
    expect(within(menu).queryByRole("button", { name: /Custom code/ })).toBeNull();
  });

  it("Ctrl+Z in the code editor undoes the text there, not the graph; typing is one undo step", async () => {
    withCodeNode();
    await open();
    editNode("c");
    fireEvent.click(codeTab());
    expect(codeTab().getAttribute("aria-selected")).toBe("true");
    expect(window.localStorage.getItem("ducky.workflows.detailsTab")).toBe("code");
    const box = within(details()!).getByRole("textbox", { name: "Code" }) as HTMLTextAreaElement;
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: `${CODE}// one\n` } });
    fireEvent.change(box, { target: { value: `${CODE}// one\n// two\n` } });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    fireEvent.keyDown(box, { key: "z", ctrlKey: true });
    fireEvent.blur(box);
    await save();
    expect(saved.graph.nodes.find((item) => item.id === "c")?.config.code).toBe(`${CODE}// one\n// two\n`);
    // On the canvas, one Ctrl+Z takes back the whole typing session.
    fireEvent.keyDown(canvas(), { key: "z", ctrlKey: true });
    await waitFor(() => expect(saved.graph.nodes.find((item) => item.id === "c")?.config.code).toBe(CODE));
  });

  it("code that becomes a value node loses its white wires, so nothing after it is skipped silently", async () => {
    withCodeNode();
    saved.graph.edges.push({ source: "a", target: "c", kind: "main" }, { source: "c", target: "b", kind: "main" });
    const VALUE = CODE.replace("kind: \"step\"", "kind: \"value\"");
    api.check_workflow_node_code.mockImplementation(async (code: string) => ({ ok: true, problems: [], settings_spec: [], uses: { tools: [], builtins: [] }, code_sha: "s",
      pins: { exec: !code.includes("\"value\""), inputs: [], outputs: [] } }));
    await open();
    editNode("c");
    fireEvent.click(codeTab());
    const box = within(details()!).getByRole("textbox", { name: "Code" }) as HTMLTextAreaElement;
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: VALUE } });
    fireEvent.blur(box);
    await waitFor(() => expect(document.querySelector('[data-aw-wire="a>c:main"]')).toBeNull());
    expect(document.querySelector('[data-aw-wire="c>b:main"]')).toBeNull();
    await save();
    expect(saved.graph.nodes.find((item) => item.id === "c")?.config.code).toBe(VALUE);
    expect(saved.graph.edges.filter((edge) => edge.source === "c" || edge.target === "c")).toEqual([]);
    expect(saved.graph.edges).toEqual([{ source: "s", target: "a", kind: "main" }]);
  });

  it("a failed code step in the run log opens its Code tab at the line", async () => {
    withCodeNode();
    saved.runs = [{ ok: false, error: "My code: Line 2: boom", steps: [{ id: "c", type: "code.js", label: "My code", ok: false, error: "Line 2: boom", code_error: { line: 2, col: 3, message: "boom" } }] }];
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Run log" }));
    fireEvent.click(screen.getByRole("button", { name: "My code — Line 2: boom" }));
    expect(details()?.querySelector(".aw-insp-name")?.textContent).toBe("My code");
    expect(codeTab().getAttribute("aria-selected")).toBe("true");
    expect(within(details()!).getByRole("button", { name: "Last run failed: Line 2: boom" })).toBeTruthy();
  });

  it("shows the generated code of a built-in on its Code tab", async () => {
    await open();
    editNode("a");
    fireEvent.click(codeTab());
    const box = await within(details()!).findByRole("textbox", { name: /Code of Wait/ }) as HTMLTextAreaElement;
    expect(box.value).toBe("// generated");
    // The node as it is on the canvas goes along, so unsaved settings show in its code.
    expect(api.get_workflow_node_code).toHaveBeenCalledWith("p", "a", expect.objectContaining({ id: "a", type: "flow.wait" }));
    // Settings stays the default view for everyone else.
    fireEvent.click(within(details()!).getByRole("tab", { name: "Settings" }));
    expect(screen.getByRole("spinbutton", { name: "Seconds" })).toBeTruthy();
  });
});

describe("phone layout", () => {
  it("folds the toolbar actions into ⋯ and the canvas tools into one button, each closing after a pick", async () => {
    await open();
    const actions = document.querySelector(".aw-toolbar-actions")!;
    const more = screen.getByRole("button", { name: "Workflow actions" });
    expect(actions.classList.contains("is-open")).toBe(false);
    fireEvent.click(more);
    expect(more.getAttribute("aria-expanded")).toBe("true");
    expect(actions.classList.contains("is-open")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "History" }));  // opens its own menu: the list stays
    expect(actions.classList.contains("is-open")).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(actions.classList.contains("is-open")).toBe(false);
    fireEvent.click(more);
    await save();  // a plain action runs and closes the list
    expect(actions.classList.contains("is-open")).toBe(false);

    const controls = screen.getByRole("toolbar", { name: "Canvas" });
    const fab = screen.getByRole("button", { name: "Canvas tools" });
    fireEvent.click(fab);
    expect(controls.classList.contains("is-open")).toBe(true);
    fireEvent.pointerDown(document.querySelector(".aw-board")!);  // a tap on the canvas closes it
    expect(controls.classList.contains("is-open")).toBe(false);
    fireEvent.click(fab);
    fireEvent.click(screen.getByRole("button", { name: "Select tool" }));
    expect(controls.classList.contains("is-open")).toBe(false);
    expect(screen.getByRole("button", { name: "Select tool" }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("unsaved changes and Save", () => {
  const saveButton = () => screen.getByRole("button", { name: "Save", exact: true });
  const nameBox = () => screen.getByRole("textbox", { name: "Workflow name" }) as HTMLInputElement;
  const canvas = () => document.querySelector(".aw-board") as HTMLElement;
  /** Ctrl+S (or Cmd+S) pressed on `target`: false when the page's own save was held back. */
  const ctrlS = (target: Element, mac = false) => fireEvent.keyDown(target, { key: "s", ...(mac ? { metaKey: true } : { ctrlKey: true }) });
  const moveNode = (id: string) => {
    fireEvent.pointerDown(document.querySelector(`[data-aw-node="${id}"] .aw-node-card`)!, { button: 0, pointerId: 3, clientX: 600, clientY: 200 });
    fireEvent.pointerMove(canvas(), { pointerId: 3, clientX: 680, clientY: 260 });
    fireEvent.pointerUp(canvas(), { pointerId: 3, clientX: 680, clientY: 260 });
  };
  const prompt = () => screen.findByRole("dialog");

  it("turns Save orange after an edit and back after saving, with a short Saved check", async () => {
    await open();
    expect(saveButton().classList.contains("is-dirty")).toBe(false);
    expect(saveButton().title).toBe("Save (Ctrl+S)");
    fireEvent.change(nameBox(), { target: { value: "Renamed" } });
    expect(saveButton().classList.contains("is-dirty")).toBe(true);
    expect(saveButton().title).toBe("Unsaved changes - Save (Ctrl+S)");
    expect(saveButton().querySelector(".aw-save-dot")).toBeTruthy();
    expect(api.save_workflow).not.toHaveBeenCalled();
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveButton().title).toBe("Saved"));
    expect(saved.name).toBe("Renamed");
    expect(saveButton().classList.contains("is-dirty")).toBe(false);
    expect(saveButton().querySelector(".aw-save-dot")).toBeNull();
  });

  it("shows why a save failed on the Save button", async () => {
    await open();
    moveNode("b");
    api.save_workflow.mockResolvedValueOnce({ error: "Too much code in one node." });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveButton().classList.contains("is-error")).toBe(true));
    expect(saveButton().title).toContain("Too much code in one node.");
    expect(screen.getByRole("alert").textContent).toContain("Too much code in one node.");
  });

  it("Ctrl+S on the canvas saves a moved node and keeps the WebView's own save dialog away", async () => {
    await open();
    moveNode("b");
    expect(saveButton().classList.contains("is-dirty")).toBe(true);
    expect(api.save_workflow).not.toHaveBeenCalled();
    expect(ctrlS(canvas())).toBe(false);
    await waitFor(() => expect(saved.graph.nodes.find((node) => node.id === "b")?.x).toBe(640));
    await waitFor(() => expect(saveButton().classList.contains("is-dirty")).toBe(false));
  });

  it("Ctrl+S or Cmd+S in a text field saves what was typed", async () => {
    await open();
    fireEvent.change(nameBox(), { target: { value: "Typed name" } });
    expect(ctrlS(nameBox(), true)).toBe(false);
    await waitFor(() => expect(saved.name).toBe("Typed name"));
    fireEvent.change(nameBox(), { target: { value: "Typed again" } });
    ctrlS(nameBox());
    await waitFor(() => expect(saved.name).toBe("Typed again"));
  });

  it("Ctrl+S in the code editor saves the keystrokes just typed", async () => {
    const CODE = "export const node = { kind: \"step\", inputs: [], outputs: [] };\nexport default async function run() {\n  return {};\n}\n";
    api.list_workflow_nodes.mockResolvedValue({ nodes: [{ type: "start.chat", label: "Chat", role: "starter", group: "Starting" }, { type: "code.js", label: "Custom code", role: "action", group: "Code" }] });
    api.check_workflow_node_code.mockResolvedValue({ ok: true, problems: [], pins: { exec: true, inputs: [], outputs: [] }, settings_spec: [], uses: { tools: [], builtins: [] }, code_sha: "s" });
    api.workflow_code_api.mockResolvedValue({ ok: true, dts: "" });
    api.get_workflow_node_code.mockResolvedValue({ ok: true, kind: "custom", code: CODE, code_sha: "sha1", pins: { exec: true, inputs: [], outputs: [] }, settings_spec: [], uses: { tools: [], builtins: [] }, problems: [], convertible: true, reason: "", approved: true });
    saved.graph.nodes.push({ id: "c", type: "code.js", label: "My code", x: 840, y: 0, config: { code: CODE, code_sha: "sha1", pins: { exec: true, inputs: [], outputs: [] }, settings: {}, inputs: {} } });
    await open();
    editNode("c");
    fireEvent.click(within(details()!).getByRole("tab", { name: /Code/ }));
    const box = within(details()!).getByRole("textbox", { name: "Code" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: `${CODE}// typed\n` } });
    expect(ctrlS(box)).toBe(false);
    await waitFor(() => expect(saved.graph.nodes.find((node) => node.id === "c")?.config.code).toBe(`${CODE}// typed\n`));
  });

  it("never saves a read-only workflow with Ctrl+S and says why", async () => {
    daily.owner = { ...TEAM, readOnly: true, reason: "Only Alpha Studio's editors can change it." };
    await open("Daily check");
    expect((saveButton() as HTMLButtonElement).disabled).toBe(true);
    expect(ctrlS(canvas())).toBe(false);
    expect(document.querySelector(".aw-toast")?.textContent).toContain("Only Alpha Studio's editors can change it.");
    expect(api.save_workflow).not.toHaveBeenCalled();
  });

  it("asks before switching workflows: Save saves, then switches", async () => {
    await open();
    fireEvent.change(nameBox(), { target: { value: "Renamed" } });
    fireEvent.click(screen.getByText("Daily check"));
    const dialog = await prompt();
    expect(dialog.textContent).toContain("Renamed");
    expect(dialog.textContent).toContain("unsaved changes");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await screen.findByDisplayValue("Daily check");
    expect(saved.name).toBe("Renamed");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("asks before switching workflows: Don't save switches without saving", async () => {
    await open();
    moveNode("b");
    fireEvent.click(screen.getByText("Daily check"));
    fireEvent.click(within(await prompt()).getByRole("button", { name: "Don't save" }));
    await screen.findByDisplayValue("Daily check");
    expect(api.save_workflow).not.toHaveBeenCalled();
    expect(saveButton().classList.contains("is-dirty")).toBe(false);
    fireEvent.click(screen.getByText("Example"));
    await screen.findByDisplayValue("Example");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect((document.querySelector('[data-aw-node="b"]') as HTMLElement).style.left).toBe("560px");
  });

  it("asks before switching workflows: Cancel stays with the edits", async () => {
    await open();
    fireEvent.change(nameBox(), { target: { value: "Renamed" } });
    fireEvent.click(screen.getByText("Daily check"));
    fireEvent.click(within(await prompt()).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(nameBox().value).toBe("Renamed");
    expect(saveButton().classList.contains("is-dirty")).toBe(true);
    expect(api.save_workflow).not.toHaveBeenCalled();
    expect(api.get_workflow).not.toHaveBeenCalledWith("d");
  });

  it("asks before starting a new workflow", async () => {
    await open();
    moveNode("b");
    fireEvent.click(screen.getAllByRole("button", { name: /New workflow/ })[0]);
    fireEvent.click(within(await prompt()).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByRole("button", { name: "Create workflow" })).toBeNull();
  });

  it("keeps unsaved edits when the editor closes and brings them back, one Undo from the saved copy", async () => {
    await open();
    fireEvent.change(nameBox(), { target: { value: "Not saved yet" } });
    cleanup();
    renderView();
    await waitFor(() => expect(nameBox().value).toBe("Not saved yet"));
    expect(saveButton().classList.contains("is-dirty")).toBe(true);
    expect(api.save_workflow).not.toHaveBeenCalled();
    fireEvent.keyDown(canvas(), { key: "z", ctrlKey: true });
    await waitFor(() => expect(nameBox().value).toBe("Example"));
  });
});
