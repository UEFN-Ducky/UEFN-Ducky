import { useCallback, useEffect, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { getApi } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import type { VideoSettingsDto } from "../types/panel";
import { useUiTarget } from "../ui-targets/registry";

interface AttachMenuButtonProps {
  disabled?: boolean;
  onAddFiles: (files: File[]) => void;
}

/** Phone back camera when the panel is on a phone; webcam on the desktop. */
export async function openCameraStream(): Promise<MediaStream> {
  const media = navigator.mediaDevices;
  if (!media?.getUserMedia) {
    throw new Error("Camera is not available on this device.");
  }
  try {
    return await media.getUserMedia({ video: { facingMode: "environment" }, audio: false });
  } catch {
    return await media.getUserMedia({ video: true, audio: false });
  }
}

function stopStream(stream: MediaStream | null) {
  stream?.getTracks().forEach((track) => track.stop());
}

/** The limits Settings → Videos holds, as one line (0 = Auto, shown as what it resolves to). */
export function attachLimitsSummary(s: VideoSettingsDto | null): string {
  if (!s) return "Video size, frames per video, images per message";
  const frames = s.video_frames_per_video || s.auto?.frames_per_video || 0;
  const images = s.max_images_per_message || s.auto?.max_images_per_message || 0;
  return [
    `Videos to ${s.video_max_mb} MB`,
    frames ? `${frames} frames each` : "",
    images ? `${images} images per message` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function MenuItem({
  icon,
  tone,
  title,
  sub,
  trailing,
  onClick,
}: {
  icon: ReactNode;
  tone: "blue" | "purple" | "amber" | "plain";
  title: string;
  sub: string;
  trailing?: ReactNode;
  onClick: () => void;
}) {
  return (
    <button type="button" role="menuitem" className="attach-menu-item" aria-label={title} onClick={onClick}>
      <span className={`attach-menu-item-icon is-${tone}`} aria-hidden>
        {icon}
      </span>
      <span className="attach-menu-item-text">
        <span className="attach-menu-item-title">{title}</span>
        <span className="attach-menu-item-sub" title={sub}>
          {sub}
        </span>
      </span>
      {trailing}
    </button>
  );
}

export function AttachMenuButton({ disabled, onAddFiles }: AttachMenuButtonProps) {
  const [open, setOpen] = useState(false);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [cameraError, setCameraError] = useState("");
  const [limits, setLimits] = useState<VideoSettingsDto | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const mediaRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const uiTargetRef = useUiTarget("chat.composer.generated", {
    kind: "button",
    label: "Attach",
    route: "chat",
  });

  const close = useCallback(() => {
    setOpen(false);
    setCameraError("");
    setStream((current) => {
      stopStream(current);
      return null;
    });
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !stream) return;
    video.srcObject = stream;
    const played = video.play?.();
    if (played && typeof played.catch === "function") void played.catch(() => undefined);
  }, [stream]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  // The limits under each row come from Settings → Videos; read them each time the menu opens.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    void Promise.resolve(getApi()?.get_video_settings?.())
      .then((s) => {
        if (alive && s) setLimits(s);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [open]);

  const startCamera = async () => {
    setCameraError("");
    try {
      const next = await openCameraStream();
      setStream((current) => {
        stopStream(current);
        return next;
      });
    } catch {
      setCameraError("Camera blocked. Allow the camera, or upload a file instead.");
    }
  };

  const stopCamera = () => {
    setCameraError("");
    setStream((current) => {
      stopStream(current);
      return null;
    });
  };

  const shutter = () => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) {
      setCameraError("Camera is not ready yet.");
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          setCameraError("Could not save that photo.");
          return;
        }
        const file = new File([blob], `photo-${Date.now()}.jpg`, { type: "image/jpeg" });
        onAddFiles([file]);
        close();
      },
      "image/jpeg",
      0.92,
    );
  };

  const onPicked = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (files.length === 0) return;
    onAddFiles(files);
    close();
  };

  const videoMb = limits?.video_max_mb;

  return (
    <div className="attach-menu-wrap" ref={wrapRef}>
      <button
        ref={uiTargetRef}
        type="button"
        className={`snip-btn attach-menu-trigger${open ? " is-open" : ""}`}
        title="Attach photos, videos or files"
        aria-label="Attach photos, videos or files"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <Icons.Paperclip />
      </button>
      {open ? (
        <div className="attach-menu-flyout" role="menu" aria-label="Attach">
          {cameraError ? <div className="attach-menu-error">{cameraError}</div> : null}
          {stream ? (
            <>
              <video ref={videoRef} className="attach-menu-preview" autoPlay playsInline muted />
              <div className="attach-menu-camera-actions">
                <button type="button" className="attach-menu-camera-btn" onClick={stopCamera}>
                  Back
                </button>
                <button type="button" className="attach-menu-camera-btn is-primary" onClick={shutter}>
                  <Icons.Camera /> Use photo
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="attach-menu-caption">Attach</div>
              <MenuItem
                icon={<Icons.Camera />}
                tone="blue"
                title="Take a photo"
                sub="Use this device's camera"
                onClick={() => void startCamera()}
              />
              <MenuItem
                icon={<Icons.Image />}
                tone="purple"
                title="Photo or video"
                sub={videoMb ? `Images to 20 MB, videos to ${videoMb} MB` : "Images and videos from this device"}
                onClick={() => mediaRef.current?.click()}
              />
              <MenuItem
                icon={<Icons.File />}
                tone="amber"
                title="File"
                sub="Code, text or data, as much as the model can read"
                onClick={() => fileRef.current?.click()}
              />
              <div className="attach-menu-divider" role="separator" />
              <MenuItem
                icon={<Icons.Sliders />}
                tone="plain"
                title="Attachment settings"
                sub={attachLimitsSummary(limits)}
                trailing={
                  <span className="attach-menu-item-chevron" aria-hidden>
                    <Icons.ChevronRight />
                  </span>
                }
                onClick={() => {
                  close();
                  requestOpenSettings("Videos");
                }}
              />
              <div className="attach-menu-tip">You can also drop files onto the chat.</div>
            </>
          )}
        </div>
      ) : null}
      <input ref={mediaRef} className="attach-menu-file" type="file" multiple accept="image/*,video/*" onChange={onPicked} />
      <input ref={fileRef} className="attach-menu-file" type="file" multiple onChange={onPicked} />
    </div>
  );
}
