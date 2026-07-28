import { useCallback, useState } from "react";
import type { ComposerAttachment, MessageAttachmentDto } from "../types/panel";

const MAX_IMAGES = 4;
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const MAX_FILE_TEXT = 256 * 1024;

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

export function useComposerAttachments() {
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [error, setError] = useState("");

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  /** Replace an image attachment's pixels (e.g. after drawing annotations on it). */
  const updateAttachmentImage = useCallback((id: string, dataUrl: string) => {
    setAttachments((prev) =>
      prev.map((a) => (a.id === id && a.kind === "image" ? { ...a, dataUrl, mime: "image/png" } : a)),
    );
  }, []);

  const clearAttachments = useCallback(() => {
    setAttachments([]);
    setError("");
  }, []);

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

      try {
        for (const file of list) {
          const isImage = file.type.startsWith("image/");
          if (opts?.imagesOnly && !isImage) {
            setError("Only image files are supported for image upload.");
            continue;
          }
          if (isImage) {
            if (imageCount + next.filter((a) => a.kind === "image").length >= MAX_IMAGES) {
              setError(`At most ${MAX_IMAGES} images per message.`);
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
            if (file.size > MAX_FILE_TEXT) {
              setError(`${file.name} exceeds 256KB text limit.`);
              continue;
            }
            const text = await readFileAsText(file);
            if (text.length > MAX_FILE_TEXT) {
              setError(`${file.name} exceeds 256KB text limit.`);
              continue;
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
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to read file");
      }
    },
    [attachments],
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
      return { kind: "file", name: a.name, mime: a.mime, text: a.text };
    });
  }, [attachments]);

  return {
    attachments,
    error,
    hasImages,
    addFiles,
    removeAttachment,
    updateAttachmentImage,
    clearAttachments,
    toApiAttachments,
    setError,
  };
}
