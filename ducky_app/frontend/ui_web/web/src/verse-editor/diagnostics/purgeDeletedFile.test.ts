import { beforeEach, describe, expect, it, vi } from "vitest";

const removeFile = vi.fn();
const closeDocument = vi.fn();
const pushLocalAgentEvent = vi.fn();

vi.mock("../lsp/fileDiagnosticRegistry", () => ({
  fileDiagnosticRegistry: { removeFile: (...args: unknown[]) => removeFile(...args) },
}));

vi.mock("../lsp/verseLspSession", () => ({
  getVerseLspSession: () => ({
    projectRoot: "C:/proj",
    monaco: {},
    client: {
      canonicalUri: (uri: string) => uri,
      closeDocument: (...args: unknown[]) => closeDocument(...args),
    },
  }),
}));

vi.mock("../lsp/uriUtils", () => ({
  toLspProtocolUri: (_root: string, path: string) => `file:///${path}`,
}));

vi.mock("../../hooks/useAgentEventBus", () => ({
  pushLocalAgentEvent: (...args: unknown[]) => pushLocalAgentEvent(...args),
}));

import { notifyMissingProjectFile, purgeDeletedFileState } from "./purgeDeletedFile";

describe("purgeDeletedFile", () => {
  beforeEach(() => {
    removeFile.mockClear();
    closeDocument.mockClear();
    pushLocalAgentEvent.mockClear();
  });

  it("purgeDeletedFileState drops registry + LSP doc", () => {
    purgeDeletedFileState("Content/a.verse");
    expect(removeFile).toHaveBeenCalledWith("Content/a.verse");
    expect(closeDocument).toHaveBeenCalled();
    expect(pushLocalAgentEvent).not.toHaveBeenCalled();
  });

  it("notifyMissingProjectFile also emits file_deleted so tabs auto-close", () => {
    notifyMissingProjectFile("Content\\gone.verse");
    expect(removeFile).toHaveBeenCalledWith("Content/gone.verse");
    expect(pushLocalAgentEvent).toHaveBeenCalledWith({
      type: "file_deleted",
      old_path: "Content/gone.verse",
    });
  });
});
