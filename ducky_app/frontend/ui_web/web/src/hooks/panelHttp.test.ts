// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("../remote/directTransport", () => ({ getDirectTransport: () => null, isRemoteDuckyWindow: () => false }));

import { callOverHttp, sendWithPicturesOverHttp } from "./panelHttp";
import { getApi } from "./usePanelApi";

const fetchMock = vi.fn();
const bridge = { get_listener_status: vi.fn(), get_skill_info: vi.fn(), list_folders: vi.fn() };

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  (window as unknown as { pywebview: unknown }).pywebview = { api: bridge };
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetAllMocks();
  delete (window as unknown as { pywebview?: unknown }).pywebview;
});

it("callOverHttp posts the args to /__panel_api and returns the result", async () => {
  fetchMock.mockResolvedValue(reply(200, { ok: true, result: { packs: 3 } }));
  await expect(callOverHttp("get_skill_info", ["a", 1])).resolves.toEqual({ ok: true, result: { packs: 3 } });
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("/__panel_api/get_skill_info");
  expect(JSON.parse(String(init.body))).toEqual({ args: ["a", 1] });
});

it("callOverHttp reports a 500 or a thrown method as a failure", async () => {
  fetchMock.mockResolvedValueOnce(reply(500, {}));
  await expect(callOverHttp("get_plan", [])).resolves.toEqual({ ok: false, timedOut: false });
  fetchMock.mockResolvedValueOnce(reply(200, { ok: false, error: "boom" }));
  await expect(callOverHttp("get_plan", [])).resolves.toEqual({ ok: false, timedOut: false });
});

it("a routed method through getApi() goes over HTTP, and to the bridge on a 500", async () => {
  // jsdom serves the page over http://, like the desktop window's local server.
  const api = getApi()!;
  expect(getApi()).toBe(api);
  fetchMock.mockResolvedValueOnce(reply(200, { ok: true, result: "over-http" }));
  await expect(api.get_skill_info()).resolves.toBe("over-http");
  expect(bridge.get_skill_info).not.toHaveBeenCalled();

  fetchMock.mockResolvedValueOnce(reply(500, {}));
  bridge.get_skill_info.mockResolvedValue("over-bridge");
  await expect(api.get_skill_info()).resolves.toBe("over-bridge");
});

it("a picture send goes over HTTP and retries a timeout once under the same request id", async () => {
  vi.useFakeTimers();
  // First try hangs until its timeout aborts it; the retry answers.
  fetchMock.mockImplementationOnce((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
  }));
  fetchMock.mockResolvedValueOnce(reply(200, { ok: true, result: { run_id: "r1" } }));
  const sent = sendWithPicturesOverHttp(["c1", "look"]);
  await vi.advanceTimersByTimeAsync(45_000);
  await expect(sent).resolves.toEqual({ ok: true, result: { run_id: "r1" } });
  vi.useRealTimers();
  const headers = fetchMock.mock.calls.map(([, init]) => (init as RequestInit).headers as Record<string, string>);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls[0][0]).toBe("/__panel_api/send_message");
  expect(headers[0]["X-Ducky-Request-Id"]).toMatch(/^send-/);
  expect(headers[1]["X-Ducky-Request-Id"]).toBe(headers[0]["X-Ducky-Request-Id"]);
});

it("a method that is not routed stays on the bridge", async () => {
  bridge.list_folders.mockResolvedValue(["f"]);
  await expect(getApi()!.list_folders()).resolves.toEqual(["f"]);
  expect(fetchMock).not.toHaveBeenCalled();
});
