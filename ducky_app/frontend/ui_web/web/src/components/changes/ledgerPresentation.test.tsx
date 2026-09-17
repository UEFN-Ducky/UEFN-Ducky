// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import { describe, expect, it } from "vitest";
import { JsonDiffView } from "./JsonDiffView";
import { failedActionCopyText, recordedResult } from "./ledgerPresentation";
afterEach(cleanup);

describe("ledger detail presentation", () => {
  it("shows compile facts without mistaking a result for a changed property", () => {
    render(<JsonDiffView before="{}" after={JSON.stringify({ params: {}, after: { result: { compile: { numErrors: 0, numWarnings: 2 }, diagnostics: { files: [{ path: "example.verse" }] } } } })} />);
    expect(screen.getByText("Compilation report")).toBeTruthy();
    expect(screen.getByText("Warnings")).toBeTruthy();
    expect(screen.queryByText(/Set result to/)).toBeNull();
    expect(screen.getByText("Technical details · full recorded data").closest("details")?.open).toBe(false);
  });
  it("keeps missing error counts unknown", () => {
    expect(recordedResult({ result: { compile: {} } })).toEqual({ title: "Compilation report", facts: [] });
  });
  it("extracts plan context from a recorded response", () => {
    expect(recordedResult({ node_id: "build", status: "in_progress", result: { ok: true, plan: { title: "Build a level" } } })?.facts).toContainEqual(["Recorded status", "In progress"]);
  });
  it("copies the failed ledger popup as the title, note, reason, and extra steps", () => {
    expect(
      failedActionCopyText({
        title: "Action failed — Verse-Base Core-tycoon base device",
        outcome: "failed",
        reason: "Field 'WallButtons' is in Verse source but has no compiled hash on this device yet.",
        steps: [
          { tool: "wire_verse_device_array", summary: "WallButtons" },
          { tool: "wire_verse_device_array", summary: "retry" },
        ],
      }),
    ).toBe(
      [
        "Action failed — Verse-Base Core-tycoon base device",
        "This attempt was recorded as failed. Review the recorded reason below.",
        "Field 'WallButtons' is in Verse source but has no compiled hash on this device yet.",
        "wire_verse_device_array — WallButtons\nwire_verse_device_array — retry",
      ].join("\n\n"),
    );
  });
  it("retains large unfamiliar values behind a disclosure", () => {
    render(<JsonDiffView before="{}" after={JSON.stringify({ custom: "x".repeat(400) })} />);
    expect(screen.getByText("View recorded value").closest("details")?.open).toBe(false);
    expect(screen.getByText("x".repeat(400))).toBeTruthy();
  });
});
