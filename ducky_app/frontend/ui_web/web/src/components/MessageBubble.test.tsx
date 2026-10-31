// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./ModelSelector", () => ({
  ModelSelector: (p: { selectedModel: string }) => (
    <button type="button" data-testid="interrupted-model-picker">
      {p.selectedModel}
    </button>
  ),
}));
vi.mock("./rich-content/RichContentRenderer", () => ({
  RichContentRenderer: (p: { text: string }) => <div>{p.text}</div>,
}));
vi.mock("./ThinkingBlock", () => ({ ThinkingBlock: () => null }));
vi.mock("../voice/VoiceControls", () => ({ SpeakMessageButton: () => null }));
vi.mock("../voice/TtsReadAlong", () => ({
  mapReadAlong: () => null,
  markSpokenWord: () => false,
  clearSpokenWord: () => {},
}));
vi.mock("../voice/ttsEngine", () => ({
  ttsEngine: {
    onProgress: () => () => {},
    getProgress: () => ({ state: "idle", sourceText: "", spokenText: "", charIndex: 0, loading: false }),
  },
}));

const openPanelRoute = vi.fn();
vi.mock("../navigation/openPanelRoute", () => ({ openPanelRoute: (...a: unknown[]) => openPanelRoute(...a) }));

import { MessageBubble, MotionFrame } from "./MessageBubble";
import { __setAssetBaseForTest } from "../remote/assetBase";

describe("MotionFrame", () => {
  afterEach(() => {
    cleanup();
    __setAssetBaseForTest(null);
    vi.unstubAllGlobals();
  });

  it("loads the scene by src on the desktop", () => {
    __setAssetBaseForTest("/");
    const { container } = render(<MotionFrame url="http://127.0.0.1:4199/generated-images/m.html" name="m.html" />);
    const frame = container.querySelector("iframe");
    expect(frame?.getAttribute("src")).toBe("http://127.0.0.1:4199/generated-images/m.html");
    expect(frame?.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("hands the sandboxed frame the scene HTML on the phone (Remote View)", async () => {
    const base = "/static/plugins/uefn-ducky/panel/";
    __setAssetBaseForTest(base);
    const fetchMock = vi.fn().mockResolvedValue(new Response("<p>scene</p>", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { container, findByTitle } = render(<MotionFrame url={`${base}generated-images/m.html`} name="m.html" />);
    const frame = await findByTitle("m.html");
    expect(fetchMock).toHaveBeenCalledWith(`${base}generated-images/m.html`);
    expect(frame.getAttribute("srcdoc")).toBe("<p>scene</p>");
    expect(frame.getAttribute("src")).toBeNull();
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(container.querySelectorAll("iframe")).toHaveLength(1);
  });
});

describe("MessageBubble interrupted continue", () => {
  afterEach(() => cleanup());

  it("puts the model picker next to Continue", () => {
    render(
      <MessageBubble
        role="assistant"
        text="partial"
        incomplete
        error="You've hit your session limit"
        onContinue={() => {}}
        selectedModel="claude-sonnet-5"
        setSelectedModel={() => {}}
      />,
    );
    expect(screen.getByText(/Interrupted: You've hit your session limit/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
    expect(screen.getByTestId("interrupted-model-picker").textContent).toBe("claude-sonnet-5");
  });

  it("shows the Strict protection recovery action on a file-protection interruption", () => {
    render(<MessageBubble role="assistant" text="" incomplete error="AI_FILE_PROTECTION: external agents blocked" />);
    expect(screen.getByRole("button", { name: "Turn off Strict protection" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Permissions and rules" })).toBeTruthy();
  });
});

it("a streaming checkpoint has no Interrupted banner or Continue until stopped", () => {
  const { rerender } = render(<MessageBubble role="assistant" text="partial" incomplete isStreaming onContinue={() => {}} />);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  rerender(<MessageBubble role="assistant" text="partial" incomplete onContinue={() => {}} />);
  expect(screen.getByText("Interrupted before finishing")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
});

describe("MessageBubble pictures", () => {
  afterEach(() => cleanup());

  it("shows the pictures the agent made under its reply", () => {
    // Oct 11 2026: replies never drew their attachments, so generated pictures never showed.
    render(
      <MessageBubble
        role="assistant"
        text="Here is your duck."
        attachments={[{ kind: "image", name: "duck.png", mime: "image/png", data_base64: "iVBORw0KGgo=" }]}
      />,
    );
    const img = screen.getByAltText("duck.png") as HTMLImageElement;
    expect(img.src).toBe("data:image/png;base64,iVBORw0KGgo=");
    expect(img.closest(".message-bubble-reply-pictures")).not.toBeNull();
  });
});

describe("MessageBubble generated media", () => {
  afterEach(() => cleanup());
  const url = "http://127.0.0.1:1/generated-images/";

  it("plays a video with Open, Export MP4 and Save", () => {
    const { container } = render(
      <MessageBubble role="assistant" text="Made it." attachments={[{ kind: "video", name: "clip.mp4", media_url: `${url}clip.mp4` }]} />,
    );
    expect((container.querySelector("video") as HTMLVideoElement).src).toBe(`${url}clip.mp4`);
    expect((screen.getByText("Export MP4") as HTMLAnchorElement).getAttribute("download")).toBe("clip.mp4");
    expect((screen.getByText("Save") as HTMLAnchorElement).getAttribute("href")).toBe(`${url}clip.mp4`);
    fireEvent.click(screen.getByText("Open in Media Kit"));
    expect(openPanelRoute).toHaveBeenCalledWith("settings.tab", "Island Media Kit");
  });

  it("plays audio with no Export MP4", () => {
    const { container } = render(
      <MessageBubble role="assistant" text="" attachments={[{ kind: "audio", name: "tune.wav", media_url: `${url}tune.wav` }]} />,
    );
    expect(container.querySelector("audio")).not.toBeNull();
    expect(screen.queryByText("Export MP4")).toBeNull();
    expect(screen.getByText("Save")).toBeTruthy();
  });

  it("runs a motion scene in a script-only sandbox and exports through Media Kit", () => {
    const seen: CustomEvent[] = [];
    const on = (e: Event) => seen.push(e as CustomEvent);
    window.addEventListener("ducky:media-kit", on);
    const { container } = render(
      <MessageBubble role="assistant" text="" attachments={[{ kind: "motion", name: "intro.html", path: "C:/g/intro.html", media_url: `${url}intro.html` }]} />,
    );
    const frame = container.querySelector("iframe") as HTMLIFrameElement;
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts"); // never allow-same-origin
    fireEvent.click(screen.getByText("Export MP4"));
    window.removeEventListener("ducky:media-kit", on);
    expect(seen[0].detail).toEqual({ action: "export", kind: "motion", name: "intro.html", path: "C:/g/intro.html" });
  });
});
