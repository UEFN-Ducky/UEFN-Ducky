import { describe, expect, it } from "vitest";
import { loginCanRetry, loginLinkPrompt } from "./codingAgentLoginCopy";

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
