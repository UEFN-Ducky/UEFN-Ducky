import { describe, expect, it } from "vitest";
import { activityPanelTitle, buildActivityLines, formatToolDuration, humanToolLabel, shellActivityText } from "./agentActivity";
import type { ChatMessage } from "../types/panel";

it("uses completion action details in the activity feed", () => {
  const intent = { id: 1, role: "tool", tool: { name: "web_search", arguments: {}, status: "pending" } } as ChatMessage;
  const done = { id: 2, role: "success", tool: { name: "web_search", arguments: { action: { type: "find_in_page" } }, status: "success" } } as ChatMessage;
  expect(buildActivityLines([intent, done], "")[0].text).toBe("Find in page");
  expect(buildActivityLines([intent], "")[0].text).toBe("Web lookup");
});

describe("humanToolLabel / formatToolDuration", () => {
  it("uses friendly names from the tool-card prototype", () => {
    expect(humanToolLabel("get_all_actors")).toBe("Query Actors");
    expect(humanToolLabel("mcp__uefn__execute_python")).toBe("Editor Python Script");
    expect(humanToolLabel("Skill")).toBe("Agent Knowledge Retrieval");
    expect(humanToolLabel("take_high_res_screenshot")).toBe("Capture Screenshot");
    expect(humanToolLabel("workspace_list_dir")).toBe("List Directories");
    expect(humanToolLabel("ls")).toBe("List Directories");
    expect(humanToolLabel("grep")).toBe("Search File Content");
    expect(humanToolLabel("read")).toBe("Read file");
  });

  it("formats durations like the prototype", () => {
    expect(formatToolDuration(11)).toBe("11ms");
    expect(formatToolDuration(4250)).toBe("4.3s");
  });
});

describe("shell commands in the running status", () => {
  const ps = String.raw`"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -Command ".\.venv\Scripts\python.exe -m pytest tests/test_x.py -q"`;

  it("says what a command does, never the command", () => {
    expect(shellActivityText(ps)).toBe("Running tests");
    expect(shellActivityText("git status --short")).toBe("Checking git");
    expect(shellActivityText("git commit -m wip")).toBe("Committing");
    expect(shellActivityText(`bash -lc 'rg -n "foo" src'`)).toBe("Searching files");
    expect(shellActivityText("Get-Content src/app.ts | Select-Object -First 40")).toBe("Reading files");
    expect(shellActivityText("Get-ChildItem -Recurse src")).toBe("Listing files");
    expect(shellActivityText("npm ci")).toBe("Installing packages");
    expect(shellActivityText("npx tsc --noEmit")).toBe("Building");
    expect(shellActivityText("python tools/gen.py")).toBe("Running a script");
    expect(shellActivityText("whoami")).toBe("Running a command");
  });

  it("prefers the agent's own description", () => {
    expect(shellActivityText("pytest -q", "Run the store tests")).toBe("Run the store tests");
  });

  it("keeps the raw command out of the live title", () => {
    const tool = {
      id: 1,
      role: "tool",
      text: "",
      tool: { name: "PowerShell", arguments: { command: ps }, status: "pending" },
    } as unknown as ChatMessage;
    const title = activityPanelTitle(buildActivityLines([tool], ""), false, 0);
    expect(title).toBe("Running tests");
    expect(title).not.toContain("powershell.exe");
  });

  it("names the new file tools", () => {
    expect(humanToolLabel("mcp__uefn__workspace_search")).toBe("Search File Content");
    expect(humanToolLabel("workspace_replace_lines")).toBe("Edit file");
    expect(humanToolLabel("workspace_file_outline")).toBe("File outline");
  });
});
