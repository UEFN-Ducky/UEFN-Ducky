import { afterEach, describe, expect, it, vi } from "vitest";
import { getApi, isRemoteNetworkBlip, remoteInflightKey } from "./usePanelApi";

describe("isRemoteNetworkBlip", () => {
  it("treats Failed to fetch and abort as tunnel blips", () => {
    expect(isRemoteNetworkBlip(new TypeError("Failed to fetch"))).toBe(true);
    expect(isRemoteNetworkBlip(new DOMException("aborted", "AbortError"))).toBe(true);
    expect(isRemoteNetworkBlip(new Error("HTTP 500"))).toBe(false);
  });
});

describe("remoteInflightKey", () => {
  it("is stable for the same method and args", () => {
    expect(remoteInflightKey("has_any_api_key", [])).toBe(remoteInflightKey("has_any_api_key", []));
    expect(remoteInflightKey("get_session_files", ["a"])).not.toBe(remoteInflightKey("get_session_files", ["b"]));
  });
});

describe("remoteApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shares one fetch for identical concurrent calls", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        calls += 1;
        return new Promise<Response>((resolve) => {
          setTimeout(() => {
            resolve(new Response(JSON.stringify({ ok: true, result: true }), { status: 200 }));
          }, 20);
        });
      }),
    );
    const api = getApi();
    expect(api).toBeTruthy();
    const [a, b] = await Promise.all([api!.has_any_api_key(), api!.has_any_api_key()]);
    expect(calls).toBe(1);
    expect(a).toBe(true);
    expect(b).toBe(true);
  });

  it("returns undefined on Failed to fetch instead of throwing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    await expect(getApi()!.has_any_api_key()).resolves.toBeUndefined();
  });
});
