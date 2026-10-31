// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MarkdownContent } from "../../rich-content/MarkdownContent";
import { WebSearchBody } from "./WebSearchBody";

afterEach(() => cleanup());

describe("web completion details", () => {
  const card = (resultText: string, args: Record<string, unknown> = {}, toolName = "web_search") =>
    render(<WebSearchBody toolName={toolName} args={args} argsText="" resultText={resultText} isSuccess isError={false} />);

  it.each(["", "{}", "Codex MCP"])("does not mistake missing native result details for empty search: %s", (result) => {
    const { container } = card(result, { query: "Codex MCP" });
    expect(container.textContent).toContain("Result details unavailable");
    expect(container.textContent).not.toContain("No results.");
    expect(container.textContent).not.toContain("Searching the web");
  });

  it("reserves No results for an explicit empty search", () => {
    expect(card('{"ok":true,"results":[]}').container.textContent).toContain("No results.");
  });

  it.each(["web_fetch", "web_search"])("preserves returned page text and its safe link for %s", (name) => {
    const { container } = card(JSON.stringify({ url: "https://developers.openai.com/codex/mcp", text: "Connect Codex to MCP servers." }), {}, name);
    expect(container.textContent).toContain("Connect Codex to MCP servers.");
    expect(container.querySelector("a")?.href).toBe("https://developers.openai.com/codex/mcp");
  });

  it("shows a video's channel, length, contact sheet and frames saved in the chat", () => {
    const media = (name: string) => `http://127.0.0.1:4199/chat-attachments/abc-123/${name}`;
    const { container } = card(JSON.stringify({
      ok: true,
      kind: "video_frames",
      url: "https://www.youtube.com/watch?v=FBs--2nSPLQ",
      title: "I challenged Rust Developers",
      channel: "Arjay McCandless",
      length: "1:38",
      sheet: { media_url: media("video_FBs--2nSPLQ_sheet12.jpg") },
      frames: [
        { t: 13, time: "0:13", media_url: media("video_FBs--2nSPLQ_000130.jpg") },
        { t: 30, time: "0:30", media_url: "https://evil.example/x.jpg" },
      ],
    }), { url: "https://www.youtube.com/watch?v=FBs--2nSPLQ", frames: [13, 30] }, "web_video");
    expect(container.textContent).toContain("Arjay McCandless · 1:38");
    const srcs = Array.from(container.querySelectorAll("img")).map((img) => img.getAttribute("src"));
    expect(srcs).toEqual([media("video_FBs--2nSPLQ_sheet12.jpg"), media("video_FBs--2nSPLQ_000130.jpg")]);
    expect(container.textContent).toContain("0:13");
    expect(container.textContent).not.toContain("0:30");
  });

  it("lists GitHub search results like web results", () => {
    const { container } = card(JSON.stringify({
      ok: true,
      action: "search",
      query: "\"twelve-dollar-server-challenge\"",
      results: [{ title: "arjaythedev/twelve-dollar-server-challenge", url: "https://github.com/arjaythedev/twelve-dollar-server-challenge", snippet: "★ 900" }],
    }), { action: "search", query: "\"twelve-dollar-server-challenge\"" }, "web_github");
    expect(container.querySelector("a")?.href).toBe("https://github.com/arjaythedev/twelve-dollar-server-challenge");
    expect(container.textContent).toContain("★ 900");
  });

  it("preserves successful plain text without calling it empty", () => {
    const { container } = card("MCP server configuration reference", { url: "https://example.com" }, "web_fetch");
    expect(container.textContent).toContain("MCP server configuration reference");
    expect(container.textContent).not.toContain("No results.");
  });
});

describe("WebSearchBody", () => {
  it("shows the query, title, snippet, and link", () => {
    const { container } = render(
      <WebSearchBody
        toolName="web_search"
        args={{ query: "country flag" }}
        argsText=""
        resultText={JSON.stringify({
          ok: true,
          query: "country flag",
          results: [
            {
              title: "Flag of Japan",
              url: "https://example.com/japan",
              snippet: "A red disc on a white field.",
            },
          ],
        })}
        isSuccess
        isError={false}
      />,
    );
    expect(container.textContent).toContain("country flag");
    expect(container.textContent).toContain("Flag of Japan");
    expect(container.textContent).toContain("A red disc on a white field.");
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://example.com/japan");
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows https pictures from the search card and skips other srcs", () => {
    const { container } = render(
      <WebSearchBody
        toolName="web_search"
        args={{ query: "brainrot", images: true }}
        argsText=""
        resultText={JSON.stringify({
          ok: true,
          query: "brainrot",
          images: [
            {
              title: "Tung Tung",
              url: "https://example.com/page",
              thumb: "https://example.com/thumb.jpg",
            },
            {
              title: "Local",
              url: "https://example.com/page",
              thumb: "http://127.0.0.1/secret.png",
            },
          ],
          results: [],
        })}
        isSuccess
        isError={false}
      />,
    );
    const img = container.querySelector("img");
    expect(img?.getAttribute("src")).toBe("https://example.com/thumb.jpg");
    expect(img?.getAttribute("alt")).toBe("Tung Tung");
    expect(container.innerHTML).not.toContain("127.0.0.1");
    expect(container.querySelectorAll("img")).toHaveLength(1);
  });

  it("still shows results when the page text was truncated", () => {
    const prefix = JSON.stringify({
      ok: true,
      query: "country flag",
      results: [{ title: "Flag of Japan", url: "https://example.com/japan", snippet: "Red disc." }],
    });
    const cut = prefix.slice(0, -1) + ',"text":"AAAA';
    const { container } = render(
      <WebSearchBody
        toolName="web_search"
        args={{ query: "country flag" }}
        argsText=""
        resultText={cut}
        isSuccess
        isError={false}
      />,
    );
    expect(container.textContent).toContain("Flag of Japan");
  });

  it("shows a plain-text tool error instead of a generic failure", () => {
    const { container } = render(
      <WebSearchBody
        toolName="web_search"
        args={{ query: "brainrot" }}
        argsText=""
        resultText="UEFN listener offline on port 4200"
        isSuccess={false}
        isError
      />,
    );
    expect(container.textContent).toContain("UEFN listener offline on port 4200");
    expect(container.textContent).not.toContain("Web lookup failed");
  });
});

describe("MarkdownContent remote images", () => {
  it("does not request a remote image", () => {
    const { container } = render(
      <MarkdownContent text={"Look ![flag](https://evil.example/a.png)"} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("flag");
    expect(container.innerHTML).not.toContain("evil.example");
  });

  it("shows a picture saved in this chat, on the desktop and rewritten for Remote View", () => {
    const desk = render(<MarkdownContent text={"![Top](http://127.0.0.1:4199/chat-attachments/c1/s1_top.png)"} />);
    expect(desk.container.querySelector("img")?.getAttribute("src")).toBe("http://127.0.0.1:4199/chat-attachments/c1/s1_top.png");
    cleanup();
    const phone = render(<MarkdownContent text={"![Top](/static/plugins/uefn-ducky/panel/chat-attachments/c1/s1_top.png)"} />);
    expect(phone.container.querySelector("img")?.getAttribute("src")).toBe("/static/plugins/uefn-ducky/panel/chat-attachments/c1/s1_top.png");
    cleanup();
    const other = render(<MarkdownContent text={"![x](http://127.0.0.1:4199/__panel_api/secret.png)"} />);
    expect(other.container.querySelector("img")).toBeNull();
  });
});
