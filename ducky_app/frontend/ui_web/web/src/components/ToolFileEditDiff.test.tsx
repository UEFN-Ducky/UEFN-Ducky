// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToolFileEditDiff } from "./ToolFileEditDiff";
import { ChatCollapseScopeProvider } from "../hooks/useChatCollapseState";
import * as diff from "../utils/fileEditDiff";

vi.mock("../verse-editor/VerseEditorProvider", () => ({ useVerseEditorOptional: () => null }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("chat file edit cards", () => {
  const edit = { path: "test.verse", before: "unchanged\nold", after: "unchanged\nnew", linesAdded: 1, linesRemoved: 1, kind: "write" as const };

  it("builds the diff only when opened, shows it in a popup, and drops it on close", () => {
    const build = vi.spyOn(diff, "buildFileEditDiff");
    const view = render(<ChatCollapseScopeProvider scope="lazy-diff"><ToolFileEditDiff edit={edit} /></ChatCollapseScopeProvider>);
    expect(view.container.textContent).toContain("+1");
    expect(build).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("button", { name: /test\.verse/ }));
    expect(build).toHaveBeenCalledTimes(1);
    const dialog = screen.getByRole("dialog");
    expect(dialog.querySelector(".tool-file-edit-diff-code")?.textContent).toContain("new");
    expect(view.container.querySelector(".tool-file-edit-diff-code")).toBeNull(); // not inline in the chat
    fireEvent.click(screen.getAllByRole("button", { name: "Close" })[0]!);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens the file from the popup at the first change and closes it", () => {
    const onOpenFile = vi.fn();
    const view = render(<ChatCollapseScopeProvider scope="lazy-diff-open"><ToolFileEditDiff edit={edit} onOpenFile={onOpenFile} /></ChatCollapseScopeProvider>);
    fireEvent.click(view.getAllByRole("button")[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Open file" }));
    expect(onOpenFile).toHaveBeenCalledWith("test.verse", "test.verse", { line: 2 });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens a collapsed filename at the first changed line", () => {
    const onOpenFile = vi.fn();
    const view = render(<ChatCollapseScopeProvider scope="lazy-diff-link"><ToolFileEditDiff edit={edit} onOpenFile={onOpenFile} /></ChatCollapseScopeProvider>);
    fireEvent.click(view.getByRole("link"));
    expect(onOpenFile).toHaveBeenCalledWith("test.verse", "test.verse", { line: 2 });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("still renders an initially expanded review inline", () => {
    const view = render(<ChatCollapseScopeProvider scope="review-diff"><ToolFileEditDiff edit={edit} defaultExpanded /></ChatCollapseScopeProvider>);
    expect(view.container.querySelector(".tool-file-edit-diff-code")?.textContent).toContain("new");
  });
});

describe("a file edit that came without its texts", () => {
  const whole = { path: "big.verse", before: "same\nold", after: "same\nnew", linesAdded: 1, linesRemoved: 1, kind: "write" as const };
  const stub = { ...whole, before: "", after: "", contentOmitted: true };

  it("shows its name and counts without reading anything, and reads once when the diff opens", async () => {
    const loadFull = vi.fn(async () => whole);
    const view = render(<ChatCollapseScopeProvider scope="omitted"><ToolFileEditDiff edit={stub} loadFull={loadFull} /></ChatCollapseScopeProvider>);
    expect(view.container.textContent).toContain("big.verse");
    expect(view.container.textContent).toContain("+1");
    expect(loadFull).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("button", { name: /big\.verse/ }));
    expect(screen.getByRole("status").textContent).toBe("Loading the changes…");
    await vi.waitFor(() => expect(screen.getByRole("dialog").querySelector(".tool-file-edit-diff-code")?.textContent).toContain("new"));
    expect(screen.getByRole("dialog").querySelector(".tool-file-edit-diff-code")?.textContent).toContain("old");
    fireEvent.click(screen.getAllByRole("button", { name: "Close" })[0]!);
    fireEvent.click(view.getByRole("button", { name: /big\.verse/ }));
    expect(screen.getByRole("dialog").querySelector(".tool-file-edit-diff-code")?.textContent).toContain("new");
    expect(loadFull).toHaveBeenCalledTimes(1); // kept for the card
  });

  it("opens the file at the first change after reading the edit", async () => {
    const loadFull = vi.fn(async () => whole);
    const onOpenFile = vi.fn();
    const view = render(<ChatCollapseScopeProvider scope="omitted-open"><ToolFileEditDiff edit={stub} loadFull={loadFull} onOpenFile={onOpenFile} /></ChatCollapseScopeProvider>);
    fireEvent.click(view.getByRole("link"));
    await vi.waitFor(() => expect(onOpenFile).toHaveBeenCalledWith("big.verse", "big.verse", { line: 2 }));
    expect(loadFull).toHaveBeenCalledTimes(1);
  });

  it("says so when the edit cannot be read", async () => {
    const view = render(<ChatCollapseScopeProvider scope="omitted-fail"><ToolFileEditDiff edit={stub} loadFull={async () => null} /></ChatCollapseScopeProvider>);
    fireEvent.click(view.getByRole("button", { name: /big\.verse/ }));
    expect((await screen.findByRole("alert")).textContent).toBe("These changes could not be loaded.");
  });
});

describe("buildFileEditDiff on big files", () => {
  it("diffs a one-line change in a 20k-line file fast, with the right line numbers", () => {
    const before = Array.from({ length: 20_000 }, (_, i) => `line ${i}`);
    const after = [...before];
    after[10_000] = "changed";
    const t = performance.now();
    const out = diff.buildFileEditDiff("big.ts", before.join("\n"), after.join("\n"), 1, 1);
    expect(performance.now() - t).toBeLessThan(500);
    const changed = out.hunks.flatMap((h) => h.lines).filter((l) => l.kind !== "context");
    expect(changed).toEqual([
      { kind: "remove", line: 10_001, text: "line 10000" },
      { kind: "add", line: 10_001, text: "changed" },
    ]);
  });

  it("shows a whole rewrite of a huge file as removed then added instead of freezing", () => {
    const before = Array.from({ length: 5_000 }, (_, i) => `a${i}`).join("\n");
    const after = Array.from({ length: 5_000 }, (_, i) => `b${i}`).join("\n");
    const t = performance.now();
    const out = diff.buildFileEditDiff("big.ts", before, after, 5_000, 5_000);
    expect(performance.now() - t).toBeLessThan(500);
    const lines = out.hunks.flatMap((h) => h.lines);
    expect(lines.filter((l) => l.kind === "remove")).toHaveLength(5_000);
    expect(lines.filter((l) => l.kind === "add")).toHaveLength(5_000);
  });

  it("still matches lines inside a small changed middle", () => {
    const out = diff.buildFileEditDiff("s.ts", "a\nb\nc\nd\ne", "a\nx\nc\ny\ne", 2, 2);
    expect(out.hunks.flatMap((h) => h.lines).map((l) => `${l.kind[0]}${l.text}`)).toEqual(["ca", "rb", "ax", "cc", "rd", "ay", "ce"]);
  });
});
