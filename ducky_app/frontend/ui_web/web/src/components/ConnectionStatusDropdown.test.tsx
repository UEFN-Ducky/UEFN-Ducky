// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ConnectionStatusDropdown } from "./ConnectionStatusDropdown";
import type { ListenerStatus } from "../types/panel";

afterEach(cleanup);

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
    expect(screen.queryByText(/restart UEFN once/i)).toBeNull();
    const settings = dialog.querySelector(".connection-status-menu-settings");
    const head = dialog.querySelector(".connection-status-menu-head");
    expect(settings && head && settings.compareDocumentPosition(head) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
