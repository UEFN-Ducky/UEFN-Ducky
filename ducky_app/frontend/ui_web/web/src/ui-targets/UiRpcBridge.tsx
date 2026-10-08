/**
 * Answers ui_rpc_request events from the guided-UI MCP tools.
 *
 * A tool posts a request to the panel (navigate / list_targets / show / walkthrough_run /
 * tour_workflow / ask_user); the panel pushes it here as a `ui_rpc_request` event. We do
 * the work and reply via `PanelApi.ui_rpc_respond`, which unblocks the tool waiting over
 * loopback.
 *
 * With several windows open (popped-out windows, the phone panel), the one the user last
 * clicked or typed in claims "active"; requests that show something carry `_for_client`
 * and only that window takes them (and acknowledges, so the panel knows it's alive). A
 * request for no window in particular is claimed: the first window to claim it runs it.
 */
import { useEffect } from "react";
import type { AgentEvent, MessageAuthorDto } from "../types/panel";
import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { onApiReady } from "../hooks/onApiReady";
import { hasPluginUiTabOpener, requestOpenPluginUiTab } from "../plugin-ui/openPluginUiTab";
import { openPanelRoute } from "../navigation/openPanelRoute";
import { parseShowMeRequest, playShowMe, whenShowMeClosed } from "../showme/ShowMeService";
import { listTargets } from "./registry";
import { listUiActions, runUiAction, searchTargets, waitForUiAction } from "./resolve";
import { installEditorLineTargets } from "./editorLineTarget";
import { asGuidedUi } from "./guidedBusy";
import { getCurrentWorkflow, aiTourSteps, buildWorkflowTour, withOpenStep } from "../automations/workflowTour";
import { dropAnsweredAskUser, runAskUser } from "../ask-user";
import { runAgentWalkthrough } from "../walkthrough/agentWalkthrough";

type RpcResult = Record<string, unknown>;

/** This window, for requests meant for the window in use. */
export const UI_CLIENT_ID = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
const WINDOW_METHODS = new Set(["show", "walkthrough_run", "tour_workflow", "navigate", "list_targets"]);

/** Settings → "Let Ducky show me things": off = Ducky's Show me only leaves its chat button. */
async function showMeAutoplay(): Promise<boolean> {
  try {
    const settings = (await getApi()?.get_settings?.()) as { show_me_autoplay?: boolean } | undefined;
    return settings?.show_me_autoplay !== false;
  } catch {
    return true;
  }
}

function handleNavigate(params: Record<string, unknown>): RpcResult {
  return openPanelRoute(String(params.route ?? ""), String(params.item_id ?? ""));
}

async function handleShow(params: Record<string, unknown>): Promise<RpcResult> {
  const request = parseShowMeRequest(params);
  if (!request) return { error: "show needs a target and a title or body" };
  if (!(await showMeAutoplay())) {
    return { ok: true, shown: false, missing: false, deferred: true, note: "The user plays Show me from its chat button (Settings: Let Ducky show me things is off)." };
  }
  const result = await playShowMe(request);
  // Not waiting, or replaced before it showed: answer now. Else wait for the close button.
  if (!params.wait || (!result.shown && !result.missing)) return { ...result };
  await whenShowMeClosed();
  return { ...result, closed: true };
}

async function handleTourWorkflow(params: Record<string, unknown>): Promise<RpcResult> {
  const workflowId = String(params.workflow_id ?? "").trim();
  if (!workflowId) return { error: "workflow_id required" };
  openPanelRoute("workflows");
  if (!(await waitForUiAction("workflows.open"))) return { error: "the Workflows editor didn't open" };
  const opened = await runUiAction("workflows.open", { id: workflowId });
  const current = getCurrentWorkflow();
  if (!opened.ok || current?.id !== workflowId) return { error: opened.error || "couldn't open that workflow" };
  const steps = params.auto ? buildWorkflowTour(current.graph, current.byType) : aiTourSteps(params.steps);
  if (!steps.length) return { error: "no steps to show" };
  const tour = withOpenStep(steps, workflowId);
  const out = await runAgentWalkthrough(tour);
  return { ...out, steps: tour };
}

function handleListTargets(params: Record<string, unknown>): RpcResult {
  const route = String(params.route ?? "");
  const query = String(params.query ?? "");
  const visibleOnly = params.visible_only === true;
  const targets = query || visibleOnly ? searchTargets(route, query, visibleOnly) : listTargets(route);
  return { targets, actions: listUiActions(route) };
}

async function dispatch(method: string, params: Record<string, unknown>, requestId = ""): Promise<RpcResult> {
  try {
    if (method === "navigate") return handleNavigate(params);
    if (method === "list_targets") return handleListTargets(params);
    if (method === "show") return await asGuidedUi(() => handleShow(params));
    if (method === "tour_workflow") return await asGuidedUi(() => handleTourWorkflow(params));
    if (method === "walkthrough_run") {
      return await asGuidedUi(() => runAgentWalkthrough(params.steps));
    }
    if (method === "open_plugin_panel") {
      // ducky_plugin_test opens each panel of the plugin it tests.
      const pluginId = String(params.plugin_id ?? "");
      const panelId = String(params.panel_id ?? "");
      if (!pluginId || !panelId) return { error: "plugin_id and panel_id required" };
      requestOpenPluginUiTab(pluginId, panelId, String(params.title ?? "") || undefined);
      return { ok: true, tab: `plugin:${pluginId}:${panelId}` };
    }
    if (method === "ask_user") {
      const rawIds = params.group_ids;
      const groupIds = Array.isArray(rawIds) ? rawIds.map((id) => String(id)) : [];
      const rawAuthor = params.author;
      const author =
        rawAuthor && typeof rawAuthor === "object" && !Array.isArray(rawAuthor)
          ? (rawAuthor as MessageAuthorDto)
          : undefined;
      return await runAskUser(
        params.questions,
        String(params.title ?? ""),
        String(params.conv_id ?? ""),
        { groupIds, author, requestId },
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
    const seen = new Set<string>();
    const handler = async (event: AgentEvent) => {
      // Answered in another window or on the phone: close it here too.
      if (event.type === "ui_rpc_settled") {
        seen.add(String(event.request_id ?? ""));
        dropAnsweredAskUser(String(event.request_id ?? ""));
        return;
      }
      if (event.type !== "ui_rpc_request") return;
      const requestId = event.request_id ?? "";
      if (!requestId) return;
      const method = event.method ?? "";
      const params = (event.params ?? {}) as Record<string, unknown>;
      if (method === "ask_user") {
        if (seen.has(requestId)) return;
        seen.add(requestId);
      }
      // Only the desktop window that opens plugin tabs answers (not popped-out windows or the phone).
      if (method === "open_plugin_panel" && (!hasPluginUiTabOpener() || isRemote())) return;
      if (WINDOW_METHODS.has(method)) {
        const forClient = String(params._for_client ?? "");
        if (forClient && forClient !== UI_CLIENT_ID) return;  // meant for the window in use
        if (forClient) {
          void getApi()?.ui_rpc_ack?.(requestId);
        } else {
          // Sent to every window: only the first to claim it plays it.
          const claim = getApi()?.ui_rpc_claim;
          if (claim && (await claim(requestId)) === false) return;
        }
      }
      void dispatch(method, params, requestId).then((result) => {
        void getApi()?.ui_rpc_respond(requestId, result);
      });
    };
    const unsubscribe = subscribeAgentEvents((event) => void handler(event));
    const stopReady = onApiReady((api) => {
      void api.ui_rpc_pending_questions?.().then((events) => {
        for (const event of events) void handler(event);
      }).catch(() => {});
    });
    return () => { unsubscribe(); stopReady(); };
  }, []);

  // Lines of code can be shown too (editor.line.<path>:<n>).
  useEffect(() => installEditorLineTargets(), []);

  // Claim "active" when the user clicks or types in this window (at most every 3 s).
  useEffect(() => {
    let last = 0;
    const claim = () => {
      const now = Date.now();
      if (now - last < 3000) return;
      last = now;
      void getApi()?.ui_rpc_active?.(UI_CLIENT_ID, true);
    };
    // Closing: tell the panel this window is gone (a beacon still goes out while the page unloads).
    const leave = () => {
      const body = JSON.stringify({ args: { client_id: UI_CLIENT_ID, active: false } });
      try {
        if (navigator.sendBeacon?.("/__panel_api/ui_rpc_active", new Blob([body], { type: "application/json" }))) return;
      } catch {
        /* fall back to the API */
      }
      void getApi()?.ui_rpc_active?.(UI_CLIENT_ID, false);
    };
    if (typeof document !== "undefined" && document.hasFocus?.()) claim();
    window.addEventListener("pointerdown", claim, true);
    window.addEventListener("keydown", claim, true);
    window.addEventListener("focus", claim);
    window.addEventListener("pagehide", leave);
    return () => {
      window.removeEventListener("pointerdown", claim, true);
      window.removeEventListener("keydown", claim, true);
      window.removeEventListener("focus", claim);
      window.removeEventListener("pagehide", leave);
    };
  }, []);
  return null;
}
