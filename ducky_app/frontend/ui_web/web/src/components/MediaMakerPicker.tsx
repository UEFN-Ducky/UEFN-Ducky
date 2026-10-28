/**
 * Which plugin makes pictures or 3D models when nothing else is picked (a workflow node with
 * no backend, a ducky's ducky_generate_image). The choices are the makers the workflow nodes
 * list; one that is not set up yet says what it needs.
 */
import { useEffect, useState } from "react";
import { getApi } from "../hooks/usePanelApi";
import { loadWorkflowNodes } from "../automations/workflowNodeCatalog";
import type { AutomationNodeDto } from "../types/panel";
import { ChoiceDropdown } from "./ChoiceDropdown";

export type MediaKind = "image" | "mesh";
const NODES: Record<MediaKind, string[]> = { image: ["image.generate"], mesh: ["mesh.generate", "mesh.from_image"] };
const SETTING = { image: "default_image_provider", mesh: "default_mesh_provider" } as const;

export type Maker = { id: string; label: string; ready: boolean; reason?: string; cost?: string };

/** One row per plugin across the kind's nodes: ready when any of its backends can run here. */
export function makersFrom(nodes: AutomationNodeDto[], kind: MediaKind): Maker[] {
  const out = new Map<string, Maker>();
  for (const node of nodes) {
    if (!NODES[kind].includes(node.type)) continue;
    for (const b of node.backends || []) {
      const id = b.plugin_id || b.id;
      const cur = out.get(id);
      if (!cur) out.set(id, { id, label: b.plugin, ready: b.available, reason: b.reason, cost: b.cost });
      else if (b.available && !cur.ready) out.set(id, { ...cur, ready: true, reason: undefined, cost: b.cost });
    }
  }
  return [...out.values()];
}

export function MediaMakerPicker({ kind, label }: { kind: MediaKind; label: string }) {
  const [makers, setMakers] = useState<Maker[] | null>(null);
  const [value, setValue] = useState("");

  useEffect(() => {
    const api = getApi();
    if (!api) return;
    let live = true;
    // Fresh: this sits in Settings, where a key just added makes a backend ready.
    void loadWorkflowNodes(true)
      .then((nodes) => live && setMakers(makersFrom(nodes, kind)))
      .catch(() => live && setMakers([]));
    void Promise.resolve(api.get_settings?.())
      .then((s) => live && setValue(String(s?.[SETTING[kind]] || "")))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [kind]);

  const picked = makers?.find((m) => m.id === value);
  return (
    <span className="media-maker-picker">
      <ChoiceDropdown
        size="compact"
        mode="radio"
        aria-label={label}
        value={value}
        disabled={!makers}
        placeholder={makers ? "None installed" : "Loading…"}
        options={[
          { value: "", label: "First one set up" },
          ...(makers || []).map((m) => ({ value: m.id, label: m.label, hint: m.ready ? m.cost : "Not set up" })),
        ]}
        onChange={(next) => {
          setValue(next);
          void getApi()?.save_agent_settings({ [SETTING[kind]]: next });
        }}
      />
      {picked && !picked.ready && picked.reason ? <span className="setup-wizard-muted">{picked.reason}</span> : null}
    </span>
  );
}
