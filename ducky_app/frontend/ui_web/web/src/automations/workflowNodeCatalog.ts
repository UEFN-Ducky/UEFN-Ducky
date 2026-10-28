import type { AgentEvent, AutomationNodeDto } from "../types/panel";
import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { getApi } from "../hooks/usePanelApi";
import { subscribePanelPush } from "../hooks/usePanelPushBus";

/**
 * The workflow node catalog, read once per window and shared (Automations, the media
 * pickers). It is megabytes; every mount used to ask for it again. A plugin install or a
 * settings change (which backends can run) drops it, so the next reader asks again.
 */
let cached: AutomationNodeDto[] | null = null;
let inflight: Promise<AutomationNodeDto[]> | null = null;
let watching = false;

/** The list sends a plugin's icon once in `icons`, not on each of its nodes: put it back. */
export function withPluginIcons(nodes: AutomationNodeDto[], icons?: Record<string, string>): AutomationNodeDto[] {
  if (!icons) return nodes;
  return nodes.map((node) => (!node.icon && node.plugin_id && icons[node.plugin_id] ? { ...node, icon: icons[node.plugin_id] } : node));
}

function watch(): void {
  if (watching) return;
  watching = true;
  subscribePanelPush((event) => {
    if (event.type === "uefn_plugins_changed" || event.type === "models_updated") cached = null;
  });
  subscribeAgentEvents((event: AgentEvent) => {
    if (event.type === "settings_changed") cached = null;
  });
}

/** The catalog; `fresh` asks the backend even when one is cached. An empty answer is not kept. */
export function loadWorkflowNodes(fresh = false): Promise<AutomationNodeDto[]> {
  watch();
  if (cached && !fresh) return Promise.resolve(cached);
  if (inflight) return inflight;
  const run = (async () => {
    const res = await getApi()?.list_workflow_nodes?.();
    const nodes = withPluginIcons(res?.nodes || [], res?.icons);
    if (nodes.length) cached = nodes;
    return nodes;
  })().finally(() => {
    if (inflight === run) inflight = null;
  });
  inflight = run;
  return run;
}

/** ponytail: test-only reset. */
export function _resetWorkflowNodesForTests(): void {
  cached = null;
  inflight = null;
}
