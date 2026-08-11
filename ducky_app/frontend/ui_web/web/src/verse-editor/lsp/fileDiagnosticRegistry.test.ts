import { beforeEach, describe, expect, it } from "vitest";
import { fileDiagnosticRegistry } from "./fileDiagnosticRegistry";

const ROOT = "C:/proj";
const URI = "file:///C:/proj/Content/Verse/Tycoon1/generator_manager.verse";
const PATH = "content/verse/tycoon1/generator_manager.verse";

const FAKE_ERROR = {
  path: PATH,
  errors: 1,
  warnings: 0,
  items: [
    {
      line: 73,
      column: 21,
      message: 'vErr:S88: Expected expression, got "{"',
      severity: "error",
    },
  ],
};

beforeEach(() => {
  fileDiagnosticRegistry.clear();
});

describe("fileDiagnosticRegistry live vs cache", () => {
  it("authoritative setScanResults clean clears prior live errors", () => {
    fileDiagnosticRegistry.updateFromLspUri(ROOT, URI, [
      {
        message: "real error",
        severity: 1,
        range: {
          start: { line: 5, character: 0 },
          end: { line: 5, character: 8 },
        },
      },
    ]);
    expect(fileDiagnosticRegistry.getTotals().errors).toBe(1);

    // Snapshot says clean → drop sticky live false-positives (Problems refresh / rescan).
    fileDiagnosticRegistry.setScanResults([
      { path: PATH, errors: 0, warnings: 0, items: [] },
      { path: "content/verse/helpers.verse", errors: 0, warnings: 0, items: [] },
    ]);
    expect(fileDiagnosticRegistry.getTotals().errors).toBe(0);
  });

  it("scan must NOT resurrect errors after an authoritative live clear", () => {
    fileDiagnosticRegistry.updateFromLspUri(ROOT, URI, []);
    fileDiagnosticRegistry.mergeScanResults([FAKE_ERROR]);
    expect(fileDiagnosticRegistry.getTotals().errors).toBe(0);
    expect(fileDiagnosticRegistry.getSummary(PATH)).toBeUndefined();
  });

  it("setScanResults must NOT resurrect errors after a live clear", () => {
    fileDiagnosticRegistry.updateFromLspUri(ROOT, URI, []);
    fileDiagnosticRegistry.setScanResults([FAKE_ERROR]);
    expect(fileDiagnosticRegistry.getTotals().errors).toBe(0);
  });

  it("scan can still ADD errors when there was no live update for that file", () => {
    fileDiagnosticRegistry.mergeScanResults([FAKE_ERROR]);
    expect(fileDiagnosticRegistry.getTotals().errors).toBe(1);
  });

  it("mergeLiveResults clean payload clears sticky Problems in other windows", () => {
    fileDiagnosticRegistry.hydrate([FAKE_ERROR]);
    expect(fileDiagnosticRegistry.getTotals().errors).toBe(1);

    fileDiagnosticRegistry.mergeLiveResults([
      { path: PATH, errors: 0, warnings: 0, items: [] },
    ]);
    expect(fileDiagnosticRegistry.getTotals().errors).toBe(0);
  });

  it("mergeLiveResults applies non-empty live errors", () => {
    fileDiagnosticRegistry.mergeLiveResults([FAKE_ERROR]);
    expect(fileDiagnosticRegistry.getSummary(PATH)?.errors).toBe(1);
  });

  it("setScanResults preserves live errors for files absent from the clean snapshot", () => {
    fileDiagnosticRegistry.updateFromLspUri(ROOT, URI, [
      {
        message: "open-tab error",
        severity: 1,
        range: {
          start: { line: 2, character: 0 },
          end: { line: 2, character: 4 },
        },
      },
    ]);
    // Snapshot omits PATH entirely (incremental cache without that file) — keep live.
    fileDiagnosticRegistry.setScanResults([
      { path: "content/verse/helpers.verse", errors: 0, warnings: 0, items: [] },
    ]);
    expect(fileDiagnosticRegistry.getTotals().errors).toBe(1);
  });

  it("live push with errors still shows after a prior live clear", () => {
    fileDiagnosticRegistry.updateFromLspUri(ROOT, URI, []);
    fileDiagnosticRegistry.updateFromLspUri(ROOT, URI, [
      {
        message: "Unknown identifier `xcsdadr_profile_data`.",
        severity: 1,
        range: {
          start: { line: 14, character: 2 },
          end: { line: 14, character: 22 },
        },
      },
    ]);
    expect(fileDiagnosticRegistry.getSummary(PATH)?.errors).toBe(1);
  });

  it("dedupes same line+message (type + ctor Unknown identifier)", () => {
    fileDiagnosticRegistry.mergeLiveResults([
      {
        path: PATH,
        errors: 2,
        warnings: 0,
        items: [
          {
            line: 27,
            column: 48,
            message: "Unknown identifier `player_manager`.",
            severity: "error",
          },
          {
            line: 27,
            column: 31,
            message: "Unknown identifier `player_manager`.",
            severity: "error",
          },
        ],
      },
    ]);
    const group = fileDiagnosticRegistry.getAllFileProblems().find((f) => f.path === PATH);
    expect(group?.errors).toBe(1);
    expect(group?.items).toHaveLength(1);
    expect(group?.items[0]?.column).toBe(31);
  });
});
