// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StoreOverview, StoreTablePreview } from "../../types/panel";

const storeOverview = vi.fn();
const storeTablePreview = vi.fn();
const storeAction = vi.fn();

vi.mock("../../hooks/usePanelApi", () => ({
  getApi: () => ({
    store_overview: storeOverview,
    store_table_preview: storeTablePreview,
    store_action: storeAction,
  }),
}));
vi.mock("../../hooks/onApiReady", () => ({
  onApiReady: (fn: () => void) => {
    fn();
    return () => {};
  },
}));
vi.mock("../../contexts/ConfirmModalContext", () => ({
  useConfirmModal: () => ({ confirm: async () => true, alert: async () => {} }),
}));
vi.mock("../../ui-targets/registry", () => ({
  useUiTarget: () => ({ current: null }),
}));

const { AppDataDatabase } = await import("./AppDataDatabase");

function overview(partial: Partial<StoreOverview> = {}): StoreOverview {
  return {
    path: "C:\\Users\\me\\AppData\\Local\\UEFN-Ducky\\ducky.db",
    exists: true,
    sqlite_version: "3.50.4",
    backends: { settings: "rows", chats: "rows" },
    size_bytes: 2_500_000,
    wal_bytes: 0,
    schema_version: 6,
    head_version: 6,
    journal_mode: "wal",
    tables: [
      { name: "conversations", label: "Conversations", group: "chats", group_label: "Chats", description: "One row per chat.", rows: 12, clearable: false },
      { name: "messages", label: "Messages", group: "chats", group_label: "Chats", description: "Every turn.", rows: 340, clearable: false },
      { name: "events", label: "Events", group: "logs", group_label: "Logs & caches", description: "Errors and activity.", rows: 9, clearable: true },
    ],
    snapshots: [{ name: "ducky-20260910-120000-daily.db", bytes: 2_400_000, ts: Date.now() / 1000 - 3600 }],
    legacy: { rel: "legacy", bytes: 1024, files: 3, stores: [{ name: "chats", bytes: 1024, files: 3 }] },
    importers: [{ name: "chats", report: { conversations: 12 }, ts: Date.now() / 1000 - 7200 }],
    integrity: { result: "ok", ts: Date.now() / 1000 - 60 },
    last_restore: null,
    restore_pending: false,
    clean_boots: 1,
    clean_boots_needed: 3,
    error: "",
    ...partial,
  };
}

const preview: StoreTablePreview = {
  ok: true,
  table: "events",
  columns: ["id", "kind", "message"],
  rows: [{ id: 1, kind: "error", message: "boom" }],
  total: 1,
  offset: 0,
  limit: 25,
};

beforeEach(() => {
  storeOverview.mockReset();
  storeOverview.mockResolvedValue(overview());
  storeTablePreview.mockReset();
  storeTablePreview.mockResolvedValue(preview);
  storeAction.mockReset();
  storeAction.mockResolvedValue({ ok: true, result: "ok" });
});

afterEach(() => cleanup());

describe("AppDataDatabase", () => {
  it("renders health, tables with row counts, snapshots and upgrade leftovers", async () => {
    render(<AppDataDatabase onOpenRel={() => {}} />);
    await waitFor(() => expect(screen.getByText("2.4 MB")).toBeTruthy());
    expect(screen.getByText("OK")).toBeTruthy();
    expect(screen.getByText("361")).toBeTruthy(); // total rows across tables
    expect(screen.getByText("Messages")).toBeTruthy();
    expect(screen.getByText("ducky-20260910-120000-daily.db")).toBeTruthy();
    expect(screen.getByText(/1 of 3 so far/)).toBeTruthy();
    expect(screen.getByText("Import log")).toBeTruthy();
  });

  it("runs actions through store_action and reloads", async () => {
    render(<AppDataDatabase onOpenRel={() => {}} />);
    await waitFor(() => expect(screen.getByText("Check integrity")).toBeTruthy());
    fireEvent.click(screen.getByText("Check integrity"));
    await waitFor(() => expect(storeAction).toHaveBeenCalledWith("check", ""));
    expect(storeOverview.mock.calls.length).toBeGreaterThanOrEqual(2);
    await waitFor(() => expect(screen.getByText(/Integrity check: ok/)).toBeTruthy());
    fireEvent.click(screen.getByText("Delete now"));
    await waitFor(() => expect(storeAction).toHaveBeenCalledWith("retire_legacy", ""));
    fireEvent.click(screen.getByText("Restore"));
    await waitFor(() => expect(storeAction).toHaveBeenCalledWith("restore", "ducky-20260910-120000-daily.db"));
  });

  it("previews rows when a table is expanded and clears clearable tables", async () => {
    render(<AppDataDatabase onOpenRel={() => {}} />);
    await waitFor(() => expect(screen.getByText("Events")).toBeTruthy());
    const summary = screen.getByText("Events").closest("summary")!;
    const details = summary.parentElement as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    await waitFor(() => expect(storeTablePreview).toHaveBeenCalledWith("events", 25, 0));
    await waitFor(() => expect(screen.getByText("boom")).toBeTruthy());
    const clearButtons = screen.getAllByText("Clear");
    fireEvent.click(clearButtons[0]);
    await waitFor(() => expect(storeAction).toHaveBeenCalledWith("clear_table", "events"));
  });

  it("shows the rollback banner and a staged restore", async () => {
    storeOverview.mockResolvedValue(overview({ backends: { settings: "files", chats: "rows" }, restore_pending: true }));
    render(<AppDataDatabase onOpenRel={() => {}} />);
    await waitFor(() => expect(screen.getByText(/Rollback switch active: settings/)).toBeTruthy());
    expect(screen.getByText(/A snapshot restore is staged/)).toBeTruthy();
    fireEvent.click(screen.getByText("cancel it"));
    await waitFor(() => expect(storeAction).toHaveBeenCalledWith("cancel_restore", ""));
  });
});
