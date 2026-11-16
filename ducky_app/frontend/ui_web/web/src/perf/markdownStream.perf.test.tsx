// @vitest-environment jsdom
/**
 * A streaming reply's cost per token: how much Markdown is parsed again when one word
 * arrives. Nov 16 2026: the window stalled 1,481 times an hour (p95 181 ms) with two
 * agents streaming, and the whole reply was parsed again for every token.
 * The real react-markdown is wrapped (not replaced) by a counter.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const probes = vi.hoisted(() => ({ parses: 0, chars: 0 }));

vi.mock("react-markdown", async (importOriginal) => {
  const real = await importOriginal<typeof import("react-markdown")>();
  const Real = real.default;
  return {
    ...real,
    default: (p: Parameters<typeof Real>[0]) => {
      probes.parses++;
      probes.chars += String(p.children ?? "").length;
      return <Real {...p} />;
    },
  };
});

import { markdownSections } from "../components/rich-content/MarkdownContent";
import { RichContentRenderer } from "../components/rich-content/RichContentRenderer";

it("sections cut only at a heading after a blank line, outside code fences", () => {
  const text = "# A\n\nintro\n\n## B\n\n```py\n\n# a comment, not a heading\n```\n\ntext\n## no blank line before\n\n### C\n\nend";
  const parts = markdownSections(text);
  expect(parts.join("")).toBe(text); // nothing lost, nothing added
  expect(parts.map((p) => p.split("\n")[0])).toEqual(["# A", "## B", "### C"]);
  // Reference links and footnotes resolve across sections: such text stays whole.
  const refs = "# A\n\nsee [the docs][d]\n\n## B\n\n[d]: https://example.com\n";
  expect(markdownSections(refs)).toEqual([refs]);
  expect(markdownSections("")).toEqual([""]);
});

const section = (n: number) =>
  `## Section ${n}\n\nSome **bold** text with \`code${n}\` and a [link](https://example.com/${n}).\n\n` +
  `- item one\n- item two\n- item three\n\n\`\`\`ts\n# not a heading ${n}\nconst x${n} = ${n};\n\`\`\`\n\n` +
  `| a | b |\n|---|---|\n| ${n} | 2 |\n\n`;

afterEach(cleanup);

it("a streamed token parses only the section being written, not the whole reply", () => {
  const SECTIONS = 20;
  const TOKENS = 40;
  let text = Array.from({ length: SECTIONS }, (_, n) => section(n)).join("") + "## Last\n\nstreaming";
  const view = render(<RichContentRenderer text={text} mode="streaming" />);
  const before = view.container.innerHTML.length;
  const chars: number[] = [];
  const ms: number[] = [];
  for (let i = 0; i < TOKENS; i++) {
    text += " word";
    probes.parses = 0;
    probes.chars = 0;
    const t0 = performance.now();
    act(() => view.rerender(<RichContentRenderer text={text} mode="streaming" />));
    ms.push(performance.now() - t0);
    chars.push(probes.chars);
  }
  const avg = (a: number[]) => a.reduce((s, v) => s + v, 0) / a.length;
  const stats = {
    replyChars: text.length,
    parsedCharsPerToken: Math.round(avg(chars)),
    msPerToken: +avg(ms).toFixed(1),
    msPerTokenMax: +Math.max(...ms).toFixed(1),
  };
  console.log("[markdown stream perf]", JSON.stringify(stats));
  // Every section is still on screen, the fenced "# not a heading" stayed code, and the
  // table of the last full section is a table.
  expect(view.container.querySelectorAll("h2").length + view.container.querySelectorAll(".rich-heading").length).toBeGreaterThanOrEqual(SECTIONS + 1);
  expect(view.container.textContent).toContain("# not a heading 19");
  expect(view.container.querySelectorAll("table").length).toBe(SECTIONS);
  expect(view.container.innerHTML.length).toBeGreaterThan(before);
  // The budget: about the section being written, never the 20 finished ones.
  expect(stats.parsedCharsPerToken).toBeLessThan(section(0).length * 2);
});
