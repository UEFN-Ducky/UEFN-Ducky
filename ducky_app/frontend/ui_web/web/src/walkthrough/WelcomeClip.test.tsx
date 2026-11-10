// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { rememberWelcomeChat, WelcomeClip } from "./WelcomeClip";

beforeEach(() => localStorage.clear());
afterEach(cleanup);

describe("WelcomeClip", () => {
  it("shows in the welcome chat only, and never after Close", () => {
    rememberWelcomeChat("welcome-1");
    const { unmount } = render(<WelcomeClip chatId="other" src="clip.mp4" />);
    expect(screen.queryByRole("region")).toBeNull();
    unmount();

    const first = render(<WelcomeClip chatId="welcome-1" src="clip.mp4" />);
    const video = first.container.querySelector("video")!;
    expect(video.muted).toBe(true);
    expect(video.loop).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("region")).toBeNull();
    first.unmount();

    rememberWelcomeChat("welcome-1"); // a second setup run doesn't bring it back
    render(<WelcomeClip chatId="welcome-1" src="clip.mp4" />);
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("shows nothing without a bundled clip (the first-run tour is the walkthrough)", () => {
    rememberWelcomeChat("welcome-1");
    render(<WelcomeClip chatId="welcome-1" src="" />);
    expect(screen.queryByRole("region")).toBeNull();
  });
});
