import { useMemo, useState } from "react";
import { Icons } from "../../../icons/Icons";
import { assetUrl } from "../../../remote/assetBase";
import type { ToolCardBodyProps } from "../toolCardTypes";

export function asScreenshotRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function parseScreenshotResult(raw: string): Record<string, unknown> | null {
  // Claude Code appends "[Image: source: …]" lines after the JSON text block.
  const trimmed = raw.trim().replace(/(?:\s*\[Image: [^\]\n]*\])+$/, "").trim();
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
      if (Array.isArray(parsed)) {
        let text = "";
        for (const block of parsed) {
          const row = asScreenshotRecord(block);
          const candidate = row && row.type === "text" && typeof row.text === "string" ? row.text.trim() : "";
          if (candidate.startsWith("{") || candidate.startsWith("[")) {
            text = candidate;
            break;
          }
        }
        if (!text) return null;
        parsed = text;
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
    if (typeof v !== "string") continue;
    const url = v.trim();
    if (/^https?:\/\//i.test(url) || /\/chat-attachments\//i.test(url)) return url;
  }
  return "";
}

/** Chat-folder PNG path → panel URL the card can load. */
export function screenshotSrcFromPath(path: string): string {
  const norm = path.replace(/\\/g, "/");
  const match = /\/conversations\/([^/]+)\/attachments\/([^/]+\.(?:png|jpe?g|webp))$/i.exec(norm);
  if (!match) return "";
  return `/chat-attachments/${match[1]}/${match[2]}`;
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
  // Only a path the tool returned — the filename argument is a request, not a saved file.
  const fromPath = showResult && !mediaUrl && !base64 ? screenshotSrcFromPath(pickScreenshotPath(data, {})) : "";
  const captureError =
    showResult && typeof data?.error === "string" && data.error.trim() && !mediaUrl && !fromPath
      ? data.error.trim()
      : "";
  const mime = pickMime(data);
  const src = useMemo(() => {
    if (mediaUrl) return mediaUrl;
    if (fromPath) return assetUrl(fromPath); // inside the phone panel's worker scope in Remote View
    if (base64) return `data:${mime};base64,${base64}`;
    return "";
  }, [mediaUrl, fromPath, base64, mime]);
  const [imgFailed, setImgFailed] = useState(false);

  const showImage = !!src && !imgFailed && !isError && !captureError;

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
            {isError || imgFailed || captureError ? "Failed" : showResult ? "No image" : "Capture"}
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
      {captureError ? <div className="tool-execution-card-hint">{captureError}</div> : null}
      {imgFailed ? (
        <div className="tool-execution-card-hint">
          Could not load screenshot preview. Path is still listed below if available.
        </div>
      ) : null}
      {hint ? <div className="tool-execution-card-hint">Hint: {hint}</div> : null}
    </div>
  );
}
