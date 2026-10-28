import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { editor } from "monaco-editor";

import { forceFullTokenization } from "./forceFullTokenization";

function fakes(lineCount: number, visibleEnd: number) {
  const forceTokenization = vi.fn();
  const model = {
    isDisposed: () => false,
    getLanguageId: () => "verse",
    getLineCount: () => lineCount,
    tokenization: { forceTokenization },
  };
  const ed = {
    getModel: () => model,
    layout: vi.fn(),
    render: vi.fn(),
    getVisibleRanges: () => [{ endLineNumber: visibleEnd }],
  };
  const setModelLanguage = vi.fn();
  const monaco = { editor: { setModelLanguage } } as unknown as typeof import("monaco-editor");
  return { ed: ed as unknown as editor.IStandaloneCodeEditor, edMock: ed, monaco, setModelLanguage, forceTokenization };
}

describe("forceFullTokenization", () => {
  beforeEach(() => {
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      cb(0);
      return 0;
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("tokenizes a model once, then only repaints on remount", () => {
    const f = fakes(3000, 40);
    forceFullTokenization(f.monaco, f.ed);
    expect(f.setModelLanguage).toHaveBeenCalledTimes(2);
    expect(f.forceTokenization).toHaveBeenCalledWith(240);

    forceFullTokenization(f.monaco, f.ed);
    expect(f.setModelLanguage).toHaveBeenCalledTimes(2);
    expect(f.forceTokenization).toHaveBeenCalledTimes(1);
    expect(f.edMock.render).toHaveBeenCalledTimes(2);
  });

  it("never asks for more lines than the file has", () => {
    const f = fakes(10, 10);
    forceFullTokenization(f.monaco, f.ed);
    expect(f.forceTokenization).toHaveBeenCalledWith(10);
  });
});
