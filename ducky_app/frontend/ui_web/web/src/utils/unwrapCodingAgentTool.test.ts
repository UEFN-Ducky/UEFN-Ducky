import { describe, expect, it } from "vitest";
import { unwrapCodingAgentTool } from "./unwrapCodingAgentTool";

describe("unwrapCodingAgentTool", () => {
  it("passes through normal tools", () => {
    expect(unwrapCodingAgentTool("Read", { path: "a.verse" })).toEqual({
      name: "Read",
      arguments: { path: "a.verse" },
    });
  });

  it("unwraps CallMcpTool to the real MCP tool", () => {
    expect(
      unwrapCodingAgentTool("CallMcpTool", {
        providerIdentifier: "uefn",
        toolName: "workspace_list_verse_errors",
        args: {},
      }),
    ).toEqual({
      name: "workspace_list_verse_errors",
      arguments: {},
    });
  });

  it("unwraps walkthrough so Replay can match", () => {
    expect(
      unwrapCodingAgentTool("CallMcpTool", {
        toolName: "ducky_walkthrough_run",
        args: { tour_id: "app_shell" },
      }),
    ).toEqual({
      name: "ducky_walkthrough_run",
      arguments: { tour_id: "app_shell" },
    });
  });

  it("parses string args JSON", () => {
    expect(
      unwrapCodingAgentTool("call_mcp_tool", {
        tool_name: "workspace_read_file",
        arguments: '{"path":"x.verse"}',
      }),
    ).toEqual({
      name: "workspace_read_file",
      arguments: { path: "x.verse" },
    });
  });

  it("unwraps ducky_call_tool to the flat MCP tool", () => {
    expect(
      unwrapCodingAgentTool("ducky_call_tool", {
        name: "blender_status",
        arguments: { pretty: false },
        description: "check blender",
      }),
    ).toEqual({
      name: "blender_status",
      arguments: { pretty: false },
    });
  });

  it("unwraps Cursor SDK mcp discriminator", () => {
    expect(
      unwrapCodingAgentTool("mcp", {
        providerIdentifier: "uefn",
        toolName: "workspace_read_file",
        args: { path: "a.verse" },
      }),
    ).toEqual({
      name: "workspace_read_file",
      arguments: { path: "a.verse" },
    });
  });

  it("unwraps generic tool name when args carry toolName", () => {
    expect(
      unwrapCodingAgentTool("tool", {
        toolName: "find_devices",
        args: { label_filter: "Player" },
      }),
    ).toEqual({
      name: "find_devices",
      arguments: { label_filter: "Player" },
    });
  });

  it("keeps generic tool when there is no inner MCP name", () => {
    expect(unwrapCodingAgentTool("tool", { path: "C:/proj/Content/Verse" })).toEqual({
      name: "tool",
      arguments: { path: "C:/proj/Content/Verse" },
    });
  });
});
