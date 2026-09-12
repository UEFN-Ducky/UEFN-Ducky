/** Pure Remote View helpers — kept off the React/Worker import graph so tests can run in Node. */

export type ContentBox = { left: number; top: number; width: number; height: number };

/** H.264 High > Main > Baseline (hardware paths on Windows), then AV1, VP9, VP8. */
export function rankVideoCodec(codec: { mimeType: string; sdpFmtpLine?: string }): number {
  const mime = codec.mimeType.toLowerCase();
  const fmtp = (codec.sdpFmtpLine || "").toLowerCase();
  if (mime.endsWith("/h264")) {
    const m = /profile-level-id=([0-9a-f]{2})/.exec(fmtp);
    const profile = m?.[1] ?? "";
    if (profile === "64") return 0;
    if (profile === "4d") return 1;
    return 2;
  }
  if (mime.endsWith("/av1")) return 3;
  if (mime.endsWith("/vp9")) return 4;
  if (mime.endsWith("/vp8")) return 5;
  return 8;
}

/** Where the video pixels actually sit inside an object-fit: contain element. */
export function contentRect(
  box: ContentBox,
  videoWidth: number,
  videoHeight: number,
): ContentBox {
  if (!videoWidth || !videoHeight || !box.width || !box.height) return box;
  const s = Math.min(box.width / videoWidth, box.height / videoHeight);
  const w = videoWidth * s;
  const h = videoHeight * s;
  return {
    left: box.left + (box.width - w) / 2,
    top: box.top + (box.height - h) / 2,
    width: w,
    height: h,
  };
}
