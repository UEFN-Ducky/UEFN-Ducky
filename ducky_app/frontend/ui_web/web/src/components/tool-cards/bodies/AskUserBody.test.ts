import { describe, expect, it } from "vitest";
import { rowsFromAskUser } from "./AskUserBody";

describe("AskUserBody rows", () => {
  it("summarizes selected labels and skipped answers", () => {
    const rows = rowsFromAskUser(
      {
        questions: [
          {
            id: "owner",
            prompt: "Who owns tables?",
            options: [
              { id: "plugin", label: "Plugin owns tables" },
              { id: "core", label: "Core owns tables" },
            ],
          },
          { id: "notes", prompt: "Notes?" },
        ],
      },
      JSON.stringify({
        ok: true,
        answers: {
          owner: { selected: ["plugin"], text: "", skipped: false },
          notes: { selected: [], text: "", skipped: true },
        },
      }),
    );
    expect(rows).toHaveLength(2);
    expect(rows[0].summary).toBe("Plugin owns tables");
    expect(rows[0].status).toBe("answered");
    expect(rows[1].summary).toBe("Skipped");
    expect(rows[1].status).toBe("skipped");
  });

  it("shows waiting rows while the agent is paused", () => {
    const rows = rowsFromAskUser(
      {
        questions: [
          { id: "a", prompt: "Path A?" },
          { id: "b", prompt: "Path B?" },
        ],
      },
      "",
      { pending: true },
    );
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.status === "waiting")).toBe(true);
    expect(rows[0].summary).toBe("Waiting…");
  });
});
