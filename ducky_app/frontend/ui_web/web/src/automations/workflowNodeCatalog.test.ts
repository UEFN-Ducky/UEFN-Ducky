// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PanelPushEvent } from "../types/panel";

const api = vi.hoisted(() => ({ list_workflow_nodes: vi.fn() }));
const push = vi.hoisted(() => ({ listener: null as ((e: PanelPushEvent) => void) | null }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../hooks/useAgentEventBus", () => ({ subscribeAgentEvents: () => () => {} }));
vi.mock("../hooks/usePanelPushBus", () => ({
  subscribePanelPush: (fn: (e: PanelPushEvent) => void) => { push.listener = fn; return () => {}; },
}));
const { loadWorkflowNodes, withPluginIcons, _resetWorkflowNodesForTests } = await import("./workflowNodeCatalog");

const ICON = "data:image/png;base64,AAAA";
beforeEach(() => {
  _resetWorkflowNodesForTests();
  api.list_workflow_nodes.mockReset();
  api.list_workflow_nodes.mockResolvedValue({
    nodes: [{ type: "kit.make", label: "Make", group: "Kit", plugin_id: "kit", icon: "" }, { type: "flow.wait", label: "Wait", group: "Logic" }],
    icons: { kit: ICON },
  });
});

describe("workflow node catalog", () => {
  it("two readers (and two at once) cost one fetch", async () => {
    const [a, b] = await Promise.all([loadWorkflowNodes(), loadWorkflowNodes()]);
    const c = await loadWorkflowNodes();
    expect(api.list_workflow_nodes).toHaveBeenCalledTimes(1);
    expect(a).toBe(b);
    expect(c).toBe(a);
  });

  it("puts each plugin's shared icon back on its nodes", async () => {
    const nodes = await loadWorkflowNodes();
    expect(nodes[0].icon).toBe(ICON);
    expect(nodes[1].icon).toBeUndefined();
    expect(withPluginIcons([{ type: "x", label: "X", group: "G", plugin_id: "kit", icon: "own" }], { kit: ICON })[0].icon).toBe("own");
  });

  it("an empty answer is not kept; a plugin install or fresh read asks again", async () => {
    api.list_workflow_nodes.mockResolvedValueOnce(undefined);
    expect(await loadWorkflowNodes()).toEqual([]);
    await loadWorkflowNodes();
    expect(api.list_workflow_nodes).toHaveBeenCalledTimes(2);
    push.listener?.({ type: "uefn_plugins_changed" } as PanelPushEvent);
    await loadWorkflowNodes();
    await loadWorkflowNodes(true);
    expect(api.list_workflow_nodes).toHaveBeenCalledTimes(4);
  });
});
