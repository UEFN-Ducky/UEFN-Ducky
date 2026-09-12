/**
 * Remote View direct protocol — shared by the phone (viewer) and the desktop
 * WebView2 page. See docs/remote-direct-protocol.md. Pure helpers only; no DOM.
 */

export const PROTOCOL_VERSION = 1;

/** Safari drops DataChannel messages over 64 KiB; keep headroom for framing. */
export const MAX_CHUNK_BYTES = 16 * 1024;
export const BLOB_LOW_WATER_BYTES = 64 * 1024;

export const CHANNEL_RPC = "rpc";
export const CHANNEL_EVENTS = "events";
export const CHANNEL_BLOB = "blob";
export const CHANNEL_INPUT = "input";
export const STREAM_PREFIX = "stream:";

/** Paths the desktop will serve over the blob channel. Anything else → 403. */
export const BLOB_PREFIXES = [
  "/plugin-ui/",
  "/user-sounds/",
  "/tool-captures/",
  "/duckies/custom/",
  "/model-files/",
  "/duckies/",
] as const;

export const DEFAULT_ICE: RTCIceServer[] = [
  { urls: "stun:stun.cloudflare.com:3478" },
  { urls: "stun:stun.l.google.com:19302" },
];

export type RpcRequest = { id: number; method: string; args: unknown[] };
export type RpcReply =
  | { id: number; ok: true; result: unknown }
  | { id: number; ok: false; error: string }
  | { id: number; ok: boolean; part: number; of: number; data: string };

export type EventFrame = { seq: number; event: Record<string, unknown> };

export type BlobRequest = { id: number; get: string; etag?: string };
export type BlobHeader = { id: number; status: number; type?: string; etag?: string; size?: number };
export type BlobDone = { id: number; done: true };

export type SessionDescriptionJson = { type: "offer" | "answer"; sdp: string };

export type StreamOpen = { kind: "terminal" | "lsp"; url: string };

export function blobPathAllowed(path: string): boolean {
  const p = path.startsWith("/") ? path : `/${path}`;
  if (p.includes("..")) return false;
  return BLOB_PREFIXES.some((prefix) => p.startsWith(prefix));
}

/** Split a JSON string into ≤ MAX_CHUNK_BYTES UTF-16-safe slices. */
export function splitText(text: string, max = MAX_CHUNK_BYTES): string[] {
  if (text.length <= max) return [text];
  const out: string[] = [];
  let i = 0;
  while (i < text.length) {
    let end = Math.min(text.length, i + max);
    // Never split a surrogate pair.
    const code = text.charCodeAt(end - 1);
    if (end < text.length && code >= 0xd800 && code <= 0xdbff) end -= 1;
    out.push(text.slice(i, end));
    i = end;
  }
  return out;
}

/** Reassemble `part` replies; returns the full text once all parts arrived. */
export class PartAssembler {
  private parts = new Map<number, { of: number; got: Map<number, string> }>();

  push(id: number, part: number, of: number, data: string): string | null {
    let entry = this.parts.get(id);
    if (!entry) {
      entry = { of, got: new Map() };
      this.parts.set(id, entry);
    }
    entry.got.set(part, data);
    if (entry.got.size < entry.of) return null;
    const pieces: string[] = [];
    for (let i = 0; i < entry.of; i += 1) pieces.push(entry.got.get(i) ?? "");
    this.parts.delete(id);
    return pieces.join("");
  }

  drop(id: number): void {
    this.parts.delete(id);
  }
}

/**
 * Rewrite absolute loopback URLs the desktop hands out (`http://127.0.0.1:4199/x`)
 * to same-origin paths so the viewer's Service Worker can serve them over the
 * blob channel. Walks arrays and plain objects; leaves everything else alone.
 */
export function rewriteLoopbackUrls<T>(value: T, loopbackOrigins: readonly string[]): T {
  const visit = (v: unknown): unknown => {
    if (typeof v === "string") {
      for (const origin of loopbackOrigins) {
        if (v.startsWith(origin + "/")) return v.slice(origin.length);
      }
      return v;
    }
    if (Array.isArray(v)) return v.map(visit);
    if (v && typeof v === "object" && Object.getPrototypeOf(v) === Object.prototype) {
      const out: Record<string, unknown> = {};
      for (const [k, inner] of Object.entries(v as Record<string, unknown>)) out[k] = visit(inner);
      return out;
    }
    return v;
  };
  return visit(value) as T;
}

/** Fingerprint line from an SDP (`a=fingerprint:sha-256 AB:CD…`), or "". */
export function sdpFingerprint(sdp: string): string {
  const m = /a=fingerprint:\s*([a-z0-9-]+)\s+([0-9A-Fa-f:]+)/i.exec(sdp);
  return m ? `${m[1].toLowerCase()} ${m[2].toUpperCase()}` : "";
}

export function newSessionId(): string {
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Compare desktop and viewer versions: -1 desktop older, 0 equal, 1 desktop newer. */
export function compareVersions(desktop: string, viewer: string): number {
  const a = desktop.split(".").map((n) => parseInt(n, 10) || 0);
  const b = viewer.split(".").map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}
