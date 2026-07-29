import { useMemo, useState } from "react";
import { Icons } from "../../../icons/Icons";
import type { ToolCardBodyProps } from "../toolCardTypes";

export function asScreenshotRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function parseScreenshotResult(raw: string): Record<string, unknown> | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    let parsed: unknown = JSON.parse(trimmed);
    for (let i = 0; i < 3; i++) {
      if (typeof parsed === "string") {
        const inner = parsed.trim();
        if (!inner.startsWith("{") && !inner.startsWith("[")) break;
        parsed = JSON.parse(inner);
        continue;
      }
      const obj = asScreenshotRecord(parsed);
      if (!obj) break;
      if (obj.data !== undefined) {
        const data = obj.data;
        if (typeof data === "string") {
          parsed = data;
          continue;
        }
        const dataObj = asScreenshotRecord(data);
        if (
          dataObj &&
          (dataObj.base64 ||
            dataObj.path ||
            dataObj.filepath ||
            dataObj.preview_file ||
            dataObj.media_url)
        ) {
          return dataObj;
        }
      }
      return obj;
    }
    return asScreenshotRecord(parsed);
  } catch {
    return null;
  }
}

export function pickScreenshotBase64(data: Record<string, unknown> | null): string {
  if (!data) return "";
  for (const key of ["base64", "data_base64", "image_base64", "png_base64"] as const) {
    const v = data[key];
    if (typeof v === "string" && v.trim().length > 32 && !v.startsWith("[omitted")) {
      return v.trim();
    }
  }
  for (const nest of ["blender_result", "result", "image"] as const) {
    const inner = asScreenshotRecord(data[nest]);
    if (!inner) continue;
    const nested = pickScreenshotBase64(inner);
    if (nested) return nested;
  }
  return "";
}

export function pickScreenshotMediaUrl(data: Record<string, unknown> | null): string {
  if (!data) return "";
  for (const key of ["media_url", "preview_url", "url"] as const) {
    const v = data[key];
    if (typeof v === "string" && /^https?:\/\//i.test(v.trim())) return v.trim();
  }
  return "";
}

export function pickScreenshotPath(
  data: Record<string, unknown> | null,
  args: Record<string, unknown>,
): string {
  if (data) {
    for (const key of ["path", "filepath", "preview_file", "filename", "file", "asset_path"] as const) {
      const v = data[key];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
    const blender = asScreenshotRecord(data.blender_result);
    if (blender) {
      for (const key of ["filepath", "path", "filename"] as const) {
        const v = blender[key];
        if (typeof v === "string" && v.trim()) return v.trim();
      }
    }
  }
  for (const key of ["path", "filename", "filepath", "asset_path"] as const) {
    const v = args[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}

function pickMime(data: Record<string, unknown> | null): string {
  const fmt = typeof data?.format === "string" ? data.format.trim().toLowerCase() : "";
  if (fmt === "jpg" || fmt === "jpeg") return "image/jpeg";
  if (fmt === "webp") return "image/webp";
  return "image/png";
}

/** Screenshot / preview — prefers media_url (safe), then legacy base64. */
export function ScreenshotBody({
  args,
  resultText,
  showResult = true,
  isError,
  hint,
}: ToolCardBodyProps) {
  const data = showResult ? parseScreenshotResult(resultText) : null;
  const mediaUrl = showResult ? pickScreenshotMediaUrl(data) : "";
  const base64 = showResult && !mediaUrl ? pickScreenshotBase64(data) : "";
  const path = pickScreenshotPath(data, args);
  const mime = pickMime(data);
  const src = useMemo(() => {
    if (mediaUrl) return mediaUrl;
    if (base64) return `data:${mime};base64,${base64}`;
    return "";
  }, [mediaUrl, base64, mime]);
  const [imgFailed, setImgFailed] = useState(false);

  const showImage = !!src && !imgFailed && !isError;

  return (
    <div className="tool-card-screenshot-body">
      {showImage ? (
        <div className="tool-card-screenshot-frame tool-card-screenshot-frame--live">
          <img
            src={src}
            alt={path || "Screenshot"}
            className="tool-card-screenshot-img"
            onError={() => setImgFailed(true)}
          />
          <div className="tool-card-screenshot-badge tool-card-screenshot-badge--ok">
            <span className="tool-card-screenshot-badge-dot" />
            Capture
          </div>
        </div>
      ) : (
        <div className="tool-card-screenshot-frame" aria-hidden>
          <Icons.Camera />
          <div className="tool-card-screenshot-badge">
            <span className="tool-card-screenshot-badge-dot" />
            {isError || imgFailed ? "Failed" : showResult ? "No image" : "Capture"}
          </div>
        </div>
      )}
      {showResult && path ? (
        <div className="tool-card-screenshot-path">
          <Icons.File />
          <span className="tool-card-screenshot-path-text" title={path}>
            {path}
          </span>
        </div>
      ) : null}
      {showResult && !src && !path && !isError ? (
        <div className="tool-execution-card-hint">
          Screenshot succeeded but no image preview was returned.
        </div>
      ) : null}
      {imgFailed ? (
        <div className="tool-execution-card-hint">
          Could not load screenshot preview. Path is still listed below if available.
        </div>
      ) : null}
      {hint ? <div className="tool-execution-card-hint">Hint: {hint}</div> : null}
    </div>
  );
}
