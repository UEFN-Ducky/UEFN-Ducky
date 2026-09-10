// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { RichContentRenderer } from "./RichContentRenderer";
import { MarkdownContent } from "./MarkdownContent";
import { CHAT_APPEARANCE_PREVIEW } from "../../views/settings/ChatResponsePreview";

afterEach(cleanup);

describe("rich reply rendering", () => {
  it("supports every Appearance color in ordinary replies without creating links", () => {
    const colors = ["blue", "purple", "green", "amber", "yellow", "red"];
    const text = colors.map((color) => `[**${color} label**](ducky:${color})`).join(" · ");
    const { container } = render(<RichContentRenderer text={text} />);
    for (const color of colors) {
      expect(container.querySelector(`.rich-text-accent.rich-tone--${color} strong`)?.textContent).toBe(`${color} label`);
    }
    expect(container.querySelector("a, button")).toBeNull();
    expect(container.textContent).not.toContain("ducky:");
  });

  it("colors regular Markdown headings, emphasis and code by category without a report", () => {
    const text = [
      "## Verse device\n\n**Verse** `logic.verse`",
      "## Blender mesh\n\n**Blender** `crate.blend`",
      "## Blueprint\n\n**Blueprint** `BP_Crate`",
      "## UMG widget\n\n**UMG** `UW_Inventory`",
      "## UEFN devices\n\n**UEFN** `trigger_device`",
      "## Error details\n\n**Failed** to compile.",
    ].join("\n\n");
    const { container } = render(<RichContentRenderer text={text} />);
    expect(container.querySelectorAll(".rich-inventory, .rich-stats")).toHaveLength(0);
    for (const color of ["purple", "green", "amber", "yellow", "blue", "red"]) {
      expect(container.querySelector(`h2.rich-tone--${color}`)).not.toBeNull();
      expect(container.querySelector(`strong.rich-tone--${color}`)).not.toBeNull();
      if (color !== "red") expect(container.querySelector("button.rich-ref")).not.toBeNull();
    }
  });

  it("accepts color markers inside structured block text and preserves surrounding formatting", () => {
    const { container } = render(<RichContentRenderer text={JSON.stringify({ __rich: true, blocks: [
      { type: "paragraph", text: "[**Verse**](ducky:purple) with [a badge `Props`](ducky:amber)." },
      { type: "callout", tone: "info", text: "[**Verified**](ducky:green); [**pending**](ducky:amber)." },
    ] })} />);
    expect(container.querySelector(".rich-tone--purple strong")?.textContent).toBe("Verse");
    expect(container.querySelector(".rich-text-accent.rich-tone--amber button.rich-ref")?.textContent).toBe("Props");
    expect(container.querySelector(".rich-callout .rich-text-accent.rich-tone--green")?.textContent).toBe("Verified");
    expect(container.textContent).not.toContain("ducky:");
  });

  it("rejects arbitrary color values and unsafe URLs while retaining their text", () => {
    const { container } = render(<RichContentRenderer text={
      "[unknown](ducky:chartreuse) [injected](ducky:red;display:none) [unsafe](javascript:alert%281%29)"
    } />);
    expect(container.querySelector(".rich-text-accent, a, [style]")).toBeNull();
    expect(container.textContent).toContain("unknown");
    expect(container.textContent).toContain("injected");
    expect(container.textContent).toContain("unsafe");
  });

  it("does not mistake no errors or long ordinary prose for a colored status", () => {
    const { container } = render(<MarkdownContent text={
      "**No errors found**. **" + "This sentence mentions a Verse device but remains ordinary prose. ".repeat(3) + "**"
    } />);
    expect(container.querySelector("strong[class*='rich-tone--']")).toBeNull();
  });

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
    expect(container.querySelectorAll("button.rich-ref")).toHaveLength(6);
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
    expect(container.querySelector(".rich-callout--warn button.rich-ref")?.textContent).toBe("Props");
    expect(container.querySelector(".rich-callout--warn strong")?.textContent).toBe("verification");
  });

  it.each(["", "python"])("keeps fenced code with language '%s' in a code block", (language) => {
    const { container } = render(<MarkdownContent text={"```" + language + "\n# Example\n> [!WARNING]\nprint('hello')\n```"} />);
    expect(container.querySelectorAll(".rich-code--block")).toHaveLength(1);
    expect(container.querySelector(".rich-report-header")).toBeNull();
    expect(container.querySelector(".rich-callout")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toContain("print('hello')");
  });

  it("opens a Verse chip and still shows hover info for names with no location", () => {
    const onOpenFile = vi.fn();
    const { container, getByLabelText } = render(
      <MarkdownContent onOpenFile={onOpenFile} text="See `ledger_full_test_device.verse` and `EntryTrigger`." />,
    );
    fireEvent.click(getByLabelText(/Verse file: ledger_full_test_device\.verse/i));
    expect(onOpenFile).toHaveBeenCalledWith("Content/Verse/ledger_full_test_device.verse", "ledger_full_test_device.verse");
    fireEvent.mouseEnter(getByLabelText(/Field \/ label: EntryTrigger/i));
    expect(container.querySelector(".rich-ref-tip-name")?.textContent).toBe("EntryTrigger");
    expect(container.querySelector(".rich-ref-tip-hint")?.textContent).toMatch(/copies the name/i);
  });

  it("does not execute markup or unsafe links inside blocks", () => {
    const { container } = render(<RichContentRenderer text={JSON.stringify({
      __rich: true, blocks: [{ type: "paragraph", text: "<script>alert(1)</script>\n\n[unsafe](javascript:alert(1)) and `safe`" }],
    })} />);
    expect(container.querySelector("script, a[href^='javascript:']")).toBeNull();
    expect(container.querySelector("button.rich-ref")?.textContent).toBe("safe");
  });

  it("renders the Appearance chat preview as the ledger dashboard widgets", () => {
    const { container } = render(<MarkdownContent text={CHAT_APPEARANCE_PREVIEW} />);
    expect(container.querySelector(".rich-stats")).not.toBeNull();
    expect(container.querySelector(".rich-stats-num")?.textContent).toBe("23");
    expect(container.querySelector(".rich-stats-num--blocked")?.textContent).toBe("4");
    expect(container.querySelector(".rich-stats-programs-total")?.textContent).toMatch(/24/);
    expect(container.querySelectorAll(".rich-inventory-item")).toHaveLength(6);
    expect(container.querySelector(".rich-inventory-head")?.textContent).toMatch(/Inventory Added/);
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
