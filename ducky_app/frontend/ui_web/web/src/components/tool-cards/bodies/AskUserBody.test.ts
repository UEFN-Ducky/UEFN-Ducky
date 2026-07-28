import { describe, expect, it } from "vitest";
import { rowsFromAskUser } from "./AskUserBody";

describe("AskUserBody rows", () => {
  it("summarizes selected and skipped answers", () => {
    const rows = rowsFromAskUser(
      {
        questions: [
          { id: "owner", prompt: "Who owns tables?" },
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
    expect(rows[0].summary).toBe("plugin");
    expect(rows[1].summary).toBe("Skipped");
  });
});
