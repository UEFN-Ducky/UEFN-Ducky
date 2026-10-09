import { useCallback, useEffect, useRef, useState } from "react";
import type { ComposerAttachment, MessageAttachmentDto } from "../types/panel";
import { getApi } from "./usePanelApi";

const DEFAULT_MAX_IMAGES = 40;
const DEFAULT_VIDEO_MAX_MB = 100;
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const VIDEO_EXT_RE = /\.(mp4|webm|mov|mkv)$/i;
const VIDEO_EXT_MIME: Record<string, string> = {
  mp4: "video/mp4", webm: "video/webm", mov: "video/quicktime", mkv: "video/x-matroska",
};

function videoMime(file: File): string {
  if (file.type.startsWith("video/")) return file.type;
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return VIDEO_EXT_MIME[ext] ?? "";
}

function isVideoFile(file: File): boolean {
  return file.type.startsWith("video/") || VIDEO_EXT_RE.test(file.name);
}

/** Lenient token guess (~4 characters per token); the send path counts exactly. */
function approxTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

function fileTooBigMessage(what: string, tokens: number, model: string, contextTokens: number, maxTokens: number): string {
  const n = (v: number) => v.toLocaleString("en-US");
  return `${what} about ${n(tokens)} tokens. ${model || "This model"} holds ${n(contextTokens)} tokens, `
    + `so the files in one message can use about ${n(maxTokens)}. Pick a model with a bigger context window, or attach less.`;
}

function newId(): string {
  return `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read file"));
    reader.readAsDataURL(file);
  });
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read file"));
    reader.readAsText(file);
  });
}

export function composerAttachmentsFromDto(items: MessageAttachmentDto[]): ComposerAttachment[] {
  return items.map((a): ComposerAttachment => {
    if (a.kind === "video") {
      return {
        id: newId(),
        kind: "video",
        name: a.name,
        mime: a.mime || "video/mp4",
        sizeBytes: a.size_bytes ?? 0,
        ...(a.staged_id || a.abs_path
          ? { status: "ready" as const }
          : { status: "error" as const, error: "Upload interrupted — attach the video again." }),
        ...(a.staged_id ? { stagedId: a.staged_id } : {}),
        ...(a.abs_path ? { absPath: a.abs_path } : {}),
        ...(a.media_url ? { previewUrl: a.media_url } : {}),
      };
    }
    return a.kind === "image"
      ? {
          id: newId(),
          kind: "image" as const,
          name: a.name,
          mime: a.mime || "image/png",
          dataUrl: `data:${a.mime || "image/png"};base64,${a.data_base64 ?? ""}`,
          ...(a.project_path ? { projectPath: a.project_path } : {}),
        }
      : {
          id: newId(),
          kind: "file" as const,
          name: a.name,
          mime: a.mime || "text/plain",
          text: a.text ?? "",
        };
  });
}

export function useComposerAttachments(
  initial: MessageAttachmentDto[] = [],
  hookOpts: { convId?: string; model?: string; modelLabel?: string; codingAgent?: string } = {},
) {
  const [attachments, setAttachments] = useState<ComposerAttachment[]>(() =>
    composerAttachmentsFromDto(initial),
  );
  const [error, setError] = useState("");
  const convIdRef = useRef(hookOpts.convId ?? "");
  convIdRef.current = hookOpts.convId ?? "";
  const filesRef = useRef(new Map<string, File>());
  // File text is sized by the picked model's context window (0 = the model never said; no cap).
  const [limits, setLimits] = useState({
    maxImages: DEFAULT_MAX_IMAGES, videoMaxMb: DEFAULT_VIDEO_MAX_MB, contextTokens: 0, fileMaxTokens: 0,
  });
  const modelLabel = hookOpts.modelLabel || hookOpts.model || "";

  useEffect(() => {
    let alive = true;
    void getApi()?.get_video_settings?.(hookOpts.convId ?? "", hookOpts.model ?? "", hookOpts.codingAgent ?? "").then((s) => {
      if (alive && s) {
        setLimits({
          maxImages: s.max_images_per_message || s.auto?.max_images_per_message || DEFAULT_MAX_IMAGES,
          videoMaxMb: s.video_max_mb,
          contextTokens: s.auto?.context_tokens ?? 0,
          fileMaxTokens: s.auto?.file_max_tokens ?? 0,
        });
      }
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [hookOpts.convId, hookOpts.model, hookOpts.codingAgent]);

  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;

  // A model switch re-checks files already attached: a smaller window shows before send,
  // and a bigger one clears the earlier "too big" note.
  const tooBigRef = useRef("");
  useEffect(() => {
    const files = attachmentsRef.current.filter((a) => a.kind === "file");
    const tokens = files.reduce((sum, a) => sum + (a.kind === "file" ? approxTokens(a.text) : 0), 0);
    if (limits.fileMaxTokens > 0 && tokens > limits.fileMaxTokens) {
      const what = files.length === 1 ? `${files[0].name} is` : "These files are";
      tooBigRef.current = fileTooBigMessage(what, tokens, modelLabel, limits.contextTokens, limits.fileMaxTokens);
      setError(tooBigRef.current);
      return;
    }
    const stale = tooBigRef.current;
    if (stale) setError((prev) => (prev === stale ? "" : prev));
    tooBigRef.current = "";
  }, [limits, modelLabel]);

  const patchVideo = useCallback((id: string, patch: Partial<Extract<ComposerAttachment, { kind: "video" }>>) => {
    setAttachments((prev) => prev.map((a) => (a.id === id && a.kind === "video" ? { ...a, ...patch } : a)));
  }, []);

  const stageVideo = useCallback(async (id: string, file: File) => {
    const api = getApi();
    if (!api?.stage_video_attachment) {
      patchVideo(id, { status: "error", error: "Video upload is not available." });
      return;
    }
    patchVideo(id, { status: "uploading", error: undefined });
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const res = await api.stage_video_attachment(
        convIdRef.current, file.name, videoMime(file), dataUrl.replace(/^data:[^;]*;base64,/, ""),
      );
      if (!res?.ok || !res.staged_id) {
        patchVideo(id, { status: "error", error: res?.error || "Video upload failed." });
        return;
      }
      const prep = res.prep;
      const status = !prep || prep.state === "ready" ? "ready" : prep.state === "error" ? "error" : "preparing";
      patchVideo(id, {
        stagedId: res.staged_id,
        mime: res.mime || file.type,
        sizeBytes: res.size_bytes ?? file.size,
        status,
        progress: res.ffmpeg?.progress ?? 0,
        prep,
        error: status === "error" ? prep?.error : undefined,
      });
    } catch (e) {
      patchVideo(id, { status: "error", error: e instanceof Error ? e.message : "Video upload failed." });
    }
  }, [patchVideo]);

  const retryVideo = useCallback((id: string) => {
    const att = attachments.find((a) => a.id === id);
    if (!att || att.kind !== "video") return;
    if (att.stagedId) {
      patchVideo(id, { status: "preparing", error: undefined, progress: 0, prep: undefined });
      const api = getApi();
      if (!api?.retry_video_prep) {
        void api?.install_ffmpeg?.();
        return;
      }
      void api.retry_video_prep(att.stagedId).then((r) => {
        if (r?.prep) patchVideo(id, { prep: r.prep });
      }).catch(() => undefined);
      return;
    }
    const file = filesRef.current.get(id);
    if (file) void stageVideo(id, file);
  }, [attachments, patchVideo, stageVideo]);

  const preparing = attachments.some((a) => a.kind === "video" && a.status === "preparing" && a.stagedId);
  useEffect(() => {
    if (!preparing) return;
    const timer = window.setInterval(() => {
      const api = getApi();
      const ids = attachmentsRef.current
        .flatMap((a) => (a.kind === "video" && a.status === "preparing" && a.stagedId ? [a.stagedId] : []));
      if (ids.length === 0 || !api?.get_video_prep_status) return;
      void api.get_video_prep_status(ids).then(async (res) => {
        const map = res?.prep;
        if (!map) return;
        const queued = ids.some((sid) => !map[sid] || map[sid].state === "queued" || map[sid].state === "preparing_ffmpeg");
        const ff = queued ? await api.get_ffmpeg_status?.().catch(() => undefined) : undefined;
        setAttachments((prev) => prev.map((a) => {
          if (a.kind !== "video" || a.status !== "preparing" || !a.stagedId) return a;
          const prep = map[a.stagedId];
          if (!prep) return a;
          if (prep.state === "ready") return { ...a, status: "ready", prep, progress: 1 };
          if (prep.state === "error") {
            return { ...a, status: "error", prep, error: prep.error || "Could not prepare the video." };
          }
          return { ...a, prep, ...((prep.state === "queued" || prep.state === "preparing_ffmpeg") && ff ? { progress: ff.progress } : {}) };
        }));
      }).catch(() => undefined);
    }, 700);
    return () => window.clearInterval(timer);
  }, [preparing]);

  const hasPendingVideos = attachments.some((a) => a.kind === "video" && a.status !== "ready" && !(a.status === "preparing" && a.prep?.sendable));

  /** Revoke blob previews and forget the staged File of dropped video attachments. */
  const releaseVideos = useCallback((dropped: ComposerAttachment[]) => {
    for (const a of dropped) {
      if (a.kind !== "video") continue;
      if (a.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(a.previewUrl);
      filesRef.current.delete(a.id);
    }
  }, []);

  useEffect(() => {
    const files = filesRef.current;
    return () => {
      releaseVideos(attachmentsRef.current);
      files.clear();
    };
  }, [releaseVideos]);

  const removeAttachment = useCallback((id: string) => {
    releaseVideos(attachmentsRef.current.filter((a) => a.id === id));
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }, [releaseVideos]);

  /** Replace an image attachment's pixels (e.g. after drawing annotations on it). */
  const updateAttachmentImage = useCallback((id: string, dataUrl: string) => {
    setAttachments((prev) =>
      prev.map((a) => (a.id === id && a.kind === "image" ? { ...a, dataUrl, mime: "image/png" } : a)),
    );
  }, []);

  const clearAttachments = useCallback(() => {
    releaseVideos(attachmentsRef.current);
    setAttachments([]);
    setError("");
  }, [releaseVideos]);

  /** Restore queued payloads without re-reading files or losing the current draft. */
  const restoreAttachments = useCallback((items: MessageAttachmentDto[]) => {
    const restored = composerAttachmentsFromDto(items);
    setAttachments((prev) => [...restored, ...prev]);
    setError("");
  }, []);

  /** Tab restore: overwrite, do not append onto leftover chips. */
  const replaceAttachments = useCallback((items: MessageAttachmentDto[]) => {
    releaseVideos(attachmentsRef.current);
    setAttachments(composerAttachmentsFromDto(items));
    setError("");
  }, [releaseVideos]);

  const addFiles = useCallback(
    async (
      files: FileList | File[],
      opts?: { imagesOnly?: boolean; projectPath?: string },
    ) => {
      const list = Array.from(files);
      if (list.length === 0) return;
      setError("");

      const imageCount = attachments.filter((a) => a.kind === "image").length;
      const next: ComposerAttachment[] = [];
      const uploads: Array<{ id: string; file: File }> = [];

      try {
        for (const file of list) {
          if (isVideoFile(file)) {
            if (opts?.imagesOnly) {
              setError("Only image files are supported for image upload.");
              continue;
            }
            if (file.size > limits.videoMaxMb * 1024 * 1024) {
              setError(`${file.name} exceeds the ${limits.videoMaxMb}MB video limit.`);
              continue;
            }
            const id = newId();
            const previewUrl = typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : undefined;
            filesRef.current.set(id, file);
            next.push({
              id, kind: "video", name: file.name, mime: videoMime(file), sizeBytes: file.size,
              status: "uploading", ...(previewUrl ? { previewUrl } : {}),
            });
            uploads.push({ id, file });
            continue;
          }
          const isImage = file.type.startsWith("image/");
          if (opts?.imagesOnly && !isImage) {
            setError("Only image files are supported for image upload.");
            continue;
          }
          if (isImage) {
            if (imageCount + next.filter((a) => a.kind === "image").length >= limits.maxImages) {
              setError(`At most ${limits.maxImages} images per message.`);
              break;
            }
            if (file.size > MAX_IMAGE_BYTES) {
              setError(`${file.name} exceeds 20MB limit.`);
              continue;
            }
            const dataUrl = await readFileAsDataUrl(file);
            next.push({
              id: newId(),
              kind: "image",
              name: file.name,
              mime: file.type || "image/png",
              dataUrl,
              ...(opts?.projectPath ? { projectPath: opts.projectPath } : {}),
            });
          } else {
            const text = await readFileAsText(file);
            if (limits.fileMaxTokens > 0) {
              const held = [...attachments, ...next]
                .reduce((sum, a) => sum + (a.kind === "file" ? approxTokens(a.text) : 0), 0);
              const tokens = approxTokens(text);
              if (held + tokens > limits.fileMaxTokens) {
                setError(fileTooBigMessage(`${file.name} is`, tokens, modelLabel, limits.contextTokens,
                  Math.max(0, limits.fileMaxTokens - held)));
                continue;
              }
            }
            next.push({
              id: newId(),
              kind: "file",
              name: file.name,
              mime: file.type || "text/plain",
              text,
            });
          }
        }
        if (next.length > 0) {
          setAttachments((prev) => [...prev, ...next]);
        }
        await Promise.all(uploads.map((u) => stageVideo(u.id, u.file)));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to read file");
      }
    },
    [attachments, limits, modelLabel, stageVideo],
  );

  const hasImages = attachments.some((a) => a.kind === "image");

  const toApiAttachments = useCallback((): MessageAttachmentDto[] => {
    return attachments.map((a) => {
      if (a.kind === "image") {
        const base64 = a.dataUrl.replace(/^data:[^;]+;base64,/, "");
        const row: MessageAttachmentDto = {
          kind: "image",
          name: a.name,
          mime: a.mime,
          data_base64: base64,
        };
        if (a.projectPath) row.project_path = a.projectPath;
        return row;
      }
      if (a.kind === "video") {
        const row: MessageAttachmentDto = { kind: "video", name: a.name, mime: a.mime, size_bytes: a.sizeBytes };
        if (a.stagedId) row.staged_id = a.stagedId;
        if (a.absPath) row.abs_path = a.absPath;
        return row;
      }
      return { kind: "file", name: a.name, mime: a.mime, text: a.text };
    });
  }, [attachments]);

  return {
    attachments,
    error,
    hasImages,
    hasPendingVideos,
    retryVideo,
    addFiles,
    removeAttachment,
    updateAttachmentImage,
    clearAttachments,
    restoreAttachments,
    replaceAttachments,
    toApiAttachments,
    setError,
  };
}
