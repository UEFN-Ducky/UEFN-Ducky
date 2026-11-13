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

  it("shows the download server apart from the site: the site can answer while downloads are cut", () => {
    // Nov 13 2026: plugins installed (the site serves them) while every update download failed.
    const lines = networkTestLines({
      ok: true, host: "uefnducky.org", proxy_mode: "none", proxy: "direct",
      dns: { ok: true, ms: 5, ipv4: 2, ipv6: 2 }, ipv4: { ok: true, ms: 18 }, ipv6: { ok: true, ms: 19 },
      https: { ok: true, status: 200, ms: 150 },
      download: {
        ok: false, host: "bucket.fly.storage.tigris.dev",
        ipv4: { ok: false, stage: "cut", error: "EOF occurred in violation of protocol" }, ipv6: { ok: null },
      },
      download_alt: { ok: true, host: "bucket.t3.storage.dev", ipv4: { ok: true, ms: 82 }, ipv6: { ok: null } },
    });
    expect(lines.slice(6)).toEqual([
      "Update downloads (bucket.fly.storage.tigris.dev): NOT reachable",
      "  IPv4: failed (EOF occurred in violation of protocol)",
      "  IPv6: not offered",
      "Second download server (bucket.t3.storage.dev): reachable",
      "  IPv4: ok (82 ms)",
      "  IPv6: not offered",
    ]);
  });
});
