import { describe, expect, it } from "vitest";
import { networkTestLines } from "./AppSection";

describe("networkTestLines", () => {
  it("names the hop that failed so a person can act on it", () => {
    const lines = networkTestLines({
      ok: false,
      host: "uefnducky.org",
      proxy_mode: "pac",
      proxy: "http://proxy.school:8080",
      dns: { ok: true, ms: 12, ipv4: 2, ipv6: 2 },
      ipv4: { ok: true, ms: 20 },
      ipv6: { ok: false, error: "timed out" },
      https: { ok: false, stage: "proxy", error: "The network's proxy refused" },
    });
    expect(lines).toEqual([
      "uefnducky.org: NOT reachable from UEFN Ducky",
      "DNS: ok (12 ms) · 2 IPv4, 2 IPv6",
      "IPv4: ok (20 ms)",
      "IPv6: failed (timed out)",
      "Proxy: pac → http://proxy.school:8080",
      "HTTPS: failed at proxy · The network's proxy refused",
    ]);
  });

  it("reads as reachable when HTTPS answered", () => {
    const lines = networkTestLines({
      ok: true, host: "uefnducky.org", proxy_mode: "none", proxy: "direct",
      dns: { ok: true, ms: 5, ipv4: 2, ipv6: 0 }, ipv4: { ok: true, ms: 18 }, ipv6: { ok: null },
      https: { ok: true, status: 200, ms: 150 },
    });
    expect(lines[0]).toBe("uefnducky.org: reachable from UEFN Ducky");
    expect(lines).toContain("IPv6: not offered");
    expect(lines).toContain("Proxy: none");
    expect(lines.at(-1)).toBe("HTTPS: ok (200, 150 ms)");
  });
});
