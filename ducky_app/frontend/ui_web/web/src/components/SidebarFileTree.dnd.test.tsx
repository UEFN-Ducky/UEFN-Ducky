// @vitest-environment jsdom
import { act, cleanup, createEvent, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UndoHistoryProvider, useUndoHistoryOptional } from "../navigation/UndoHistoryContext";
import { useUndoShortcuts } from "../navigation/useUndoShortcuts";
import type { ProjectFileEntry } from "../types/panel";
import { setProjectContentRoot } from "../verse-editor/utils/isVerseFile";
import { SidebarFileTree, withPendingMoves } from "./SidebarFileTree";

vi.mock("../contexts/ConfirmModalContext", () => ({ useConfirmModal: () => ({ confirm: async () => true }) }));
vi.mock("../hooks/usePluginContributions", () => ({ usePluginContributions: () => ({}), pluginContributesSettingsTab: () => false }));
vi.mock("../hooks/usePluginUiPrefs", () => ({ usePluginUiPrefs: () => ({ prefs: {}, setPref: () => {} }) }));
vi.mock("./editor/FileTabHoverCard", () => ({ FileTabHoverCard: ({ children }: { children: unknown }) => children }));

const dir = (path: string): ProjectFileEntry => ({ name: path.split("/").pop()!, path, is_dir: true, read_only: false } as ProjectFileEntry);
const file = (path: string): ProjectFileEntry => ({ name: path.split("/").pop()!, path, is_dir: false, read_only: false } as ProjectFileEntry);

const listings: Record<string, ProjectFileEntry[]> = {
  __roots__: [{ name: "Content", path: "Content", is_dir: true, read_only: false, kind: "content" } as ProjectFileEntry],
  Content: [dir("Content/Art"), file("Content/a.verse")],
  "Content/Art": [],
};

const api = {
  get_listener_status: vi.fn(),
  list_project_files: vi.fn(async (path: string) => ({ entries: listings[path] ?? [] })),
  list_workspace_roots: vi.fn(async () => []),
  set_import_drop_target: vi.fn(async () => undefined),
  move_project_entry: vi.fn(async (src: string, dest: string) => ({ path: `${dest}/${src.split("/").pop()}` })),
};

function Shortcuts() {
  const history = useUndoHistoryOptional()!;
  useUndoShortcuts(history.undo, history.redo);
  return null;
}

function Tree() {
  return (
    <UndoHistoryProvider>
      <Shortcuts />
      <SidebarFileTree
        projectSlug="island"
        parentPath="Content"
        onParentPathChange={() => {}}
        onFileSelect={() => {}}
      />
    </UndoHistoryProvider>
  );
}

const transfer = () => ({ setData: vi.fn(), getData: vi.fn(), setDragImage: vi.fn(), effectAllowed: "", dropEffect: "", types: [] as string[] });
function drag(kind: "dragStart" | "dragOver" | "drop" | "dragEnd", target: Element, dataTransfer: ReturnType<typeof transfer>, clientY = 10) {
  const event = createEvent[kind](target, { dataTransfer });
  Object.defineProperty(event, "clientY", { value: clientY });
  Object.defineProperty(event, "clientX", { value: 40 });
  fireEvent(target, event);
}
const row = (id: string) => document.querySelector(`[data-tree-row="${id}"]`);

beforeEach(() => {
  setProjectContentRoot("Content");
  window.pywebview = { api } as unknown as typeof window.pywebview;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    top: 0, bottom: 20, height: 20, left: 0, right: 240, width: 240, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  api.move_project_entry.mockClear();
  delete window.pywebview;
});

describe("Content tree on the shared drag engine", () => {
  it("drops a file into a folder with one move, and Ctrl+Z in Content moves it back", async () => {
    render(<Tree />);
    await waitFor(() => expect(row("file:Content/a.verse")).toBeTruthy());
    const dt = transfer();
    drag("dragStart", row("file:Content/a.verse")!, dt);
    drag("dragOver", row("dir:Content/Art")!, dt);
    // VS Code style: the whole target folder lights up.
    expect(row("dir:Content/Art")!.classList.contains("tree-drop-into")).toBe(true);
    drag("drop", row("dir:Content/Art")!, dt);
    await waitFor(() => expect(api.move_project_entry).toHaveBeenCalledTimes(1));
    expect(api.move_project_entry).toHaveBeenCalledWith("Content/a.verse", "Content/Art");

    const shell = document.querySelector("[data-undo-scope='files']") as HTMLElement;
    act(() => shell.focus());
    // The undo step is recorded once the move has landed; press until it runs (once).
    await waitFor(() => {
      fireEvent.keyDown(shell, { key: "z", ctrlKey: true });
      expect(api.move_project_entry).toHaveBeenCalledTimes(2);
    });
    expect(api.move_project_entry).toHaveBeenLastCalledWith("Content/Art/a.verse", "Content");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.move_project_entry).toHaveBeenCalledTimes(2);
  });

  it("does nothing when a file is dropped back on its own folder", async () => {
    render(<Tree />);
    await waitFor(() => expect(row("file:Content/a.verse")).toBeTruthy());
    const dt = transfer();
    drag("dragStart", row("dir:Content/Art")!, dt);
    drag("dragOver", row("file:Content/a.verse")!, dt);
    expect(dt.dropEffect).toBe("none");
    drag("drop", row("file:Content/a.verse")!, dt);
    await Promise.resolve();
    expect(api.move_project_entry).not.toHaveBeenCalled();
  });
});

describe("moves still being saved", () => {
  it("are drawn at their destination, folder contents included", () => {
    const cache = new Map<string, ProjectFileEntry[]>([
      ["Content", [dir("Content/Art"), dir("Content/Lib"), file("Content/a.verse")]],
      ["Content/Art", []],
      ["Content/Lib", [file("Content/Lib/x.verse")]],
    ]);
    const shown = withPendingMoves(cache, [
      { from: "Content/a.verse", to: "Content/Art/a.verse", entry: file("Content/a.verse") },
      { from: "Content/Lib", to: "Content/Art/Lib", entry: dir("Content/Lib") },
    ]);
    expect(shown.get("Content")!.map((e) => e.path)).toEqual(["Content/Art"]);
    expect(shown.get("Content/Art")!.map((e) => e.path)).toEqual(["Content/Art/Lib", "Content/Art/a.verse"]);
    expect(shown.get("Content/Art/Lib")!.map((e) => e.path)).toEqual(["Content/Art/Lib/x.verse"]);
    expect(withPendingMoves(cache, [])).toBe(cache);
  });
});
