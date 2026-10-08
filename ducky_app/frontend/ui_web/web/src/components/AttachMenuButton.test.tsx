// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const listGenerated = vi.fn();
const getVideoSettings = vi.fn();
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({ list_generated_images: listGenerated, get_video_settings: getVideoSettings }),
}));
const requestOpenSettings = vi.fn();
vi.mock("../navigation/openSettingsTab", () => ({ requestOpenSettings }));

const { AttachMenuButton, attachLimitsSummary } = await import("./AttachMenuButton");

const SETTINGS = {
  video_max_mb: 50,
  video_frames_per_video: 0,
  max_images_per_message: 40,
  auto: { frames_per_video: 20, max_images_per_message: 40 },
  ffmpeg: { state: "ready" as const, progress: 1, error: "", version: "n9", bundled: true },
};

afterEach(() => {
  cleanup();
  listGenerated.mockClear();
  getVideoSettings.mockReset();
  requestOpenSettings.mockClear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function openMenu(onAddFiles: (files: File[]) => void = () => {}) {
  render(<AttachMenuButton onAddFiles={onAddFiles} />);
  fireEvent.click(screen.getByTitle("Attach photos, videos or files"));
}

describe("AttachMenuButton", () => {
  it("is a paperclip button whose menu lists photo, photo or video, file and settings", () => {
    openMenu();
    expect(screen.getByRole("menu", { name: "Attach" })).toBeTruthy();
    for (const name of ["Take a photo", "Photo or video", "File", "Attachment settings"]) {
      expect(screen.getByRole("menuitem", { name })).toBeTruthy();
    }
  });

  it("uploads a file and does not list generated images", () => {
    const onAddFiles = vi.fn();
    openMenu(onAddFiles);
    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    const input = document.querySelector('input[type="file"]:not([accept])') as HTMLInputElement;
    const file = new File(["hello"], "note.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(onAddFiles).toHaveBeenCalledWith([file]);
    expect(listGenerated).not.toHaveBeenCalled();
  });

  it("Photo or video opens a picker for images and videos", () => {
    const clicked: string[] = [];
    vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(function (this: HTMLInputElement) {
      clicked.push(this.accept);
    });
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Photo or video" }));
    expect(clicked).toEqual(["image/*,video/*"]);
  });

  it("shows the video limits and opens Settings → Videos", async () => {
    getVideoSettings.mockResolvedValue(SETTINGS);
    openMenu();
    await waitFor(() => expect(screen.getByText("Videos to 50 MB · 20 frames each · 40 images per message")).toBeTruthy());
    expect(screen.getByText("Images to 20 MB, videos to 50 MB")).toBeTruthy();
    fireEvent.click(screen.getByRole("menuitem", { name: "Attachment settings" }));
    expect(requestOpenSettings).toHaveBeenCalledWith("Videos");
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("opens the camera for take a photo, and Back returns to the menu", async () => {
    const stop = vi.fn();
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Take a photo" }));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: "Back" }));
    expect(stop).toHaveBeenCalled();
    expect(screen.getByRole("menuitem", { name: "Take a photo" })).toBeTruthy();
    expect(listGenerated).not.toHaveBeenCalled();
  });

  it("summarises the limits with Auto shown as what it resolves to", () => {
    expect(attachLimitsSummary(null)).toBe("Video size, frames per video, images per message");
    expect(attachLimitsSummary({ ...SETTINGS, video_frames_per_video: 8, max_images_per_message: 12 })).toBe(
      "Videos to 50 MB · 8 frames each · 12 images per message",
    );
  });
});
