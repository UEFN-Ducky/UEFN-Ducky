import { describe, expect, it } from "vitest";
import { cleanLoginCode, loginCanRetry, loginLinkPrompt, PASTED_A_LINK } from "./codingAgentLoginCopy";

describe("cleanLoginCode", () => {
  it("keeps both halves of Claude's code and drops what a copy brought along", () => {
    expect(cleanLoginCode("abc123#state456")).toEqual({ code: "abc123#state456", error: "" });
    expect(cleanLoginCode("  abc123#state456\r\n")).toEqual({ code: "abc123#state456", error: "" });
    expect(cleanLoginCode("abc123\n#state456")).toEqual({ code: "abc123#state456", error: "" });
    expect(cleanLoginCode('"abc123#state456"')).toEqual({ code: "abc123#state456", error: "" });
    expect(cleanLoginCode("`abc123#state456`")).toEqual({ code: "abc123#state456", error: "" });
  });

  it("reads the code out of a pasted callback link", () => {
    expect(
      cleanLoginCode("https://console.anthropic.com/oauth/code/callback?code=abc123&state=state456"),
    ).toEqual({ code: "abc123#state456", error: "" });
    expect(cleanLoginCode("https://example.test/callback?code=only")).toEqual({ code: "only", error: "" });
  });

  it("says so when the sign-in link itself was pasted", () => {
    expect(cleanLoginCode("https://claude.ai/oauth/authorize?client_id=x&response_type=code")).toEqual({
      code: "",
      error: PASTED_A_LINK,
    });
  });
});

describe("loginLinkPrompt", () => {
  it("does not say waiting after a spawn error", () => {
    expect(
      loginLinkPrompt({
        starting: false,
        authUrl: "",
        error: "Git Bash not found. Install Git for Windows or add bash.exe to PATH.",
      }),
    ).toBe("");
  });

  it("waits only while the session is still producing a link", () => {
    expect(loginLinkPrompt({ starting: true, authUrl: "", error: "" })).toBe(
      "Getting the sign-in link…",
    );
    expect(loginLinkPrompt({ starting: false, authUrl: "", error: "" })).toBe(
      "Waiting for the sign-in link…",
    );
  });
});

describe("loginCanRetry", () => {
  it("offers Try again when the link never arrived", () => {
    expect(loginCanRetry({ starting: false, authUrl: "", error: "Claude never printed a sign-in link." })).toBe(
      true,
    );
    expect(loginCanRetry({ starting: false, authUrl: "https://claude.com/x", error: "x" })).toBe(false);
  });
});
