import { describe, expect, it } from "vitest";
import { formatToolDuration, humanToolLabel } from "./agentActivity";

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
