// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("../../hooks/useAgentEventBus", () => ({
  installAgentEventBus: () => {},
  subscribeAgentEvents: () => () => {},
}));
vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => null }));
vi.mock("../api/verseEditorApi", () => ({ stopLsp: () => Promise.resolve() }));
vi.mock("../lsp/verseLspSession", () => ({ releaseVerseLspSession: () => {} }));

import { useVerseProjectDiagnostics } from "./useVerseProjectDiagnostics";
import { fileDiagnosticRegistry } from "../lsp/fileDiagnosticRegistry";

beforeEach(() => {
  vi.useFakeTimers();
  fileDiagnosticRegistry.endScan();
});

afterEach(() => {
  cleanup();
  fileDiagnosticRegistry.endScan();
  vi.useRealTimers();
});

it("does not wake the Problems header while no scan is running", async () => {
  const onChange = vi.fn();
  const setIntervalSpy = vi.spyOn(window, "setInterval");
  renderHook(() => useVerseProjectDiagnostics("", onChange));
  const callsAfterMount = onChange.mock.calls.length;

  for (let i = 0; i < 30; i++) await act(async () => { await vi.advanceTimersByTimeAsync(2000); });

  expect(onChange.mock.calls.length).toBe(callsAfterMount);
  expect(setIntervalSpy).not.toHaveBeenCalled();
  setIntervalSpy.mockRestore();
});

it("still ends a scan that stopped answering", async () => {
  const onChange = vi.fn();
  renderHook(() => useVerseProjectDiagnostics("", onChange));
  act(() => { fileDiagnosticRegistry.beginScan(); });
  expect(fileDiagnosticRegistry.isScanInProgress()).toBe(true);
  onChange.mockClear();

  for (let i = 0; i < 10; i++) await act(async () => { await vi.advanceTimersByTimeAsync(2000); });

  expect(fileDiagnosticRegistry.isScanInProgress()).toBe(false);
  expect(onChange).toHaveBeenCalled();

  onChange.mockClear();
  for (let i = 0; i < 10; i++) await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(onChange).not.toHaveBeenCalled();
});
