// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MarkdownContent } from "../../rich-content/MarkdownContent";
import { WebSearchBody } from "./WebSearchBody";

afterEach(() => cleanup());

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
});
