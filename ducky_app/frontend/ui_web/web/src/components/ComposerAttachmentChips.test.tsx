// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ComposerAttachmentChips } from "./ComposerAttachmentChips";

afterEach(cleanup);

describe("video chips", () => {
  it("shows progress while preparing and a retry button on error", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <ComposerAttachmentChips
        attachments={[{ id: "v1", kind: "video", name: "bug.mp4", mime: "video/mp4", sizeBytes: 5_000_000, status: "preparing", progress: 0.42, prep: { state: "queued", frames_done: 0, frames_total: 20, transcript: "skipped", transcript_note: "", error: "" } }]}
        onRemove={() => {}}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByText("Waiting…")).toBeTruthy();
    rerender(
      <ComposerAttachmentChips
        attachments={[{ id: "v1", kind: "video", name: "bug.mp4", mime: "video/mp4", sizeBytes: 5_000_000, status: "error", error: "offline" }]}
        onRemove={() => {}}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByTitle("offline").textContent).toBe("offline");
    fireEvent.click(screen.getByRole("button", { name: "Retry bug.mp4" }));
    expect(onRetry).toHaveBeenCalledWith("v1");
  });

  const prep = (over: object) => ({
    state: "ready", frames_done: 20, frames_total: 20, transcript: "ok", transcript_note: "", error: "", ...over,
  });
  const chip = (status: "preparing" | "ready", p: object) => (
    <ComposerAttachmentChips
      attachments={[{ id: "v1", kind: "video", name: "bug.mp4", mime: "video/mp4", sizeBytes: 5_000_000, status, prep: prep(p) as never }]}
      onRemove={() => {}}
    />
  );

  it("labels extraction, transcription and ready", () => {
    const { rerender } = render(chip("preparing", { state: "extracting", frames_done: 7 }));
    expect(screen.getByText("Extracting frames 7/20")).toBeTruthy();
    rerender(chip("preparing", { state: "transcribing" }));
    expect(screen.getByText("Transcribing audio…")).toBeTruthy();
    rerender(chip("preparing", { state: "transcribing", sendable: true }));
    expect(screen.getByText("Transcribing audio… (you can send)")).toBeTruthy();
    rerender(chip("ready", {}));
    expect(screen.getByText("Ready")).toBeTruthy();
    expect(screen.queryByTestId("chip-note")).toBeNull();
  });

  it("shows ffmpeg download progress while preparing_ffmpeg", () => {
    const att = (progress?: number) => (
      <ComposerAttachmentChips
        attachments={[{ id: "v1", kind: "video", name: "bug.mp4", mime: "video/mp4", sizeBytes: 1, status: "preparing", ...(progress === undefined ? {} : { progress }), prep: prep({ state: "preparing_ffmpeg", frames_done: 0 }) as never }]}
        onRemove={() => {}}
      />
    );
    const { rerender } = render(att(0.5));
    expect(screen.getByText("Preparing ffmpeg… 50%")).toBeTruthy();
    rerender(att(0));
    expect(screen.getByText("Preparing ffmpeg…")).toBeTruthy();
  });

  it("shows the transcript note as a secondary line when ready", () => {
    render(chip("ready", { transcript: "none", transcript_note: "No audio track" }));
    const note = screen.getByTestId("chip-note");
    expect(note.textContent).toBe("No audio track");
    expect(note.getAttribute("title")).toBe("No audio track");
  });
});
