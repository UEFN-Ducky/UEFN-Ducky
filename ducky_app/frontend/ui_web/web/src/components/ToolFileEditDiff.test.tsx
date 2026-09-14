// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToolFileEditDiff } from "./ToolFileEditDiff";
import { ChatCollapseScopeProvider } from "../hooks/useChatCollapseState";
import * as diff from "../utils/fileEditDiff";

vi.mock("../verse-editor/VerseEditorProvider", () => ({ useVerseEditorOptional: () => null }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("collapsed file edit cards", () => {
  const edit = { path: "test.verse", before: "unchanged\nold", after: "unchanged\nnew", linesAdded: 1, linesRemoved: 1, kind: "write" as const };

  it("builds and mounts the diff only when expanded, and removes it on collapse", () => {
    const build = vi.spyOn(diff, "buildFileEditDiff");
    const view = render(<ChatCollapseScopeProvider scope="lazy-diff"><ToolFileEditDiff edit={edit} /></ChatCollapseScopeProvider>);
    expect(view.container.textContent).toContain("+1");
    expect(build).not.toHaveBeenCalled();
    expect(view.container.querySelector(".tool-file-edit-diff-code")).toBeNull();
    fireEvent.click(view.getByRole("button"));
    expect(build).toHaveBeenCalledTimes(1);
    expect(view.container.querySelector(".tool-file-edit-diff-code")?.textContent).toContain("new");
    fireEvent.click(view.getByRole("button"));
    expect(view.container.querySelector(".tool-file-edit-diff-code")).toBeNull();
  });

  it("opens a collapsed filename at the first changed line", () => {
    const onOpenFile = vi.fn();
    const view = render(<ChatCollapseScopeProvider scope="lazy-diff-link"><ToolFileEditDiff edit={edit} onOpenFile={onOpenFile} /></ChatCollapseScopeProvider>);
    fireEvent.click(view.getByRole("link"));
    expect(onOpenFile).toHaveBeenCalledWith("test.verse", "test.verse", { line: 2 });
    expect(view.container.querySelector(".tool-file-edit-diff-code")).toBeNull();
  });

  it("still renders an initially expanded review", () => {
    const view = render(<ChatCollapseScopeProvider scope="review-diff"><ToolFileEditDiff edit={edit} defaultExpanded /></ChatCollapseScopeProvider>);
    expect(view.container.querySelector(".tool-file-edit-diff-code")?.textContent).toContain("new");
  });
});
