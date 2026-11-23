// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

let api: Record<string, unknown> = {};
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
const { MoveToLinux, passphraseProblem } = await import("./MoveToLinux");

const move = vi.fn();
beforeEach(() => {
  move.mockReset();
  api = {
    move_to_linux: move,
    pick_workflow_folder: async () => ({ ok: true, folder: "C:/Backups" }),
    pick_workflow_files: async () => ({ ok: true, files: [{ path: "/home/me/ducky-move-2026-11-22.zip" }] }),
  };
});
afterEach(cleanup);

const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

it("passphrase rules", () => {
  expect(passphraseProblem("export", "short", "short")).toMatch(/at least 8/);
  expect(passphraseProblem("export", "long enough", "long enougj")).toMatch(/don't match/);
  expect(passphraseProblem("export", "long enough", "long enough")).toBe("");
  expect(passphraseProblem("import", "long enough", "")).toBe("");
});

it("export waits for matching passphrases, then shows the saved file", async () => {
  move.mockResolvedValue({ ok: true, path: "C:/Backups/ducky-move-2026-11-22.zip", counts: { chats: 300, keys: 12 } });
  render(<MoveToLinux />);
  const go = screen.getByRole("button", { name: "Export…" });
  type("Passphrase", "correct horse");
  type("Repeat passphrase", "correct horsf");
  expect(screen.getByText("The passphrases don't match.")).toBeTruthy();
  expect((go as HTMLButtonElement).disabled).toBe(true);
  type("Repeat passphrase", "correct horse");
  expect((go as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(go);
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("300 chats, 12 keys"));
  expect(move).toHaveBeenCalledWith("export", "C:/Backups", "correct horse");
});

it("import shows the summary, and the error from a wrong passphrase", async () => {
  render(<MoveToLinux />);
  fireEvent.click(screen.getByRole("tab", { name: "Import from Windows" }));
  type("Passphrase", "wrong passphrase");
  move.mockResolvedValueOnce({ ok: false, error: "Wrong passphrase, or the archive was changed." });
  fireEvent.click(screen.getByRole("button", { name: "Import…" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Wrong passphrase, or the archive was changed."));
  move.mockResolvedValueOnce({ ok: true, summary: "300 chats, 12 keys imported, 0 skipped" });
  fireEvent.click(screen.getByRole("button", { name: "Import…" }));
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("300 chats, 12 keys imported, 0 skipped"));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(move).toHaveBeenLastCalledWith("import", "/home/me/ducky-move-2026-11-22.zip", "wrong passphrase");
});
