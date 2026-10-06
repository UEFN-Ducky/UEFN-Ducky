// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NodeSettings } from "./NodeSettings";
import { resetWorkflowToolsCache } from "./ToolSettings";
import type { AutomationGraphNodeDto, AutomationNodeDto } from "../types/panel";

// No bridge_job_start on this stub → runBridgeJob calls the method directly.
const api = vi.hoisted(() => ({ get_workflow_tools_catalog: vi.fn(), get_mcp_tools_catalog: vi.fn(), pick_workflow_folder: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
let current: AutomationGraphNodeDto;
function Editor({ config, meta }: { config: Record<string, unknown>; meta?: AutomationNodeDto }) {
  const [node, setNode] = useState<AutomationGraphNodeDto>({ id: "tool", type: meta?.type || "tool.call", x: 0, y: 0, config });
  current = node;
  return <NodeSettings node={node} meta={meta} onChange={setNode} />;
}
beforeEach(() => {
  resetWorkflowToolsCache();
  api.get_mcp_tools_catalog.mockRejectedValue(new Error("full catalog must not be used by workflow nodes"));
  api.get_workflow_tools_catalog.mockResolvedValue({ tools: [
    { name: "blender_scene_info", description: "Inspect the scene", category_label: "Blender", parameters: [] },
    { name: "blender_add_object", description: "Add an object", category_label: "Blender", parameters: [
      { name: "object_name", type: "string", required: true, description: "Name in the scene" },
      { name: "size", type: "number", default: 1 },
      { name: "visible", type: "boolean", default: true },
    ] },
    { name: "meshy_generate", description: "Make a mesh", category_label: "Meshy", parameters: [] },
  ] });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("node settings", () => {
  it("searches real tools, selects with radios and shows only relevant settings", async () => {
    render(<Editor config={{ name: "blender_scene_info", arguments_json: "{}" }} />);
    await screen.findByText("This tool needs no inputs.");
    expect(screen.queryByText("Arguments JSON")).toBeNull();
    expect(screen.queryByText("Label")).toBeNull();
    expect(screen.queryByText("Description")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Tool" }));
    expect(document.activeElement).toBe(screen.getByRole("searchbox", { name: "Search tools" }));
    fireEvent.change(document.activeElement!, { target: { value: "blender object" } });
    expect(screen.queryByRole("radio", { name: /meshy_generate/ })).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: /blender_add_object/ }));
    expect(current.config.name).toBe("blender_add_object");
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "object name" }), { target: { value: "Island" } });
    fireEvent.change(screen.getByRole("textbox", { name: "size" }), { target: { value: "3." } });
    expect((screen.getByRole("textbox", { name: "size" }) as HTMLInputElement).value).toBe("3.");  // half-typed stays as typed
    fireEvent.change(screen.getByRole("textbox", { name: "size" }), { target: { value: "3.5" } });
    fireEvent.click(screen.getByRole("button", { name: "visible" }));
    fireEvent.click(screen.getByRole("radio", { name: "No", exact: true }));
    expect(current.config.arguments).toEqual({ object_name: "Island", size: 3.5, visible: false });
    expect(current.config.arguments_json).toBeUndefined();
  });
  it("gives every input its own field, keeping values from earlier steps and inputs the tool doesn't list", async () => {
    const args = { extra: { keep: true }, size: "${payload.size}", visible: "${payload.visible}" };
    render(<Editor config={{ name: "blender_add_object", arguments_json: JSON.stringify(args) }} />);
    fireEvent.change(await screen.findByRole("textbox", { name: "object name" }), { target: { value: "Rock" } });
    expect(current.config.arguments).toEqual({ ...args, object_name: "Rock" });
    expect(screen.queryByText("Advanced inputs")).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Arguments JSON" })).toBeNull();
    expect((screen.getByRole("textbox", { name: "size" }) as HTMLInputElement).value).toBe("${payload.size}");
    expect((screen.getByRole("textbox", { name: "visible" }) as HTMLInputElement).value).toBe("${payload.visible}");
    expect(JSON.parse((screen.getByRole("textbox", { name: "extra" }) as HTMLTextAreaElement).value)).toEqual({ keep: true });
    fireEvent.change(screen.getByRole("textbox", { name: "extra" }), { target: { value: "{ \"keep\": " } });
    expect(screen.getByText("Not valid JSON yet.")).toBeTruthy();
    expect(current.config.arguments).toMatchObject({ extra: { keep: true } });  // unchanged until it is valid
    fireEvent.change(screen.getByRole("textbox", { name: "extra" }), { target: { value: "{ \"keep\": false }" } });
    expect(current.config.arguments).toMatchObject({ extra: { keep: false } });
    fireEvent.change(screen.getByRole("textbox", { name: "size" }), { target: { value: "{{score}}" } });
    expect(current.config.arguments).toMatchObject({ size: "{{score}}" });
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(current.config.arguments).not.toHaveProperty("extra");
  });
  it("preserves invalid JSON for repair before allowing individual input changes", async () => {
    render(<Editor config={{ name: "blender_add_object", arguments_json: "{broken" }} />);
    await screen.findByRole("status");
    expect(screen.queryByRole("textbox", { name: "object name" })).toBeNull();
    expect((screen.getByRole("textbox", { name: "Arguments JSON" }) as HTMLTextAreaElement).value).toBe("{broken");
    fireEvent.change(screen.getByRole("textbox", { name: "Arguments JSON" }), { target: { value: '{"object_name":"Repaired"}' } });
    await waitFor(() => expect((screen.getByRole("textbox", { name: "object name" }) as HTMLInputElement).value).toBe("Repaired"));
  });
  it("retains a saved tool while its catalog is unavailable and retries on open", async () => {
    api.get_workflow_tools_catalog.mockRejectedValueOnce(new Error("offline"));
    render(<Editor config={{ name: "custom_tool", arguments: { value: 42 } }} />);
    await waitFor(() => expect(api.get_workflow_tools_catalog).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Tool" }));
    await screen.findByRole("radio", { name: /blender_scene_info/ });
    expect((screen.getByRole("radio", { name: /custom_tool/ }) as HTMLInputElement).checked).toBe(true);
    expect(current.config).toEqual({ name: "custom_tool", arguments: { value: 42 } });
    expect(api.get_mcp_tools_catalog).not.toHaveBeenCalled();
  });
  it("loads the host tool list once and reuses it when the dropdown reopens or another node mounts", async () => {
    render(<Editor config={{ name: "blender_scene_info" }} />);
    await screen.findByText("This tool needs no inputs.");
    fireEvent.click(screen.getByRole("button", { name: "Tool" }));
    await screen.findByRole("radio", { name: /meshy_generate/ });
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Tool" }));
    await screen.findByRole("radio", { name: /meshy_generate/ });
    cleanup();
    render(<Editor config={{ name: "meshy_generate" }} />);
    await screen.findByText("This tool needs no inputs.");
    expect(api.get_workflow_tools_catalog).toHaveBeenCalledTimes(1);
  });
  it("uses a shared checkbox menu for multiple choices and retains saved unknown values", () => {
    render(<Editor config={{ targets: ["saved"] }} meta={{ type: "plugin.action", label: "Action", config_fields: [{ id: "targets", label: "Targets", type: "multiselect", options: [{ id: "one", label: "One" }, { id: "two", label: "Two" }] }] }} />);
    fireEvent.click(screen.getByRole("button", { name: "Targets" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "One" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Two" }));
    expect(current.config.targets).toEqual(["saved", "one", "two"]);
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("checkbox", { name: "Two" }), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Targets" }));
  });
});

describe("image and 3D nodes", () => {
  const meta: AutomationNodeDto = {
    type: "mesh.generate", label: "Text to 3D", group: "3D", exec: false, paid: true,
    config_fields: [{ id: "backend", label: "Backend", type: "backend" }, { id: "spend", label: "Spend credits", type: "boolean" }],
    backends: [
      { id: "meshy_text_to_3d", label: "Meshy", plugin: "Meshy", credits: 25, available: true },
      { id: "tripo", label: "Tripo v3", plugin: "3D AI Studio", credits: 60, available: false, reason: "Turn on the 3D AI Studio plugin in the Store and add its API key." },
    ],
  };

  it("picks a backend, says what it costs and only spends with the switch on", async () => {
    render(<Editor config={{}} meta={meta} />);
    const spend = screen.getByRole("switch", { name: "Spend credits" });
    expect(spend.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText(/About 25 credits each run on Meshy/)).toBeTruthy();
    expect(screen.getByText(/nothing is spent/)).toBeTruthy();
    fireEvent.click(spend);
    expect(current.config.spend).toBe(true);
    expect(screen.getByRole("switch", { name: "Spend credits" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Backend" }));
    await screen.findByRole("radio", { name: /Meshy/ });
    expect(screen.queryByRole("radio", { name: /Tripo v3/ })).toBeNull();  // its plugin isn't set up here: not offered
  });

  it("edits the direct image handler model and size without an agent picker", async () => {
    const imageMeta: AutomationNodeDto = { ...meta, type: "image.generate", backends: [{ id: "openai_image", label: "OpenAI image", plugin: "OpenAI", credits: 0, available: true, model: "image-a", config_fields: [
      { id: "model", label: "Image model", type: "select", options: [{ id: "image-a", label: "Image A" }, { id: "image-b", label: "Image B" }] },
      { id: "size", label: "Size", type: "text" },
    ] }] };
    render(<Editor config={{ backend: "openai_image" }} meta={imageMeta} />);
    expect(screen.queryByRole("button", { name: /^Model:/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Image model" }));
    fireEvent.click(await screen.findByRole("radio", { name: "Image B" }));
    expect(current.config.gateway_config).toEqual({ openai_image: { model: "image-b" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Size" }), { target: { value: "1024x1536" } });
    expect(current.config.gateway_config).toEqual({ openai_image: { model: "image-b", size: "1024x1536" } });
  });

  it("a plugin node's Backend field picks from Text to Image's list with the same gateway settings", async () => {
    const pluginMeta: AutomationNodeDto = {
      type: "cards.picture", label: "Card picture", group: "Cards", plugin_id: "cards",
      config_fields: [{ id: "picture_backend", label: "Picture backend", type: "backend", node_type: "image.generate" }, { id: "spend", label: "Spend credits", type: "boolean" }],
      backends: [
        { id: "google_imagen", label: "Imagen 4", plugin: "Google", credits: 0, cost: "Your own API key", available: false, reason: "Add your Google API key in Settings." },
        { id: "meshy_text_to_image", label: "Meshy · Nano Banana", plugin: "Meshy", credits: 5, available: true },
        { id: "ducky_image", label: "Ducky AI · Image", plugin: "Account", credits: 0, cost: "Ducky AI credit", available: true, config_fields: [
          { id: "size", label: "Size", type: "select", options: [{ id: "1024x1024", label: "Square" }, { id: "1024x1536", label: "Tall" }] },
        ] },
      ],
    };
    render(<Editor config={{}} meta={pluginMeta} />);
    expect(screen.getByText(/About 5 credits each run on Meshy · Nano Banana/)).toBeTruthy();  // first that can run
    fireEvent.click(screen.getByRole("button", { name: "Picture backend" }));
    expect(((await screen.findByRole("radio", { name: /Imagen 4/ })) as HTMLInputElement).disabled).toBe(true);  // listed, can't be picked
    fireEvent.click(screen.getByRole("radio", { name: /Ducky AI · Image/ }));
    expect(current.config.picture_backend).toBe("ducky_image");
    expect(current.config.backend).toBeUndefined();
    fireEvent.click(screen.getByRole("button", { name: "Size" }));
    fireEvent.click(await screen.findByRole("radio", { name: "Tall" }));
    expect(current.config.gateway_config).toEqual({ ducky_image: { size: "1024x1536" } });
  });

  it("a saved backend whose plugin is off says why", () => {
    render(<Editor config={{ backend: "tripo" }} meta={meta} />);
    expect(screen.getByRole("status").textContent).toContain("3D AI Studio plugin");
    expect(screen.getByText(/About 60 credits each run on Tripo v3/)).toBeTruthy();
  });

  it("with none picked, the first backend that can run is the one shown", () => {
    const offFirst = { ...meta, backends: [...(meta.backends || [])].reverse() };
    render(<Editor config={{}} meta={offFirst} />);
    expect(screen.getByText(/About 25 credits each run on Meshy/)).toBeTruthy();
  });

  it("chooses a folder for Save file", async () => {
    api.pick_workflow_folder.mockResolvedValue({ ok: true, folder: "C:/Users/me/Pictures/Ducky" });
    const saveMeta: AutomationNodeDto = { type: "util.save_file", label: "Save File", group: "Utility", exec: false,
      config_fields: [{ id: "folder", label: "Folder", type: "folder" }] };
    render(<Editor config={{}} meta={saveMeta} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose Folder" }));
    await waitFor(() => expect(current.config.folder).toBe("C:/Users/me/Pictures/Ducky"));
    fireEvent.change(screen.getByRole("textbox", { name: "Folder" }), { target: { value: "D:/Out" } });
    expect(current.config.folder).toBe("D:/Out");
  });
});
