import { describe, expect, it } from "vitest";
import { tokenizeJson } from "./highlightJson";

describe("tokenizeJson", () => {
  it("colors keys, strings, numbers, and keywords", () => {
    const kinds = tokenizeJson('{"n": 2, "ok": true, "x": null}').map((t) => `${t.kind}:${t.text}`);
    expect(kinds).toContain('string:"n"');
    expect(kinds).toContain("number:2");
    expect(kinds).toContain("keyword:true");
    expect(kinds).toContain("keyword:null");
  });
});
