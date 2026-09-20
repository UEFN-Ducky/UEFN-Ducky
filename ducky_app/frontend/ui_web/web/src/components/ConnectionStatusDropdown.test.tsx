// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectionStatusDropdown } from "./ConnectionStatusDropdown";
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
  afterEach(() => {
    cleanup();
    delete (window as unknown as { parent?: Window }).parent;
  });

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
    expect(screen.queryByText("Ducky listener")).toBeNull();
    expect(screen.queryByText(/Offline · ExampleProject1/)).toBeNull();
    const labels = [...dialog.querySelectorAll(".connection-status-menu-label")].map((el) => el.textContent);
    expect(labels).toEqual(["Ducky MCP", "UEFN listener", "UEFN MCP", "Blender MCP"]);
    expect(screen.queryByText(/restart UEFN/i)).toBeNull();
    const settings = dialog.querySelector(".connection-status-menu-settings");
    const head = dialog.querySelector(".connection-status-menu-head");
    expect(settings && head && settings.compareDocumentPosition(head) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText("Close web view")).toBeNull();
    expect(screen.queryByText("RECENT PROJECTS")).toBeNull();
  });

  it("renders extra (recent projects) above Connections on remote", () => {
    render(
      <ConnectionStatusDropdown
        status={status}
        projectName="ExampleProject1"
        onOpenSettings={() => {}}
        extra={<div className="project-selector-section-label">RECENT PROJECTS</div>}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /offline/i }));
    const dialog = screen.getByRole("dialog", { name: "Connection status" });
    const extra = dialog.querySelector(".project-selector-section-label");
    const head = dialog.querySelector(".connection-status-menu-head");
    expect(extra?.textContent).toBe("RECENT PROJECTS");
    expect(extra && head && extra.compareDocumentPosition(head) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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
});
