// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => {
  const ed = { getModel: () => null, dispose() {}, getValue: () => "", setValue() {}, layout() {} };
  return {
    monaco: {
      Uri: { parse: (s: string) => s },
      editor: {
        create: vi.fn(() => ed),
        createModel: vi.fn(() => ({ dispose() {} })),
        getModel: () => null,
        colorize: vi.fn(async (text: string) => `<span class="mtk1">${text}</span>`),
        defineTheme() {},
        setTheme() {},
      },
    },
  };
});
const appearance = vi.hoisted(() => ({ appearanceReady: true, cssVars: {}, foundation: {} }));
vi.mock("../../verse-editor/monaco/setupMonaco", () => ({ setupMonaco: async () => fake.monaco }));
vi.mock("../../verse-editor/monaco/verseTheme", () => ({ applyVerseMonacoTheme: () => {} }));
vi.mock("../../verse-editor/monaco/forceFullTokenization", () => ({ forceFullTokenization: () => {} }));
vi.mock("../../verse-editor/monaco/useMonacoEditorLayout", () => ({ useMonacoEditorLayout: () => {} }));
vi.mock("../../verse-editor/monaco/resolveMonacoFontFamily", () => ({
  applyMonacoEditorFont: () => {}, ensureMonacoFontReady: async () => "mono", MONACO_EDITOR_FONT_SIZE: 13,
}));
vi.mock("../../theme/AppearanceContext", () => ({ useAppearanceOptional: () => appearance }));

import { RichCodeBlock } from "./RichCodeBlock";

beforeEach(() => {
  fake.monaco.editor.create.mockClear();
  fake.monaco.editor.colorize.mockClear();
});
afterEach(cleanup);

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

it("30 finished blocks create no editor; they show colorized static text", async () => {
  const { container } = render(<div>{Array.from({ length: 30 }, (_, i) => (
    <RichCodeBlock key={i} text={`print(${i})`} language="python" />
  ))}</div>);
  await flush();
  expect(fake.monaco.editor.create).toHaveBeenCalledTimes(0);
  expect(container.querySelectorAll(".live-code-preview-static .mtk1")).toHaveLength(30);
});

it("the same text and language colorize once (cached)", async () => {
  render(<><RichCodeBlock text="same()" language="python" /><RichCodeBlock text="same()" language="python" /></>);
  await flush();
  render(<RichCodeBlock text="same()" language="python" />);
  await flush();
  expect(fake.monaco.editor.colorize.mock.calls.filter((c) => c[0] === "same()").length).toBeLessThanOrEqual(1);
});

it("a click creates the editor", async () => {
  render(<RichCodeBlock text="click()" language="python" />);
  await flush();
  fireEvent.click(screen.getByLabelText("Click to scroll this code"));
  await flush();
  expect(fake.monaco.editor.create).toHaveBeenCalledTimes(1);
});

it("a streaming block renders plain <pre><code>, no highlighter or editor", async () => {
  const { container } = render(<RichCodeBlock text="live()" language="python" streaming />);
  await flush();
  expect(container.querySelector("pre.live-code-preview-static code")?.textContent).toBe("live()");
  expect(fake.monaco.editor.colorize).not.toHaveBeenCalledWith("live()", expect.anything(), expect.anything());
  fireEvent.click(screen.getByLabelText("Click to scroll this code"));
  await flush();
  expect(fake.monaco.editor.create).toHaveBeenCalledTimes(0);
});
