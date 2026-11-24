/**
 * This phone or browser's identity for Remote Ducky, and the PC keys it trusts.
 * Matches frontend/remote_trust.py: P-256 ECDSA, raw r||s signatures, SPKI public keys,
 * the same 6-char code (SAS) and the same signed messages. The private key is
 * non-extractable: even this page's own code can only sign with it.
 */

const DB = "ducky-remote";
const STORE = "keys";
const PINNED = "ud-pinned-pcs";
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const SIG = { name: "ECDSA", hash: "SHA-256" } as const;
export const PAIR_POLL_MS = 2_000;
export const PAIR_WAIT_MS = 10 * 60_000;

export class PairError extends Error {}

const enc = new TextEncoder();
const b64 = (buf: ArrayBuffer | Uint8Array) => btoa(String.fromCharCode(...new Uint8Array(buf)));
const unb64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const hex = (buf: ArrayBuffer) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
const sha256 = (data: BufferSource) => crypto.subtle.digest("SHA-256", data);

function idb<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(DB, 1);
    open.onupgradeneeded = () => open.result.createObjectStore(STORE);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const req = run(open.result.transaction(STORE, mode).objectStore(STORE));
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    };
  });
}

let keyPromise: Promise<CryptoKeyPair> | null = null;

/** This device's key pair, made once and kept in IndexedDB (cleared site data = pair again). */
export function viewerKey(): Promise<CryptoKeyPair> {
  keyPromise ??= (async () => {
    const saved = await idb<CryptoKeyPair | undefined>("readonly", (s) => s.get("viewer")).catch(() => undefined);
    if (saved?.privateKey) return saved;
    const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
    // ponytail: storage blocked (private mode): a key for this page only, so it pairs each visit.
    await idb("readwrite", (s) => s.put(pair, "viewer")).catch(() => undefined);
    return pair;
  })();
  return keyPromise;
}

export async function viewerPub(): Promise<string> {
  return b64(await crypto.subtle.exportKey("spki", (await viewerKey()).publicKey));
}

/** remote_trust.pub_id: the first 16 hex of sha256(SPKI). */
export async function keyId(pubB64: string): Promise<string> {
  return hex(await sha256(unb64(pubB64))).slice(0, 16);
}

/** remote_trust.sas: 6 Crockford chars from sha256(pc SPKI || viewer SPKI). Computed here,
 * never taken from the server: a swapped key shows a different code on each screen. */
export async function sas(pcPub: string, viewerPubB64: string): Promise<string> {
  const a = unb64(pcPub), b = unb64(viewerPubB64);
  const joined = new Uint8Array(a.length + b.length);
  joined.set(a);
  joined.set(b, a.length);
  const d = new Uint8Array(await sha256(joined));
  const bits = (((d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]) >>> 0) >>> 2;
  return Array.from({ length: 6 }, (_, i) => CROCKFORD[(bits >>> (25 - 5 * i)) & 31]).join("");
}

const h = async (text: string) => hex(await sha256(enc.encode(text)));
export const offerMessage = async (session: string, ts: number, offerSdp: string) =>
  `rtc1|${session}|${ts}|${await h(offerSdp)}`;
export const answerMessage = async (session: string, offerSdp: string, answerSdp: string) =>
  `rtc1a|${session}|${await h(offerSdp)}|${await h(answerSdp)}`;

/** The fields rtc_connect needs from a paired device: who, when, and its signature. */
export async function signOffer(session: string, offerSdp: string, now = Date.now()) {
  const ts = Math.floor(now / 1000);
  const pair = await viewerKey();
  const sig = await crypto.subtle.sign(SIG, pair.privateKey, enc.encode(await offerMessage(session, ts, offerSdp)));
  return { viewer: await keyId(await viewerPub()), ts, sig: b64(sig) };
}

/** True only when the pinned PC key signed this exact answer to this exact offer. */
export async function verifyAnswer(
  pcPub: string, session: string, offerSdp: string, answerSdp: string, sigB64: string,
): Promise<boolean> {
  try {
    const sig = unb64(sigB64);
    if (sig.length !== 64) return false;
    const key = await crypto.subtle.importKey("spki", unb64(pcPub), { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    return await crypto.subtle.verify(SIG, key, sig, enc.encode(await answerMessage(session, offerSdp, answerSdp)));
  } catch {
    return false;
  }
}

function pins(): Record<string, string> {
  try {
    const rows = JSON.parse(localStorage.getItem(PINNED) || "{}");
    return rows && typeof rows === "object" ? rows : {};
  } catch {
    return {};
  }
}

export function pinnedPc(pc: string): string {
  return String(pins()[pc] || "");
}

/** Pin a PC key; a different key for a PC already pinned is a hard stop. */
export function pinPc(pc: string, pcPub: string): void {
  const rows = pins();
  if (rows[pc] && rows[pc] !== pcPub) throw new PairError("This PC's key changed. Remove this device on your PC and pair it again.");
  rows[pc] = pcPub;
  localStorage.setItem(PINNED, JSON.stringify(rows));
}

export function forgetPc(pc: string): void {
  const rows = pins();
  delete rows[pc];
  localStorage.setItem(PINNED, JSON.stringify(rows));
}

type Call = (method: "pair_request" | "pair_status", args: Record<string, unknown>) => Promise<Record<string, unknown>>;

/**
 * Make sure this device is approved on PC `pc` and its key is pinned. `onCode` shows the
 * code (computed here) while the person approves on the PC; returns the pinned PC key.
 */
export async function ensurePaired(
  pc: string, call: Call, onCode: (code: string) => void, label = deviceLabel(),
  wait = { pollMs: PAIR_POLL_MS, maxMs: PAIR_WAIT_MS },
): Promise<string> {
  const pub = await viewerPub();
  const first = await call("pair_request", { pub, label });
  const pcPub = String(first.pcPub || "");
  if (!pcPub) throw new PairError(String(first.error || "Your PC didn't answer the pairing request."));
  const pinned = pinnedPc(pc);
  if (pinned && pinned !== pcPub) throw new PairError("This PC's key changed. Remove this device on your PC and pair it again.");
  if (first.approved) {
    pinPc(pc, pcPub);
    return pcPub;
  }
  onCode(await sas(pcPub, pub));
  for (let waited = 0; waited < wait.maxMs; waited += wait.pollMs) {
    await new Promise((r) => setTimeout(r, wait.pollMs));
    const st = await call("pair_status", { pub });
    if (st.approved) {
      pinPc(pc, pcPub);
      return pcPub;
    }
    if (!st.pending) throw new PairError("Your PC denied this device, or the request expired.");
  }
  throw new PairError("No answer from your PC. Try again.");
}

export function deviceLabel(ua = typeof navigator === "undefined" ? "" : navigator.userAgent): string {
  if (ua.includes("UEFNDuckyAndroid/")) return "UEFN Ducky app (Android)";
  if (/iPhone|iPad/.test(ua)) return "iPhone or iPad browser";
  if (/Android/.test(ua)) return "Android browser";
  if (/Windows/.test(ua)) return "Windows browser";
  if (/Mac OS/.test(ua)) return "Mac browser";
  if (/Linux/.test(ua)) return "Linux browser";
  return "Browser";
}
