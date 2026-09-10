// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { RichContentRenderer } from "./RichContentRenderer";
import { MarkdownContent } from "./MarkdownContent";

afterEach(cleanup);

describe("rich reply rendering", () => {
  it("renders emphasis, badges and working file links throughout structured blocks", () => {
    const onOpenFile = vi.fn();
    const desc = "**Verified** `Props` in [the device](Verse/test.verse).";
    const { container, getAllByRole } = render(<RichContentRenderer onOpenFile={onOpenFile} text={JSON.stringify({
      __rich: true,
      blocks: [
        { type: "header", title: "Build **complete**" },
        { type: "paragraph", text: desc },
        { type: "list", items: [desc] },
        { type: "table", headers: ["Result"], rows: [[desc]] },
        { type: "inventory", items: [{ kind: "verse", title: "test.verse", desc }] },
        { type: "callout", tone: "success", text: desc },
      ],
    })} />);
    expect(container.querySelectorAll("strong")).toHaveLength(6);
    expect(container.querySelectorAll("code.rich-code--inline")).toHaveLength(6);
    expect(container.textContent).not.toContain("**");
    const links = getAllByRole("button", { name: "the device" });
    expect(links).toHaveLength(5);
    links.forEach((link) => fireEvent.click(link));
    expect(onOpenFile).toHaveBeenCalledTimes(5);
    expect(onOpenFile).toHaveBeenLastCalledWith("Content/Verse/test.verse", "test.verse");
    expect(container.querySelector(".rich-callout-title")?.textContent).toBe("Verified");
  });

  it("renders ordinary instructions with numbered steps and semantic callouts", () => {
    const { container } = render(<MarkdownContent text={
      "## Build and wire\n\n3. **Compile** the `device.verse` file.\n4. **Wire** the device.\n\n> [!WARNING] Pending reference\n> `Props` still needs **verification**."
    } />);
    expect(container.querySelector("ol")?.start).toBe(3);
    expect(container.querySelectorAll("li strong")).toHaveLength(2);
    expect(container.querySelector(".rich-callout--warn code")?.textContent).toBe("Props");
    expect(container.querySelector(".rich-callout--warn strong")?.textContent).toBe("verification");
  });

  it.each(["", "python"])("keeps fenced code with language '%s' in a code block", (language) => {
    const { container } = render(<MarkdownContent text={"```" + language + "\n# Example\n> [!WARNING]\nprint('hello')\n```"} />);
    expect(container.querySelectorAll("pre.rich-code--block")).toHaveLength(1);
    expect(container.querySelector(".rich-report-header")).toBeNull();
    expect(container.querySelector(".rich-callout")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toContain("print('hello')");
  });

  it("does not execute markup or unsafe links inside blocks", () => {
    const { container } = render(<RichContentRenderer text={JSON.stringify({
      __rich: true, blocks: [{ type: "paragraph", text: "<script>alert(1)</script>\n\n[unsafe](javascript:alert(1)) and `safe`" }],
    })} />);
    expect(container.querySelector("script, a[href^='javascript:']")).toBeNull();
    expect(container.querySelector("code")?.textContent).toBe("safe");
  });

  it("renders the shared agent prompt examples as the documented widgets", () => {
    // Contract check against the actual prompt used by embedded and coding agents.
    const prompt = readFileSync("../../../backend/agent/prompt.py", "utf8").replace(/\r\n/g, "\n");
    const rule = prompt.split('CHAT_REPORT_RULE = """')[1]!.split('"""')[0]!;
    const samples = [...rule.matchAll(/```(?:markdown)?\n([\s\S]*?)```/g)].map((m) => m[1]!);
    expect(samples).toHaveLength(2);
    const text = samples.join("\n\n").replace(/\bN\b/g, "2");
    const { container } = render(<MarkdownContent text={text} />);
    expect(container.querySelector(".rich-stats")).not.toBeNull();
    expect(container.querySelectorAll(".rich-inventory-item")).toHaveLength(2);
    expect(container.querySelectorAll(".rich-callout")).toHaveLength(4);
    expect(container.querySelector(".rich-callout--success")).not.toBeNull();
  });
});
