// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectionStatusDropdown, epicMcpRows } from "./ConnectionStatusDropdown";
import type { ListenerStatus } from "../types/panel";

const status: ListenerStatus = {
  online: false,
  version: "test",
  epic_mcp_online: false,
  epic_mcp_reason: "unreachable",
  plugin_connections: [
    { id: "blender", program: "blender", label: "Blender MCP", online: true, detail: "Connected · localhost:9876" },
  ],
};

describe("ConnectionStatusDropdown", () => {
  it("lists plugin MCP rows and puts Settings above Connections", () => {
    render(
      <ConnectionStatusDropdown
        status={status}
        projectName="ExampleProject1"
        onOpenSettings={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /offline/i }));
    const dialog = screen.getByRole("dialog", { name: "Connection status" });
    expect(dialog.querySelector(".connection-status-menu-settings")).toBeTruthy();
    expect(screen.getByText("Settings")).toBeTruthy();
    expect(screen.getByText("Connections")).toBeTruthy();
    expect(screen.getByText("Blender MCP")).toBeTruthy();
    expect(screen.getByText("Connected · localhost:9876")).toBeTruthy();
    expect(screen.getByText("Ducky MCP")).toBeTruthy();
    expect(screen.getByText("Connected · ExampleProject1")).toBeTruthy();
    expect(screen.getByText("UEFN listener")).toBeTruthy();
    expect(screen.getByText("UEFN MCP")).toBeTruthy();
    expect(screen.getByText("Unreal MCP")).toBeTruthy();
    expect(screen.queryByText("Ducky listener")).toBeNull();
    expect(screen.queryByText(/Offline · ExampleProject1/)).toBeNull();
    const labels = [...dialog.querySelectorAll(".connection-status-menu-label")].map((el) => el.textContent);
    expect(labels).toEqual(["Ducky MCP", "UEFN listener", "UEFN MCP", "Unreal MCP", "Blender MCP"]);
    expect(screen.queryByText(/restart UEFN/i)).toBeNull();
    const settings = dialog.querySelector(".connection-status-menu-settings");
    const head = dialog.querySelector(".connection-status-menu-head");
    expect(settings && head && settings.compareDocumentPosition(head) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText("Close web view")).toBeNull();
    expect(screen.queryByText("RECENT PROJECTS")).toBeNull();
  });

  it("on a phone shows the project list as a Projects tab beside Connections", () => {
    render(
      <ConnectionStatusDropdown
        status={status}
        onOpenSettings={() => {}}
        extra={<div>RECENT PROJECTS</div>}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /offline/i }));
    const tabs = screen.getAllByRole("tab").map((el) => el.textContent);
    expect(tabs).toEqual(["Projects", "Connections"]);
    expect(screen.getByText("RECENT PROJECTS")).toBeTruthy();
    expect(screen.queryByText("Ducky MCP")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Connections" }));
    expect(screen.queryByText("RECENT PROJECTS")).toBeNull();
    expect(screen.getByText("Ducky MCP")).toBeTruthy();
  });

  it("on a phone opens on a Tabs tab, with the header's tools as icons above Settings", () => {
    const onSearch = vi.fn();
    render(
      <ConnectionStatusDropdown
        status={status}
        onOpenSettings={() => {}}
        extra={<div>RECENT PROJECTS</div>}
        tabsTab
        tools={(close) => (
          <button type="button" aria-label="Search" onClick={() => { close(); onSearch(); }} />
        )}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /offline/i }));
    expect(screen.getAllByRole("tab").map((el) => el.textContent)).toEqual(["Tabs", "Projects", "Connections"]);
    const dialog = screen.getByRole("dialog", { name: "Connection status" });
    const tools = dialog.querySelector(".connection-status-tools");
    const settings = dialog.querySelector(".connection-status-menu-settings");
    expect(tools && settings && tools.compareDocumentPosition(settings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The Tabs tab is the spot the focused editor group portals its tab list into.
    expect(dialog.querySelector(".connection-status-open-tabs")).toBeTruthy();
    expect(screen.queryByText("RECENT PROJECTS")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(onSearch).toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "Connection status" })).toBeNull();
  });

  it("while watching a PC window, a pick ends the stream but switching tabs does not", () => {
    const onMenuAction = vi.fn();
    render(
      <ConnectionStatusDropdown
        status={status}
        onOpenSettings={() => {}}
        tabsTab
        onMenuAction={onMenuAction}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /offline/i }));
    fireEvent.click(screen.getByRole("tab", { name: "Connections" }));
    expect(onMenuAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Settings"));
    expect(onMenuAction).toHaveBeenCalledTimes(1);
  });

  it("shows UEFN MCP and Unreal MCP by the editor holding Epic's port", () => {
    const url = "http://127.0.0.1:8000/mcp";
    const rows = (extra: Partial<ListenerStatus>) =>
      epicMcpRows({ ...status, epic_mcp_url: url, ...extra }).map((r) => [r.label, r.detail, r.ok, Boolean(r.warn)]);

    expect(rows({ epic_mcp_online: true, epic_mcp_reason: "", epic_mcp_editor: "uefn" })).toEqual([
      ["UEFN MCP", `Connected · ${url}`, true, false],
      ["Unreal MCP", "Offline · port held by UEFN", false, false],
    ]);
    expect(rows({ epic_mcp_online: true, epic_mcp_reason: "", epic_mcp_editor: "unreal" })).toEqual([
      ["UEFN MCP", "Offline · port held by Unreal Editor", false, false],
      ["Unreal MCP", `Connected · ${url}`, true, false],
    ]);
    // Unreal Editor on UEFN's port: connected, with the note that Ducky moved it.
    expect(rows({ epic_mcp_online: true, epic_mcp_editor: "unreal", epic_mcp_note: "restart once" })[1]).toEqual(
      ["Unreal MCP", "Connected · restart once", true, true],
    );
    // An island is open but Unreal Editor holds the port: Ducky sends it nothing.
    expect(rows({ epic_mcp_reason: "other_editor", epic_mcp_editor: "unreal" })).toEqual([
      ["UEFN MCP", "Offline · port held by Unreal Editor", false, true],
      ["Unreal MCP", "Running · not used for this project", false, true],
    ]);
    expect(rows({ epic_mcp_reason: "unreachable" })).toEqual([
      ["UEFN MCP", "Offline · unreachable", false, false],
      ["Unreal MCP", "Offline · open Unreal Editor 5.8+ with Unreal MCP", false, false],
    ]);
    expect(rows({ epic_mcp_reason: "port_taken" }).map((r) => r[1])).toEqual([
      "Port taken by another program",
      "Port taken by another program",
    ]);
  });

  it("posts ud-remote-close from Close web view only when framed", () => {
    const postMessage = vi.fn();
    Object.defineProperty(window, "parent", {
      configurable: true,
      value: { postMessage },
    });
    render(
      <ConnectionStatusDropdown
        status={status}
        projectName="ExampleProject1"
        onOpenSettings={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /offline/i }));
    fireEvent.click(screen.getByRole("button", { name: "Close web view" }));
    expect(postMessage).toHaveBeenCalledWith({ type: "ud-remote-close" }, "*");
  });

  it("shows an Online tab and a green count when a plugin lists people online", () => {
    const withPeople: ListenerStatus = {
      ...status,
      plugin_people: [{ id: "team", label: "Team", people: [
        { id: "u1", name: "Ana", detail: "In BrainRot TCG", online: true },
        { id: "u2", name: "Ben", detail: "", online: false },
      ] }],
    };
    render(<ConnectionStatusDropdown status={withPeople} onOpenSettings={() => {}} />);
    expect(screen.getByLabelText("1 online").textContent).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: /offline/i }));
    expect(screen.getByText("Blender MCP")).toBeTruthy(); // Connections is the first tab
    fireEvent.click(screen.getByRole("tab", { name: /online/i }));
    expect(screen.getByText("Team")).toBeTruthy();
    expect(screen.getByText("Ana")).toBeTruthy();
    expect(screen.getByText("In BrainRot TCG")).toBeTruthy();
    expect(screen.getByText("Ben")).toBeTruthy();
    expect(screen.queryByText("Blender MCP")).toBeNull();
  });

  afterEach(() => {
    cleanup();
    delete (window as unknown as { parent?: Window }).parent;
  });
});
