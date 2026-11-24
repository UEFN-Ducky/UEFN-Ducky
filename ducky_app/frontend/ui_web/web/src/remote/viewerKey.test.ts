// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";

import { ensurePaired, keyId, offerMessage, pinPc, pinnedPc, sas, signOffer, verifyAnswer, viewerPub } from "./viewerKey";

// Made by frontend/remote_trust.py (sas, pub_id, sign(answer_message("s1", "v=0 offer", "v=0 answer"))).
const PY = {
  pcPub: "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEket3R5z416G/uDUhVTl5ykxihQnq1MtGqDlEYI3680lTmOzNqnzjgzlxGuLbjl2/nzafYAR9IMlGC80cPBsPkQ==",
  viewerPub: "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAETXd6Ffz74jOeXvtLJCL2M5JH7i6V/8I25619tSXhJa8QWR/+I0l3MaP+ZqorVD+HKljtm+ds+4FXTwpmNTQt9Q==",
  sas: "8SZVHG",
  viewerId: "b65c88df1d733dea",
  answerSig: "vPt7YjVAPL/HBpI69f/O7msqgUX3gT23cXzgewFiHgSyYhfTYADPxQvLpKsAjeiSzGq5ZzbDVwcgcXiWe09oEw==",
};

beforeEach(() => localStorage.clear());

it("the code, key id and offer message match the PC's Python exactly", async () => {
  expect(await sas(PY.pcPub, PY.viewerPub)).toBe(PY.sas);
  expect(await keyId(PY.viewerPub)).toBe(PY.viewerId);
  expect(await offerMessage("s1", 1700000000, "v=0 offer")).toBe(
    "rtc1|s1|1700000000|7e1ffcaf6d9eae23fbd83c8f17ac0cba50e740fe2305a7cff97c77ee014164d0",
  );
});

it("accepts the PC's real answer signature and refuses a tampered answer", async () => {
  expect(await verifyAnswer(PY.pcPub, "s1", "v=0 offer", "v=0 answer", PY.answerSig)).toBe(true);
  expect(await verifyAnswer(PY.pcPub, "s1", "v=0 offer", "v=0 answer evil", PY.answerSig)).toBe(false);
  expect(await verifyAnswer(PY.viewerPub, "s1", "v=0 offer", "v=0 answer", PY.answerSig)).toBe(false);
  expect(await verifyAnswer(PY.pcPub, "s1", "v=0 offer", "v=0 answer", "AAAA")).toBe(false);
});

it("signs offers with a key that verifies, and names itself by its key id", async () => {
  const signed = await signOffer("s9", "v=0 o", 1700000000_000);
  expect(signed.ts).toBe(1700000000);
  expect(signed.viewer).toBe(await keyId(await viewerPub()));
  expect(atob(signed.sig).length).toBe(64);
});

it("a different key for a pinned PC is a hard stop", () => {
  pinPc("pc1", PY.pcPub);
  expect(() => pinPc("pc1", PY.viewerPub)).toThrow(/key changed/);
  expect(pinnedPc("pc1")).toBe(PY.pcPub);
});

it("pairing shows the code computed here, waits for approval, then pins", async () => {
  const pub = await viewerPub();
  const call = vi.fn(async (method: string) =>
    method === "pair_request" ? { pcPub: PY.pcPub, code: "LIEDXX", pending: true }
      : call.mock.calls.length < 3 ? { pending: true } : { approved: true });
  const shown: string[] = [];
  const got = await ensurePaired("pc1", call, (c) => shown.push(c), "Phone", { pollMs: 1, maxMs: 1000 });
  expect(got).toBe(PY.pcPub);
  expect(shown).toEqual([await sas(PY.pcPub, pub)]); // never the server's "LIEDXX"
  expect(pinnedPc("pc1")).toBe(PY.pcPub);
});

it("a denied or expired request stops pairing", async () => {
  const call = vi.fn(async (method: string) => (method === "pair_request" ? { pcPub: PY.pcPub, pending: true } : {}));
  await expect(ensurePaired("pc2", call, () => {}, "Phone", { pollMs: 1, maxMs: 1000 })).rejects.toThrow(/denied/);
  expect(pinnedPc("pc2")).toBe("");
});

it("a server that swaps the PC key on a pinned PC is refused", async () => {
  pinPc("pc3", PY.pcPub);
  const call = vi.fn(async () => ({ pcPub: PY.viewerPub, approved: true }));
  await expect(ensurePaired("pc3", call, () => {})).rejects.toThrow(/key changed/);
});
