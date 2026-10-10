import { beforeEach, describe, expect, it, vi } from "vitest";

const api = {
  get_verse_lsp_status: vi.fn(),
  start_verse_lsp: vi.fn(),
};

vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => api }));

import { getLspStatus, startLsp } from "./verseEditorApi";

describe("verse-lsp from a phone or the website", () => {
  beforeEach(() => {
    api.get_verse_lsp_status.mockReset();
    api.start_verse_lsp.mockReset();
  });

  it("explains why instead of failing on the refused start", async () => {
    // The PC refuses: a remote call answers "method not allowed", which arrives as undefined.
    api.start_verse_lsp.mockResolvedValue(undefined);
    const status = await startLsp("C:/Island");
    expect(status.available).toBe(false);
    expect(status.running).toBe(false);
    expect(status.error).toMatch(/on your PC/);
  });

  it("reports not available for the refused status", async () => {
    api.get_verse_lsp_status.mockResolvedValue(undefined);
    const status = await getLspStatus();
    expect(status.available).toBe(false);
    expect(status.error).toMatch(/on your PC/);
  });

  it("passes the desktop window's status through", async () => {
    const live = {
      available: true, lsp_path: "verse-lsp.exe", source: "uefn", running: true,
      ws_url: "ws://127.0.0.1:5000", project_root: "C:/Island", error: "",
    };
    api.start_verse_lsp.mockResolvedValue(live);
    await expect(startLsp("C:/Island")).resolves.toBe(live);
  });
});
