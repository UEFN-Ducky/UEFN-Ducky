// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

const cacheFont = vi.fn<(family: string) => Promise<{ ok: boolean; href?: string; error?: string }>>();

vi.mock("../hooks/onApiReady", () => ({
  onApiReady: (cb: (api: { cache_font: typeof cacheFont }) => void) => {
    cb({ cache_font: cacheFont });
    return () => {};
  },
}));

import { syncGoogleUIFontLink } from "./googleFont";

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  cacheFont.mockReset();
  document.head.innerHTML = "";
});

describe("Appearance fonts", () => {
  it("loads a picked Google font from the local copy, never from Google", async () => {
    cacheFont.mockResolvedValue({ ok: true, href: "/__fonts/lobster/font.css" });
    syncGoogleUIFontLink('"Lobster", "Segoe UI", sans-serif');
    await flush();
    expect(cacheFont).toHaveBeenCalledWith("Lobster");
    const link = document.getElementById("uefn-google-font-ui") as HTMLLinkElement;
    expect(link.getAttribute("href")).toBe("/__fonts/lobster/font.css");
    expect(document.head.innerHTML).not.toContain("googleapis");
  });

  it("adds no link when the font can't be downloaded, and drops it for a built-in font", async () => {
    cacheFont.mockResolvedValue({ ok: false, error: "offline" });
    syncGoogleUIFontLink('"Pacifico", sans-serif');
    await flush();
    expect(document.getElementById("uefn-google-font-ui")).toBeNull();

    cacheFont.mockResolvedValue({ ok: true, href: "/__fonts/oswald/font.css" });
    syncGoogleUIFontLink('"Oswald", sans-serif');
    await flush();
    expect(document.getElementById("uefn-google-font-ui")).not.toBeNull();
    syncGoogleUIFontLink('"Inter", sans-serif');
    expect(document.getElementById("uefn-google-font-ui")).toBeNull();
  });
});
