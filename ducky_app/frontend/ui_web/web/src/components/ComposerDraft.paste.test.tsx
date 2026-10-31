// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it } from "vitest";

import { ComposerDraft } from "./ComposerDraft";

afterEach(cleanup);

function Box() {
  const [value, setValue] = useState("");
  const [caret, setCaret] = useState(0);
  return (
    <ComposerDraft
      value={value}
      caret={caret}
      onChange={(next, at) => {
        setValue(next);
        setCaret(at);
      }}
      onCaret={setCaret}
    />
  );
}

it("scrolls to the end of a long paste", () => {
  const { getByRole } = render(<Box />);
  const box = getByRole("textbox");
  // jsdom has no layout: give the box more text than fits.
  Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => 900 });
  Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 120 });
  box.focus();
  fireEvent.paste(box, { clipboardData: { getData: () => "line\n".repeat(60) } });
  expect(box.textContent).toContain("line");
  expect(box.scrollTop).toBe(900);
});

function Seeded({ start }: { start: string }) {
  const [value, setValue] = useState(start);
  const [caret, setCaret] = useState(start.length);
  return (
    <ComposerDraft
      value={value}
      caret={caret}
      onChange={(next, at) => {
        setValue(next);
        setCaret(at);
      }}
      onCaret={setCaret}
    />
  );
}

it("replaces the selected text and keeps Windows line ends as plain new lines", () => {
  const { getByRole } = render(<Seeded start="hello world" />);
  const box = getByRole("textbox");
  box.focus();
  const text = box.firstChild as Text;
  const range = document.createRange();
  range.setStart(text, 6);
  range.setEnd(text, 11); // "world"
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
  fireEvent.paste(box, { clipboardData: { getData: () => "there\r\nfriend" } });
  expect(box.innerHTML).toBe("hello there<br>friend");
});
