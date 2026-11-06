// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import source from "./startupRefresh.ts?raw";
import { AGENT_EVENT, installStartupRefresh, PANEL_PUSH, STARTUP_REFRESH_EVENT } from "./startupRefresh";
import { RELOAD_BATCH_MS, useChatsChanged } from "./useChatsChanged";
import { installPanelPushBus, subscribePanelPush } from "./usePanelPushBus";

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => undefined)));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function fire(stale: unknown) {
  window.dispatchEvent(new CustomEvent(STARTUP_REFRESH_EVENT, { detail: { stale } }));
}

it("re-runs the chat list loader for a stale chat getter, in place", async () => {
  installStartupRefresh();
  const load = vi.fn(() => Promise.resolve());
  const { unmount } = renderHook(() => useChatsChanged(load));
  fire(["list_all_conversations", "list_folders"]);
  await vi.advanceTimersByTimeAsync(RELOAD_BATCH_MS + 300);
  expect(load).toHaveBeenCalledTimes(1);
  unmount();
});

it("sends each existing panel update once and skips names with no loader", () => {
  installStartupRefresh();
  installPanelPushBus();
  const seen: string[] = [];
  const stop = subscribePanelPush((e) => void seen.push(String(e.type)));
  fire(["get_models_catalog", "has_any_api_key", "get_appearance", "get_header_visibility", 7]);
  stop();
  expect(seen.sort()).toEqual(["appearance_changed", "models_updated"]);
});

it("maps no getter whose start-up callers lack an in-place refresh", () => {
  for (const name of ["get_settings", "get_header_visibility", "get_workspace_dock", "get_key_status",
    "list_agent_profiles", "list_ducky_catalog", "list_recent_projects", "list_known_projects"]) {
    expect(PANEL_PUSH[name] ?? AGENT_EVENT[name], name).toBeUndefined();
  }
});

it("never reloads or navigates the page", () => {
  expect(source).not.toMatch(/reload\(|location\.(href|assign|replace)/);
});
