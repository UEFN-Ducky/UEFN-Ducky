// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import fixture from "../../../../../../backend/workspace/schemas/fixtures/changeset_run.json";
import type { ChangesetRunDto, ChatTab } from "../../types/panel";

const listChangesets = vi.fn();
const getContents = vi.fn();
const clearChangesets = vi.fn();
const archiveChangesets = vi.fn();
const deleteEntries = vi.fn();
const revertEntry = vi.fn();
const revertRunFn = vi.fn();

vi.mock("../../hooks/usePanelApi", () => ({
  getApi: () => ({
    list_changesets: listChangesets,
    get_changeset_entry_contents: getContents,
    clear_changesets: clearChangesets,
    archive_changesets: archiveChangesets,
    delete_changeset_entries: deleteEntries,
    revert_changeset_entry: revertEntry,
    revert_changeset: revertRunFn,
  }),
}));
vi.mock("../../hooks/useAgentEventBus", () => ({
  subscribeAgentEvents: () => () => {},
}));
vi.mock("../../hooks/useRunningAgents", () => ({
  useRunningAgents: () => new Set<string>(),
}));
vi.mock("../../contexts/ConfirmModalContext", () => ({
  useConfirmModal: () => ({ confirm: async () => true }),
}));

const { ChangesView } = await import("./ChangesView");

const run = fixture as unknown as ChangesetRunDto;

beforeEach(() => {
  listChangesets.mockReset();
  listChangesets.mockResolvedValue([run]);
  getContents.mockReset();
  getContents.mockResolvedValue({ before: "old", after: "new" });
  clearChangesets.mockReset();
  clearChangesets.mockResolvedValue({ removed_runs: 1, removed_blobs: 0 });
  archiveChangesets.mockReset();
  archiveChangesets.mockResolvedValue({ updated: 1 });
  deleteEntries.mockReset();
  deleteEntries.mockResolvedValue({ removed: 1, kept: 0 });
  revertEntry.mockReset();
  revertEntry.mockResolvedValue({ ok: true, reverted: [1], skipped_modified: [], errors: [] });
  revertRunFn.mockReset();
  revertRunFn.mockResolvedValue({ ok: true, reverted: [1], skipped_modified: [], errors: [] });
  // jsdom has no ResizeObserver; the view only uses it to track the viewport.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** The ducky name is in both the run header and the filter dropdown. */
function runHeading(container: HTMLElement): string {
  return container.querySelector(".changes-run-name")?.textContent ?? "";
}

describe("ChangesView", () => {
  it("shows the run, who made it, and every kind of change in order", async () => {
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));

    // The file writes, the editor change, and the refusal all read as one story.
    expect(screen.getByText("shop.verse")).toBeTruthy();
    expect(screen.getByText("VerifyCube")).toBeTruthy();
    expect(screen.getByText("moved +250 on Z")).toBeTruthy();
    expect(screen.getByText("BLOCKED")).toBeTruthy();
    // The refusal's reason lives in the row tooltip (blocked rows stay one line).
    expect(screen.getByTitle(/not in your write lane/i)).toBeTruthy();
  });

  it("counts the blocked attempt apart from the work that landed", async () => {
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));
    expect(screen.getByText("Ledger")).toBeTruthy();
    expect(container.querySelector(".changeset-badge--blocked")?.textContent).toBe("1 blocked");
    expect(container.querySelector(".changes-toolbar-count")).toBeNull();
  });

  it("the kind filter narrows to editor changes alone", async () => {
    render(<ChangesView />);
    await waitFor(() => expect(screen.getByText("shop.verse")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("Filter by kind"));
    fireEvent.click(screen.getByRole("radio", { name: "Editor" }));
    expect(screen.queryByText("shop.verse")).toBeNull();
    expect(screen.queryByText("BLOCKED")).toBeNull();
    expect(screen.getByText("VerifyCube")).toBeTruthy();
  });

  it("search matches editor targets, not just file paths", async () => {
    render(<ChangesView />);
    await waitFor(() => expect(screen.getByText("shop.verse")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("Search ledger"));
    fireEvent.change(screen.getByPlaceholderText("Search paths and targets"), { target: { value: "verifycube" } });
    expect(screen.getByText("VerifyCube")).toBeTruthy();
    expect(screen.queryByText("shop.verse")).toBeNull();
  });

  it("each run is its own accordion: newest open, older folded, click to flip", async () => {
    const older = { ...run, run_id: "older", ducky_name: "Artist", started: (run.started || 1) - 100 };
    listChangesets.mockResolvedValue([run, older]);
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(container.querySelectorAll(".changes-run-head").length).toBe(2));
    const heads = container.querySelectorAll<HTMLElement>(".changes-run-head");
    expect(heads[0].getAttribute("aria-expanded")).toBe("true");
    expect(heads[1].getAttribute("aria-expanded")).toBe("false");
    // Only the open run's rows are on screen.
    expect(screen.getAllByText("shop.verse").length).toBe(1);
    fireEvent.click(heads[1]);
    expect(screen.getAllByText("shop.verse").length).toBe(2);
    fireEvent.click(heads[0]);
    expect(screen.getAllByText("shop.verse").length).toBe(1);
  });

  it("says so plainly when the project has no history yet", async () => {
    listChangesets.mockResolvedValue([]);
    render(<ChangesView />);
    await waitFor(() => expect(screen.getByText(/Nothing has been changed/)).toBeTruthy());
  });

  it("scopes the ledger to one chat and hides the ducky filter", async () => {
    render(<ChangesView convId="chat-1" hideDuckyFilter />);
    await waitFor(() => expect(listChangesets).toHaveBeenCalledWith("chat-1", "", 500, false));
    expect(screen.queryByLabelText("Filter by ducky")).toBeNull();
    expect(screen.getByLabelText("Filter by kind")).toBeTruthy();
  });

  it("scopes the ledger to every current chat of one ducky type", async () => {
    const other = { ...run, run_id: "artist-run", profile_id: "artist", ducky_name: "Artist" };
    listChangesets.mockResolvedValue([run, other]);
    render(<ChangesView profileId="hacker" profileName="Hacker" />);
    await waitFor(() => expect(screen.getByText("Hacker")).toBeTruthy());
    expect(screen.queryByText("Artist")).toBeNull();
    expect(screen.queryByLabelText("Filter by ducky")).toBeNull();
  });

  it("opens a change dialog inside the tab, not over the app", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    render(<ChangesView convId="chat-1" hideDuckyFilter modalContainer={host} />);
    await waitFor(() => expect(screen.getByText("shop.verse")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("button", { name: "Diff" })[0]);
    await waitFor(() => expect(host.querySelector(".modal-backdrop--contained")).toBeTruthy());
    expect(document.body.querySelector(":scope > .modal-backdrop")).toBeNull();
    host.remove();
  });

  it("keeps a run after the chat is gone and says so", async () => {
    render(<ChangesView allChats={[{ id: "other-chat" } as ChatTab]} />);
    await waitFor(() => expect(screen.getByText("Deleted chat")).toBeTruthy());
    expect(screen.getByText("Hacker")).toBeTruthy();
  });

  it("archives this chat's history instead of deleting it", async () => {
    render(<ChangesView convId="conv-hacker-01" hideDuckyFilter />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Archive all" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Archive all" }));
    await waitFor(() => expect(archiveChangesets).toHaveBeenCalledWith("conv-hacker-01", "", "", true));
  });

  it("puts run actions on icon buttons with hover names", async () => {
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));
    expect(screen.getAllByRole("button", { name: "Archive" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Revert run" }).length).toBeGreaterThan(0);
    expect(container.querySelector(".changes-run-actions")).toBeTruthy();
  });

  it("Ctrl-click selects separate runs and Shift-click fills the range", async () => {
    const a = { ...run, run_id: "a", ducky_name: "Alpha", started: 30 };
    const b = { ...run, run_id: "b", ducky_name: "Bravo", started: 20 };
    const c = { ...run, run_id: "c", ducky_name: "Charlie", started: 10 };
    listChangesets.mockResolvedValue([a, b, c]);
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(container.querySelectorAll(".changes-run-head").length).toBe(3));
    const heads = container.querySelectorAll<HTMLElement>(".changes-run-head");
    fireEvent.click(heads[0], { ctrlKey: true });
    fireEvent.click(heads[2], { ctrlKey: true });
    expect(screen.getByText("2 selected")).toBeTruthy();
    fireEvent.click(heads[0], { shiftKey: true });
    expect(screen.getByText("3 selected")).toBeTruthy();
  });

  it("shows checkboxes once two runs are selected and can tick more", async () => {
    const a = { ...run, run_id: "a", ducky_name: "Alpha", started: 30 };
    const b = { ...run, run_id: "b", ducky_name: "Bravo", started: 20 };
    const c = { ...run, run_id: "c", ducky_name: "Charlie", started: 10 };
    listChangesets.mockResolvedValue([a, b, c]);
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(container.querySelectorAll(".changes-run-head").length).toBe(3));
    const heads = container.querySelectorAll<HTMLElement>(".changes-run-head");
    expect(container.querySelectorAll(".changes-run-check").length).toBe(0);
    fireEvent.click(heads[0], { ctrlKey: true });
    fireEvent.click(heads[1], { ctrlKey: true });
    await waitFor(() => expect(container.querySelectorAll(".changes-run-check").length).toBe(4));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Charlie" }));
    expect(screen.getByText("3 selected")).toBeTruthy();
  });

  it("right-click on a selected run deletes every selected run", async () => {
    const a = { ...run, run_id: "a", ducky_name: "Alpha", started: 30 };
    const b = { ...run, run_id: "b", ducky_name: "Bravo", started: 20 };
    listChangesets.mockResolvedValue([a, b]);
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(container.querySelectorAll(".changes-run-head").length).toBe(2));
    const heads = container.querySelectorAll<HTMLElement>(".changes-run-head");
    fireEvent.click(heads[0], { ctrlKey: true });
    fireEvent.click(heads[1], { ctrlKey: true });
    fireEvent.contextMenu(heads[0]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete all 2" }));
    await waitFor(() => {
      expect(clearChangesets).toHaveBeenCalledWith("", "", "a", true);
      expect(clearChangesets).toHaveBeenCalledWith("", "", "b", true);
    });
  });

  it("archives every selected run in one action", async () => {
    const a = { ...run, run_id: "a", ducky_name: "Alpha", started: 30 };
    const b = { ...run, run_id: "b", ducky_name: "Bravo", started: 20 };
    listChangesets.mockResolvedValue([a, b]);
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(container.querySelectorAll(".changes-run-head").length).toBe(2));
    const heads = container.querySelectorAll<HTMLElement>(".changes-run-head");
    fireEvent.click(heads[0], { ctrlKey: true });
    fireEvent.click(heads[1], { ctrlKey: true });
    fireEvent.click(screen.getByRole("button", { name: "Archive selected" }));
    await waitFor(() => {
      expect(archiveChangesets).toHaveBeenCalledWith("", "", "a", true);
      expect(archiveChangesets).toHaveBeenCalledWith("", "", "b", true);
    });
  });

  it("shows the chat tab title, not the library profile name", async () => {
    const { container } = render(
      <ChangesView
        allChats={[
          {
            id: "conv-hacker-01",
            name: "Animation Engineer",
            duckyName: "Verse Coder",
          } as ChatTab,
        ]}
      />,
    );
    await waitFor(() => expect(runHeading(container)).toBe("Animation Engineer"));
    expect(screen.queryByText("Verse Coder")).toBeNull();
  });

  it("opens that agent's tab when the avatar is clicked", async () => {
    const onOpenChat = vi.fn();
    const chat = {
      id: "conv-hacker-01",
      name: "Animation Engineer",
      duckyName: "Verse Coder",
    } as ChatTab;
    render(<ChangesView allChats={[chat]} onOpenChat={onOpenChat} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Open Animation Engineer" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Open Animation Engineer" }));
    expect(onOpenChat).toHaveBeenCalledWith(chat);
  });

  it("sorts rows by time, name, kind, or model when a column header is clicked", async () => {
    render(<ChangesView />);
    await waitFor(() => expect(screen.getByText("shop.verse")).toBeTruthy());
    expect(screen.queryByText("What")).toBeNull();
    const name = screen.getByRole("button", { name: /^Name/ });
    fireEvent.click(name);
    expect(name.getAttribute("aria-sort")).toBe("ascending");
    fireEvent.click(name);
    expect(name.getAttribute("aria-sort")).toBe("descending");
    fireEvent.click(screen.getByRole("button", { name: /^Time/ }));
    expect(screen.getByRole("button", { name: /^Time/ }).getAttribute("aria-sort")).toBe("ascending");
    fireEvent.click(screen.getByRole("button", { name: /^Model/ }));
    expect(screen.getByRole("button", { name: /^Model/ }).getAttribute("aria-sort")).toBe("ascending");
  });

  it("sorts run accordions when a column header is clicked", async () => {
    const zed = { ...run, run_id: "zed", ducky_name: "Zed", started: (run.started || 1) + 10 };
    const amy = { ...run, run_id: "amy", ducky_name: "Amy", started: (run.started || 1) - 10 };
    listChangesets.mockResolvedValue([zed, amy]);
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(container.querySelectorAll(".changes-run-head").length).toBe(2));
    const names = () => [...container.querySelectorAll(".changes-run-name")].map((el) => el.textContent);
    expect(names()).toEqual(["Zed", "Amy"]);
    fireEvent.click(screen.getByRole("button", { name: /^Name/ }));
    expect(names()).toEqual(["Amy", "Zed"]);
  });

  it("search matches a run accordion by who wrote it", async () => {
    const older = { ...run, run_id: "older", ducky_name: "Artist", started: (run.started || 1) - 100 };
    listChangesets.mockResolvedValue([run, older]);
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(container.querySelectorAll(".changes-run-head").length).toBe(2));
    fireEvent.click(screen.getByLabelText("Search ledger"));
    fireEvent.change(screen.getByPlaceholderText("Search paths and targets"), { target: { value: "artist" } });
    expect(container.querySelectorAll(".changes-run-head").length).toBe(1);
    expect(runHeading(container)).toBe("Artist");
  });

  it("hides program headers when the run is folded", async () => {
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));
    expect(container.querySelectorAll(".changes-program-head").length).toBeGreaterThan(0);
    fireEvent.click(container.querySelector(".changes-run-head")!);
    expect(container.querySelectorAll(".changes-program-head").length).toBe(0);
  });

  it("does not spam program headers when a run is only UEFN", async () => {
    const editors = Array.from({ length: 8 }, (_, i) => ({
      seq: i + 1,
      ts: 10 + i,
      path: `uefn://actor/A${i}/transform`,
      op: "editor" as const,
      tool: "set_actor_transform",
      before_hash: "",
      after_hash: "",
      in_lane: true,
      editor: {
        command: "set_actor_transform",
        kind: "actor",
        facet: "transform",
        program: "uefn",
        revertable: "manual" as const,
        targets: [{ kind: "actor", id: `A${i}`, label: `Actor ${i}` }],
      },
    }));
    listChangesets.mockResolvedValue([{ ...run, entries: editors }]);
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));
    expect(container.querySelectorAll(".changes-program-head").length).toBe(0);
  });

  it("nests a run's rows by program", async () => {
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));
    const heads = [...container.querySelectorAll(".changes-program-head .changes-group-label")].map(
      (el) => el.textContent,
    );
    expect(heads).toContain("Files");
    expect(heads).toContain("UEFN");
  });

  it("collapses a program accordion and keeps the other program", async () => {
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));
    fireEvent.click(screen.getByRole("button", { name: "UEFN, collapse" }));
    expect(screen.queryByText("VerifyCube")).toBeNull();
    expect(screen.getByText("shop.verse")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "UEFN, expand" }));
    expect(screen.getByText("VerifyCube")).toBeTruthy();
  });

  it("collapses an inner kind accordion", async () => {
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));
    expect(screen.getByRole("button", { name: "file, collapse" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "file, collapse" }));
    expect(screen.queryByText("shop.verse")).toBeNull();
    expect(screen.getByText("VerifyCube")).toBeTruthy();
    expect(screen.getByText("BLOCKED")).toBeTruthy();
  });

  it("reverts only that program from the accordion button", async () => {
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));
    fireEvent.click(screen.getByRole("button", { name: "Revert UEFN" }));
    await waitFor(() => expect(revertRunFn).toHaveBeenCalledWith(run.run_id, false, "uefn"));
  });

  it("lets you revert one write from the details pager", async () => {
    const first = run.entries[0];
    const second = {
      ...first,
      seq: 5,
      ts: 1757250041,
      before_hash: first.after_hash,
      after_hash: "aabbccdd",
      before_blob: first.after_blob,
      after_blob: "aabbccdd",
    };
    listChangesets.mockResolvedValue([{ ...run, entries: [...run.entries, second] }]);
    render(<ChangesView />);
    await waitFor(() => expect(screen.getByText("shop.verse")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("button", { name: "Diff" })[0]);
    await waitFor(() => expect(screen.getByText("All 2 writes")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Revert all writes" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    expect(screen.getByText("Write 2 of 2")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Revert this write" }).closest(".modal-footer")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Revert this write" }));
    await waitFor(() =>
      expect(revertEntry).toHaveBeenCalledWith(run.run_id, 5, false, true),
    );
  });

  it("grays the accordion while a revert is running", async () => {
    revertEntry.mockImplementation(() => new Promise(() => {}));
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Revert" }).length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByRole("button", { name: "Revert" })[0]);
    await waitFor(() => expect(container.querySelector(".changes-run-head--busy")).toBeTruthy());
    expect(container.querySelector(".changes-row--busy")).toBeTruthy();
    expect(screen.getByText("Reverting…")).toBeTruthy();
  });

  it("locks revert on a live run and offers Stop instead", async () => {
    listChangesets.mockResolvedValue([{ ...run, status: "running", ended: null }]);
    render(<ChangesView />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Stop agent" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Revert run" })).toBeNull();
    const rowRevert = screen.getAllByRole("button", { name: "Revert" })[0] as HTMLButtonElement;
    expect(rowRevert.disabled).toBe(true);
  });

  it("opens a Smart Revert picker with existing chats and New", async () => {
    render(
      <ChangesView allChats={[{ id: "c1", name: "Verse Coder" } as ChatTab]} />,
    );
    await waitFor(() => expect(screen.getAllByLabelText("Smart Revert").length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByLabelText("Smart Revert")[0]);
    expect(screen.getByText("Start a new chat")).toBeTruthy();
    expect(screen.getByText("Verse Coder")).toBeTruthy();
  });
});
