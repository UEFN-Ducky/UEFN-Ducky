// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const chatToolView = vi.hoisted(() =>
  vi.fn(async (tools: string[], paths: string[]) => ({
    tools: Object.fromEntries(tools.map((t) => [t, t.startsWith("imk_")
      ? { plugin_id: "island-media-kit", label: "Island Media Kit", panel_id: "main", panel_title: "Media Kit" }
      : {}])),
    media: paths.filter((p) => /\.(mp4|png)$/.test(p)).map((p) => {
      const name = p.split(/[\\/]/).pop() ?? p;
      return { path: p, asked: p, name, kind: p.endsWith(".mp4") ? "video" : "image", url: `http://127.0.0.1:4199/workflow-media/s/t/${name}` };
    }),
  })),
);
const openTab = vi.hoisted(() => vi.fn());
const openRoute = vi.hoisted(() => vi.fn(() => ({ ok: true })));

vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => ({ chat_tool_view: chatToolView }) }));
vi.mock("../../plugin-ui/openPluginUiTab", () => ({ requestOpenPluginUiTab: openTab }));
vi.mock("../../navigation/openPanelRoute", () => ({ openPanelRoute: openRoute }));
vi.mock("../../hooks/usePluginContributions", () => ({
  usePluginContributions: () => ({ ui_panels: [{ plugin_id: "island-media-kit", id: "main", title: "Media Kit", entry: "ui/index.html" }] }),
}));

import type { ChatMessage } from "../../types/panel";
import type { ActivityItem } from "../../utils/chatMessageGroups";
import { resetToolViewCache } from "../../hooks/useToolView";
import { ToolViewStrip } from "./ToolInlineView";
import { toolViewSpec } from "./toolView";

afterEach(() => {
  cleanup();
  resetToolViewCache();
  vi.clearAllMocks();
});

function call(id: string, name: string, args: Record<string, unknown>, result: unknown, status = "success"): ActivityItem {
  const tool = { id, name, arguments: args, status, result: typeof result === "string" ? result : JSON.stringify(result) };
  const msg = (role: string) => ({ id, role, content: "", tool }) as unknown as ChatMessage;
  return { kind: "tool", id, intent: msg("tool"), result: status === "running" ? null : msg("success") };
}

describe("tool results in the chat", () => {
  it("finds the media a result names, a plugin's chat_view, and navigate's place", () => {
    const nested = JSON.stringify({ ok: true, data: { job: { output_path: "C:\\Videos\\Trailer 2.mp4" }, frames: ["C:/f/1.png", "not a path.png"] } });
    expect(toolViewSpec("mcp__uefn__imk_render_get", {}, nested)?.media).toEqual(["C:\\Videos\\Trailer 2.mp4", "C:/f/1.png"]);
    expect(toolViewSpec("imk_x", {}, JSON.stringify(nested))?.media).toHaveLength(2); // JSON inside a string
    const view = toolViewSpec("imk_x", {}, {
      chat_view: { title: "Trailer 2", media: ["C:\\a\\cover.png"], blocks: [{ type: "paragraph", text: "12 clips" }, { type: "script" }], open: { panel: "main", label: "Open in Media Kit" } },
      path: "C:\\a\\t.mp4",
    });
    expect(view).toMatchObject({ title: "Trailer 2", media: ["C:\\a\\cover.png", "C:\\a\\t.mp4"], openPanel: "main", openLabel: "Open in Media Kit" });
    expect(view?.blocks).toEqual([{ type: "paragraph", text: "12 clips" }]);
    expect(toolViewSpec("imk_project_list", {}, '{"ok":true,"projects":[]}')).toBeNull();
    expect(toolViewSpec("mcp__uefn__view_image", {}, '{"path":"C:/a/shot.png","media_url":"http://x/shot.png"}')).toBeNull(); // stays in its row
    expect(toolViewSpec("ducky_ui_navigate", { route: "workflows" }, '{"ok":true,"route":"workflows","in_chat":true}')?.navigate)
      .toEqual({ route: "workflows", itemId: "", label: "Workflows" });
    expect(toolViewSpec("ducky_ui_navigate", { route: "workflows" }, '{"ok":true,"route":"workflows"}')).toBeNull(); // an older app moved the tab itself
  });

  it("a folded group shows the plugin's Open button and plays what the calls made", async () => {
    const items = [
      call("1", "mcp__uefn__imk_timeline_get", {}, { ok: true }),
      call("2", "ducky_call_tool", { name: "imk_export_sequence", arguments: {} }, { ok: true, path: "C:\\Videos\\Trailer 2.mp4", cover: "C:\\Videos\\cover.png" }),
    ];
    render(<ToolViewStrip items={items} />);
    const open = await screen.findByRole("button", { name: /Open Island Media Kit/ });
    fireEvent.click(open);
    expect(openTab).toHaveBeenCalledWith("island-media-kit", "main", "Media Kit");
    await waitFor(() => expect(document.querySelector("video")?.getAttribute("src")).toContain("Trailer 2.mp4"));
    expect(chatToolView).toHaveBeenCalledTimes(1); // one call for the whole group
    fireEvent.click(screen.getByTitle(/cover\.png · open big/));
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("dialog").querySelector("img")?.getAttribute("src")).toContain("cover.png");
  });

  it("says the plugin is working while its call runs, and Open goes to navigate's place", async () => {
    render(<ToolViewStrip items={[
      call("1", "imk_render_create", {}, "", "running"),
      call("2", "ducky_ui_navigate", { route: "workflows" }, { ok: true, route: "workflows", item_id: "", in_chat: true }),
    ]} />);
    expect(await screen.findByText("Island Media Kit · working…")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Open Workflows/ }));
    expect(openRoute).toHaveBeenCalledWith("workflows", "");
  });
});
