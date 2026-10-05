// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getApi } from "../../hooks/usePanelApi";
import { VideosTab } from "./VideosTab";

vi.mock("../../hooks/usePanelApi", () => ({ getApi: vi.fn() }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const settings = {
  video_max_mb: 100, video_frames_per_video: 20, max_images_per_message: 40,
  ffmpeg: { state: "missing", progress: 0, error: "", version: "n9.0.1" },
};

describe("VideosTab", () => {
  it("loads, saves a clamped value and installs ffmpeg", async () => {
    const set = vi.fn().mockResolvedValue({ ...settings, video_frames_per_video: 40 });
    const install = vi.fn().mockResolvedValue({ ...settings.ffmpeg, state: "installing" });
    vi.mocked(getApi).mockReturnValue({
      get_video_settings: vi.fn().mockResolvedValue(settings),
      set_video_settings: set,
      install_ffmpeg: install,
      get_ffmpeg_status: vi.fn().mockResolvedValue(settings.ffmpeg),
    } as never);
    render(<VideosTab />);
    const frames = await screen.findByLabelText("Frames per video");
    fireEvent.change(frames, { target: { value: "99" } });
    fireEvent.blur(frames);
    await waitFor(() => expect(set).toHaveBeenCalledWith({ video_frames_per_video: 99 }));
    expect((frames as HTMLInputElement).value).toBe("40");
    expect(screen.getByText("Not installed")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Install now" }));
    await waitFor(() => expect(install).toHaveBeenCalled());
  });

  function mockApi(over: Record<string, unknown> = {}) {
    const api = {
      get_video_settings: vi.fn().mockResolvedValue(settings),
      set_video_settings: vi.fn().mockResolvedValue(settings),
      install_ffmpeg: vi.fn().mockResolvedValue(settings.ffmpeg),
      remove_ffmpeg: vi.fn().mockResolvedValue(settings.ffmpeg),
      get_ffmpeg_status: vi.fn().mockResolvedValue(settings.ffmpeg),
      ...over,
    };
    vi.mocked(getApi).mockReturnValue(api as never);
    return api;
  }

  it("does not save an emptied field and restores the saved value", async () => {
    const api = mockApi();
    render(<VideosTab />);
    const frames = await screen.findByLabelText("Frames per video");
    fireEvent.change(frames, { target: { value: "" } });
    fireEvent.blur(frames);
    await waitFor(() => expect((frames as HTMLInputElement).value).toBe("20"));
    expect(api.set_video_settings).not.toHaveBeenCalled();
  });

  it("does not save an unchanged field", async () => {
    const api = mockApi();
    render(<VideosTab />);
    const frames = await screen.findByLabelText("Frames per video");
    fireEvent.blur(frames);
    expect(api.set_video_settings).not.toHaveBeenCalled();
  });

  it("shows a load error and retries", async () => {
    const get = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue(settings);
    mockApi({ get_video_settings: get });
    render(<VideosTab />);
    expect(await screen.findByText(/boom/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByLabelText("Frames per video");
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("shows an error when install_ffmpeg rejects", async () => {
    mockApi({ install_ffmpeg: vi.fn().mockRejectedValue(new Error("no network")) });
    render(<VideosTab />);
    await screen.findByLabelText("Frames per video");
    fireEvent.click(screen.getByRole("button", { name: "Install now" }));
    expect(await screen.findByText(/no network/)).toBeTruthy();
  });

  it("shows an Auto checkbox per field and toggles it", async () => {
    const auto = { ...settings, video_frames_per_video: 0, max_images_per_message: 12, auto: { frames_per_video: 20, max_images_per_message: 20 } };
    const api = mockApi({ get_video_settings: vi.fn().mockResolvedValue(auto) });
    render(<VideosTab />);
    const frames = (await screen.findByLabelText("Frames per video")) as HTMLInputElement;
    const autoFrames = screen.getByLabelText("Frames per video Auto") as HTMLInputElement;
    expect(autoFrames.checked).toBe(true);
    expect(frames.disabled).toBe(true);
    fireEvent.click(autoFrames);
    await waitFor(() => expect(api.set_video_settings).toHaveBeenCalledWith({ video_frames_per_video: 20 }));
    const autoImages = screen.getByLabelText("Max images per message Auto") as HTMLInputElement;
    expect(autoImages.checked).toBe(false);
    fireEvent.click(autoImages);
    await waitFor(() => expect(api.set_video_settings).toHaveBeenCalledWith({ max_images_per_message: 0 }));
  });

  it("clamps a typed 0 to the field minimum instead of storing Auto", async () => {
    const api = mockApi();
    render(<VideosTab />);
    const frames = await screen.findByLabelText("Frames per video");
    fireEvent.change(frames, { target: { value: "0" } });
    fireEvent.blur(frames);
    await waitFor(() => expect(api.set_video_settings).toHaveBeenCalledWith({ video_frames_per_video: 1 }));
  });

  it("shows an included label and no buttons when ffmpeg is bundled", async () => {
    mockApi({
      get_video_settings: vi.fn().mockResolvedValue({
        ...settings, ffmpeg: { state: "ready", progress: 1, error: "", version: "n9.0.1", bundled: true },
      }),
    });
    render(<VideosTab />);
    expect(await screen.findByText("Included with UEFN-Ducky (n9.0.1)")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Install now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  });
});
