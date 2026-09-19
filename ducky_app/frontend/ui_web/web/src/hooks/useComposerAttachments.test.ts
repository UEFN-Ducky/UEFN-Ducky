// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useComposerAttachments } from "./useComposerAttachments";
import type { MessageAttachmentDto } from "../types/panel";

afterEach(cleanup);

describe("restoring queued attachments", () => {
  it("round-trips images, capture paths and file text while preserving draft attachments", () => {
    const { result } = renderHook(() => useComposerAttachments());
    const draft: MessageAttachmentDto = {
      kind: "file", name: "draft.txt", mime: "text/plain", text: "existing draft",
    };
    const queued: MessageAttachmentDto[] = [
      {
        kind: "image", name: "capture.png", mime: "image/png",
        data_base64: "aW1hZ2U=", project_path: "Saved/DuckyCaptures/capture.png",
      },
      { kind: "file", name: "notes.txt", mime: "text/plain", text: "queued\nnotes" },
    ];
    act(() => result.current.restoreAttachments([draft]));
    act(() => result.current.restoreAttachments(queued));
    expect(result.current.hasImages).toBe(true);
    expect(result.current.toApiAttachments()).toEqual([...queued, draft]);
    expect(new Set(result.current.attachments.map((a) => a.id)).size).toBe(3);
    act(() => result.current.removeAttachment(result.current.attachments[0].id));
    expect(result.current.toApiAttachments()).toEqual([queued[1], draft]);
  });

  it("restores an image-only prompt and allows clearing it after resend", () => {
    const { result } = renderHook(() => useComposerAttachments());
    act(() => result.current.restoreAttachments([
      { kind: "image", name: "shot.png", data_base64: "eA==" },
    ]));
    expect(result.current.toApiAttachments()).toEqual([
      { kind: "image", name: "shot.png", mime: "image/png", data_base64: "eA==" },
    ]);
    act(() => result.current.clearAttachments());
    expect(result.current.attachments).toEqual([]);
  });

  it("seeds from initial DTOs and replaceAttachments overwrites instead of appending", () => {
    const seeded: MessageAttachmentDto[] = [
      { kind: "file", name: "draft.txt", mime: "text/plain", text: "cached draft" },
      {
        kind: "image", name: "card.png", mime: "image/png",
        data_base64: "aW1hZ2U=", project_path: "Saved/DuckyCaptures/card.png",
      },
    ];
    const { result } = renderHook(() => useComposerAttachments(seeded));
    expect(result.current.toApiAttachments()).toEqual(seeded);

    const next: MessageAttachmentDto[] = [
      { kind: "file", name: "other.txt", mime: "text/plain", text: "switched chat" },
    ];
    act(() => result.current.replaceAttachments(next));
    expect(result.current.toApiAttachments()).toEqual(next);
    expect(result.current.attachments).toHaveLength(1);
  });
});
