/**
 * Answers ui_rpc_request events from the guided-UI MCP tools.
 *
 * A tool posts a request to the panel (navigate / list_targets / walkthrough_run / ask_user);
 * the panel pushes it here as a `ui_rpc_request` event. We do the work and reply
 * via `PanelApi.ui_rpc_respond`, which unblocks the tool waiting over loopback.
 */
import { useEffect } from "react";
import type { AgentEvent } from "../types/panel";
import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { getApi } from "../hooks/usePanelApi";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import { listTargets } from "./registry";
import { runAskUser } from "../ask-user";
import { runAgentWalkthrough } from "../walkthrough/agentWalkthrough";

/** settings.* route → Settings tab label. */
const SETTINGS_TAB: Record<string, string> = {
  settings: "General",
  "settings.general": "General",
  "settings.store": "Store",
  "settings.llms": "LLMs",
  "settings.mcp": "LLMs",
  "settings.mcp_plugins": "LLMs",
  "settings.skills": "LLMs",
  "settings.appearance": "Appearance",
  "settings.duckies": "Duckies",
  "settings.plans": "Plans",
  "settings.memory": "LLMs",
  "settings.languages": "Languages",
  "settings.log_errors": "General",
  plans: "Plans",
};

/** Sub-section for tabs that have inner tabs (LLMs, General → Log & Errors). */
const SETTINGS_SECTION: Record<string, string> = {
  "settings.general": "general",
  "settings.llms": "llms",
  "settings.mcp": "mcps",
  "settings.mcp_plugins": "mcps",
  "settings.skills": "skills",
  "settings.plans": "working",
  plans: "working",
  "settings.memory": "entries",
  "settings.log_errors": "errors",
};

type RpcResult = Record<string, unknown>;

function handleNavigate(params: Record<string, unknown>): RpcResult {
  const route = String(params.route ?? "").trim();
  const itemId = String(params.item_id ?? "").trim();
  const tab = SETTINGS_TAB[route];
  if (tab) {
    requestOpenSettings(tab);
    const section = SETTINGS_SECTION[route];
    if (section) {
      window.dispatchEvent(
        new CustomEvent("ducky:settings-section", { detail: { tab, section } }),
      );
    }
    return { ok: true, route, tab };
  }
  window.dispatchEvent(new CustomEvent("ducky:navigate", { detail: { route, item_id: itemId } }));
  return { ok: true, route, dispatched: true };
}

async function dispatch(method: string, params: Record<string, unknown>): Promise<RpcResult> {
  try {
    if (method === "navigate") return handleNavigate(params);
    if (method === "list_targets") return { targets: listTargets(String(params.route ?? "")) };
    if (method === "walkthrough_run") {
      return await runAgentWalkthrough(params.steps);
    }
    if (method === "ask_user") {
      return await runAskUser(
        params.questions,
        String(params.title ?? ""),
        String(params.conv_id ?? ""),
      );
    }
    return { error: `unknown method: ${method}` };
  } catch (err) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

export function UiRpcBridge() {
  useEffect(() => {
    installAgentEventBus();
    const handler = (event: AgentEvent) => {
      if (event.type !== "ui_rpc_request") return;
      const requestId = event.request_id ?? "";
      if (!requestId) return;
      const method = event.method ?? "";
      const params = (event.params ?? {}) as Record<string, unknown>;
      void dispatch(method, params).then((result) => {
        void getApi()?.ui_rpc_respond(requestId, result);
      });
    };
    return subscribeAgentEvents(handler);
  }, []);
  return null;
}
