// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AutomationTemplatePicker } from "./AutomationTemplatePicker";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import type { AutomationTemplateDto } from "../types/panel";

const api = vi.hoisted(() => ({ list_workflow_templates: vi.fn(), save_workflow_template: vi.fn(), delete_workflow_template: vi.fn(), export_workflow_folder: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
const deepLink = vi.hoisted(() => vi.fn());
vi.mock("../navigation/deepLinks", () => ({ handleDeepLink: deepLink }));

const graph = { nodes: [{ id: "q", type: "input.text", x: 0, y: 0, config: {} }], edges: [] };
const templates: AutomationTemplateDto[] = [
  { id: "builtin:pipe-prompt-image", name: "Prompt to picture", icon: "🖼️", kind: "builtin", category: "Images", description: "Type a prompt, get a picture.",
    requires_plugins: ["studio3d"], missing_plugins: [], ready: true, graph },
  { id: "builtin:pipe-character-full", name: "Prompt → picture → character → rig → animations → UEFN", icon: "🦆", kind: "builtin", category: "Characters",
    description: "The whole character pipeline.", requires_plugins: ["studio3d", "meshy", "uefn"], missing_plugins: ["meshy"], ready: false, graph },
  { id: "builtin:stop-game", name: "Stop the game session", kind: "builtin", category: "Play tests", requires_plugins: [], missing_plugins: [], ready: true, graph },
  { id: "custom:abc12345", name: "My pipeline", kind: "custom", category: "Yours", graph },
];

beforeEach(() => {
  api.list_workflow_templates.mockResolvedValue({ ok: true, templates });
  api.save_workflow_template.mockResolvedValue({ ok: true, template: { ...templates[3], id: "custom:new12345" } });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function show(props: Partial<React.ComponentProps<typeof AutomationTemplatePicker>> = {}) {
  const onSelect = vi.fn();
  render(<ConfirmModalProvider><AutomationTemplatePicker open onClose={() => undefined} onSelect={onSelect} currentGraph={graph} {...props} /></ConfirmModalProvider>);
  return onSelect;
}

describe("new workflow picker", () => {
  it("puts templates on shelves by category, with what each one is", async () => {
    show();
    const list = await screen.findByRole("listbox", { name: "Workflow templates" });
    await within(list).findByText("Prompt to picture");
    expect([...list.querySelectorAll(".vtm-section-title")].map((el) => el.firstChild?.textContent)).toEqual(["Images", "Characters", "Play tests", "Yours"]);
    expect(within(list).getByText("Type a prompt, get a picture.")).toBeTruthy();
    // Built-in templates say so (they used to say "Yours"); one that needs a plugin says which.
    expect([...list.querySelectorAll(".vtm-badge")].map((el) => el.textContent)).toEqual(["Empty canvas", "Uses studio3d", "Needs meshy", "Built in", "Yours"]);
    fireEvent.click(screen.getByRole("tab", { name: /Characters/ }));
    expect(within(list).queryByText("Prompt to picture")).toBeNull();
    expect(within(list).getByText("Prompt → picture → character → rig → animations → UEFN")).toBeTruthy();
    expect(list.querySelector(".vtm-section-title")).toBeNull();  // one shelf: no headings
  });

  it("searches every word across names, descriptions and categories", async () => {
    show();
    await screen.findByText("Prompt to picture");
    fireEvent.change(screen.getByRole("textbox", { name: "Search templates" }), { target: { value: "character pipeline" } });
    expect(screen.queryByText("Prompt to picture")).toBeNull();
    expect(screen.getByText("Prompt → picture → character → rig → animations → UEFN")).toBeTruthy();
  });

  it("sends a template that needs a plugin to the Store instead of making it", async () => {
    const onSelect = show();
    fireEvent.click(await screen.findByText("Prompt → picture → character → rig → animations → UEFN"));
    expect(screen.getByText(/Needs meshy: Get it opens the Store/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Get meshy" }));
    expect(deepLink).toHaveBeenCalledWith("uefn-ducky://store/meshy");
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Prompt to picture"));
    fireEvent.click(screen.getByRole("button", { name: "Create workflow" }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(templates[0]));
  });

  it("saves the open workflow as a template with a category and an icon", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: /Save as template/ }));
    expect(screen.getByText("The open workflow: 1 node.")).toBeTruthy();
    expect(document.querySelector(".vtm-modal--compact")).toBeTruthy();  // as tall as its fields
    fireEvent.click(screen.getByRole("button", { name: "Save template" }));
    expect(screen.getByRole("alert").textContent).toBe("It needs a name.");
    fireEvent.change(screen.getByRole("textbox", { name: "Template name" }), { target: { value: "Duck props" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Template description" }), { target: { value: "Props from a prompt" } });
    fireEvent.click(screen.getByRole("button", { name: "Category" }));
    fireEvent.click(await screen.findByRole("radio", { name: "3D" }));
    fireEvent.click(screen.getByRole("button", { name: "Save template" }));
    await waitFor(() => expect(api.save_workflow_template).toHaveBeenCalledWith("Duck props", "Props from a prompt", "⚡", JSON.stringify(graph), "", "3D"));
  });
});

describe("folder templates", () => {
  const bundle = {
    version: 1, root: "BrainRot TCG", folders: ["Functions"],
    workflows: [
      { key: "all", name: "Card art - all cards", folder: "", graph },
      { key: "one", name: "Card art - one card", folder: "Functions", graph },
    ],
  };
  const kit: AutomationTemplateDto = { id: "plugin:brainrot-tcg:card-art", name: "Card art", icon: "🃏", kind: "plugin", plugin_id: "brainrot-tcg",
    category: "Images", shape: "bundle", workflow_count: 2, folder_count: 1, root: "BrainRot TCG", bundle, ready: true, graph: { nodes: [], edges: [] } };
  const mine: AutomationTemplateDto = { ...kit, id: "custom:kit12345", name: "My kit", kind: "custom", category: "Yours" };

  beforeEach(() => {
    api.list_workflow_templates.mockResolvedValue({ ok: true, templates: [...templates, kit, mine] });
  });

  it("shows how many workflows, the tree it makes, and asks where the folder goes", async () => {
    const onFolderChange = vi.fn();
    const onSelect = show({ owners: [{ id: "local", kind: "local", label: "Local" }], ownerId: "local", folders: { local: ["Games", "Games/Cards"] },
      folder: "", onFolderChange });
    const card = (await screen.findByText("Card art")).closest("button") as HTMLElement;
    expect(within(card).getByText("2 workflows")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "What it makes" })).toBeNull();
    fireEvent.click(card);
    const tree = screen.getByRole("list", { name: "What it makes" });
    expect([...tree.querySelectorAll("li")].map((li) => [li.textContent, li.className.includes("--folder") ? "folder" : "workflow"])).toEqual([
      ["BrainRot TCG", "folder"], ["Card art - all cards", "workflow"], ["Functions", "folder"], ["Card art - one card", "workflow"]]);
    fireEvent.click(screen.getByRole("button", { name: "Put the folder in" }));
    fireEvent.click(await screen.findByRole("radio", { name: "Games/Cards" }));
    expect(onFolderChange).toHaveBeenCalledWith("Games/Cards");
    fireEvent.click(screen.getByRole("button", { name: "Create 2 workflows" }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(kit));
  });

  it("keeps a folder from the Workflows list as a template, then closes", async () => {
    const exported = { ...bundle, root: "Kit" };
    api.export_workflow_folder.mockResolvedValue({ ok: true, bundle: exported });
    api.save_workflow_template.mockResolvedValue({ ok: true, template: { ...mine, id: "custom:new12345" } });
    const onClose = vi.fn();
    show({ onClose, saveFolder: { ownerId: "teamA", path: "Games/Kit" } });
    expect(await screen.findByText("Save folder as template")).toBeTruthy();
    expect(api.export_workflow_folder).toHaveBeenCalledWith("teamA", "Games/Kit");
    expect((screen.getByRole("textbox", { name: "Template name" }) as HTMLInputElement).value).toBe("Kit");
    await screen.findByText(/The folder “Kit”: 2 workflows, 1 folder inside/);
    expect(within(screen.getByRole("list", { name: "What it makes" })).getByText("Card art - one card")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save template" }));
    await waitFor(() => expect(api.save_workflow_template).toHaveBeenCalledWith("Kit", "", "📁", "", "", "Yours", "teamA", "Games/Kit"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("editing a folder template keeps its tree", async () => {
    show();
    const card = (await screen.findByText("My kit")).closest("button") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "Edit My kit" }));
    expect(screen.getByText("The folder stays as saved.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(api.save_workflow_template).toHaveBeenCalledWith("My kit", "", "🃏", "", "custom:kit12345", "Yours"));
  });
});
