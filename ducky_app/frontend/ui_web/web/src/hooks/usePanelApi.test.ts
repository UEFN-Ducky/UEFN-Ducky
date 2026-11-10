// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { getApi, isRemote, isRemoteNetworkBlip, remoteInflightKey } from "./usePanelApi";

describe("isRemote", () => {
  afterEach(() => {
    const w = window as unknown as { chrome?: unknown; pywebview?: unknown };
    delete w.chrome;
    delete w.pywebview;
  });

  it("knows the desktop before pywebview has injected its bridge", () => {
    const w = window as unknown as { chrome?: unknown; pywebview?: unknown };
    expect(isRemote()).toBe(true); // a plain browser: phone, tunnel, panel host
    w.chrome = { webview: {} }; // WebView2, bridge not in yet
    expect(isRemote()).toBe(false);
    w.pywebview = { api: {} };
    expect(isRemote()).toBe(false);
  });
});

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

describe("desktop bridge replies over 1 MB", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete (window as unknown as { pywebview?: unknown }).pywebview;
  });

  it("fetches the parked result once instead of failing (any method, plugin_call too)", async () => {
    const bridgeCall = vi.fn(() => Promise.reject(new Error("large result:tok123 (message over 1048576 bytes)")));
    (window as unknown as { pywebview: unknown }).pywebview = {
      api: { get_listener_status: vi.fn(), plugin_call: bridgeCall, other: () => Promise.reject(new Error("boom")) },
    };
    const fetch = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ ok: true, result: { roads: 4 } }), { status: 200 })));
    vi.stubGlobal("fetch", fetch);
    const api = getApi() as unknown as { plugin_call: () => Promise<unknown>; other: () => Promise<unknown> };
    await expect(api.plugin_call()).resolves.toEqual({ roads: 4 });
    expect(fetch).toHaveBeenCalledWith("/__panel_large_result/tok123", expect.objectContaining({ method: "POST" }));
    await expect(api.other()).rejects.toThrow("boom");
    expect(fetch).toHaveBeenCalledTimes(1);
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

  it("opens https on this device instead of posting to the PC", async () => {
    const open = vi.fn();
    const fetch = vi.fn();
    vi.stubGlobal("open", open);
    vi.stubGlobal("fetch", fetch);
    await getApi()!.open_external_url("https://example.com/docs");
    expect(open).toHaveBeenCalledWith("https://example.com/docs", "_blank", "noopener,noreferrer");
    expect(fetch).not.toHaveBeenCalled();
  });
});
