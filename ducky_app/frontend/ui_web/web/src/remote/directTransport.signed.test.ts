// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it } from "vitest";

import { NOT_YOUR_PC, parentBridge } from "./directTransport";
import { answerMessage, pinnedPc } from "./viewerKey";

// jsdom: window.parent is window, so this listener plays the site page relaying to the PC.
const SIG = { name: "ECDSA", hash: "SHA-256" } as const;
const b64 = (buf: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(buf)));
let pc: CryptoKeyPair;
let pcPub = "";
let tamper = false;
let offers: Record<string, unknown>[] = [];
let site: ((ev: MessageEvent) => void) | null = null;

beforeEach(async () => {
  localStorage.clear();
  tamper = false;
  offers = [];
  pc = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
  pcPub = b64(await crypto.subtle.exportKey("spki", pc.publicKey));
  site = (ev: MessageEvent) => {
    const d = ev.data as Record<string, unknown>;
    const reply = (msg: Record<string, unknown>) => window.postMessage(msg, "*");
    if (d.type === "ud-direct-call") {
      reply({ type: "ud-direct-call-result", id: d.id, ok: true, result: { pcPub, approved: true } });
    } else if (d.type === "ud-direct-offer") {
      offers.push(d);
      const offer = d.offer as { sdp: string };
      void (async () => {
        const answer = { type: "answer", sdp: "v=0 answer" };
        const msg = await answerMessage(String(d.session), offer.sdp, answer.sdp);
        const sig = b64(await crypto.subtle.sign(SIG, pc.privateKey, new TextEncoder().encode(msg)));
        reply({
          type: "ud-direct-answer", session: d.session, pcPub, pcSig: sig,
          answer: tamper ? { ...answer, sdp: "v=0 answer with the server's fingerprint" } : answer,
        });
      })();
    }
  };
  window.addEventListener("message", site);
});
afterEach(() => site && window.removeEventListener("message", site));

it("pairs, signs the offer, and takes an answer the pinned PC signed", async () => {
  const bridge = parentBridge();
  const out = await bridge.connect("s1", { type: "offer", sdp: "v=0 offer" }, []);
  expect(out.answer.sdp).toBe("v=0 answer");
  expect(pinnedPc("default")).toBe(pcPub);
  expect(offers[0]).toMatchObject({ viewer: expect.any(String), ts: expect.any(Number), sig: expect.any(String) });
});

it("refuses an answer the server changed", async () => {
  tamper = true;
  await expect(parentBridge().connect("s2", { type: "offer", sdp: "v=0 offer" }, [])).rejects.toThrow(NOT_YOUR_PC);
});
