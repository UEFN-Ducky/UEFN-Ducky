/**
 * Ducky's own model picker, opened by a plugin panel (bridge `ui.pickModel`).
 *
 * The panel lives in a sandboxed iframe, so it cannot render app components itself; it asks
 * the host, which shows the same ModelSelector as the chat composer in a dialog and returns
 * the qualified `backend:model` id (what `llm.complete` takes). `purpose: "video"` keeps only
 * models that can read pictures, the ones that can judge footage frames. `purpose: "agent"` is for
 * a job that runs as a chat (the plugin opens one with this pick), so coding agents such as Codex
 * or Claude Code are offered too, like a workflow node's model picker.
 *
 * `extra` adds the plugin's own rows ({id, name, group, note?, vision?}), each group its own
 * accordion: Play Tester lists Jev under TypeSafe, tagged "Not for chat". A picked extra row comes
 * back as its id, as given. `hide` (gateway ids) leaves those gateways' own models out: Play Tester
 * hides Ducky AI's chat model, and keeps its own Jev row there only with a Ducky AI plan.
 *
 * With `anchor` (the panel button's box in app coordinates) there is no dialog: the same
 * gateway menu as the chat composer drops down from that button, and the first pick answers.
 * API models only, except with `purpose: "agent"`: then logins (Claude Code, Codex…) are listed
 * too, exactly like the chat's dropdown, and a login pick comes back agent-qualified. Like the chat's,
 * a click marks the model and leaves the menu open for the slider; closing it answers.
 *
 * `effort` (a thinking effort id, e.g. "off") shows the chat's Faster / Smarter slider under the
 * menu; the answer then carries `effort`. Moving only the slider answers with the current model.
 */
import { useEffect, useRef, useState } from "react";

import { getCachedModels, type CatalogModelRow } from "../hooks/modelsCatalogCache";
import { getCachedCodingAgents } from "../hooks/codingAgentsCache";
import { ModelSelector } from "../components/ModelSelector";
import { Modal, ModalActions } from "../components/Modal";
import { qualifyModelPick } from "../components/ducky/DuckyModelPicker";

export type ModelPickPurpose = "video" | "agent" | "chat";
export interface ModelPickResult {
  model: string;
  name: string;
  effort?: string;
}
interface PickRequest {
  purpose: ModelPickPurpose;
  current: string;
  title: string;
  extra: CatalogModelRow[];
  hide: string[];
  anchor: PickAnchor | null;
  /** null = no slider. */
  effort: string | null;
  resolve: (result: ModelPickResult | null) => void;
}
export interface PickAnchor {
  left: number;
  top: number;
  width: number;
  height: number;
}

function anchorBox(raw: unknown): PickAnchor | null {
  const r = (raw && typeof raw === "object" ? raw : null) as Record<string, unknown> | null;
  if (!r) return null;
  const [left, top, width, height] = [r.left, r.top, r.width, r.height].map(Number);
  return [left, top, width, height].every(Number.isFinite) ? { left, top, width, height } : null;
}

/** A plugin's extra rows as catalog rows; anything without an id and a group is dropped. */
export function extraRows(extra: unknown): CatalogModelRow[] {
  if (!Array.isArray(extra)) return [];
  const out: CatalogModelRow[] = [];
  for (const raw of extra.slice(0, 50)) {
    const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    const id = String(r.id || "").trim();
    const group = String(r.group || "").trim();
    if (!id || !group || out.some((row) => row.id === id)) continue;
    out.push({
      provider: group,
      providerKey: id.includes(":") ? id.slice(0, id.indexOf(":")) : group,
      id,
      name: String(r.name || id).trim() || id,
      supportsVision: !!r.vision,
      supportsTools: false,
      supportsWebSearch: false,
      contextLimit: 0,
      priceIn: null,
      priceOut: null,
      isLocal: false,
      note: String(r.note || "").trim().slice(0, 40) || undefined,
    });
  }
  return out;
}

let openPicker: ((request: PickRequest) => void) | null = null;

export function pickModelFromHost(options: {
  purpose?: string;
  current?: string;
  title?: string;
  extra?: unknown;
  hide?: unknown;
  anchor?: unknown;
  effort?: unknown;
}): Promise<ModelPickResult | null> {
  if (!openPicker) return Promise.reject(new Error("model picker is not mounted"));
  const show = openPicker;
  return new Promise((resolve) =>
    show({
      purpose: options.purpose === "video" ? "video" : options.purpose === "agent" ? "agent" : "chat",
      current: String(options.current || ""),
      title: String(options.title || ""),
      extra: extraRows(options.extra),
      hide: Array.isArray(options.hide) ? options.hide.slice(0, 20).map((id) => String(id || "").trim().toLowerCase()).filter(Boolean) : [],
      anchor: anchorBox(options.anchor),
      effort: typeof options.effort === "string" ? options.effort.trim().slice(0, 40) || "off" : null,
      resolve,
    }),
  );
}

export function PluginModelPickerHost() {
  const [request, setRequest] = useState<PickRequest | null>(null);
  const [picked, setPicked] = useState("");
  const [openSignal, setOpenSignal] = useState(0);
  const [agent, setAgent] = useState("ducky");
  const [effort, setEffort] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  // The dropdown sets the agent and answers in the same click, before React re-renders.
  const agentRef = useRef("ducky");
  // purpose "agent" dropdown: a click marks the model and the menu stays open (like the chat's, so the
  // slider can still move); closing it answers with this.
  const chosenRef = useRef<ModelPickResult | null>(null);

  useEffect(() => {
    openPicker = (next) => {
      setRequest((previous) => {
        previous?.resolve(null);
        return next;
      });
      const own = next.extra.some((row) => row.id === next.current);
      const current =
        !own && next.current.includes(":") ? next.current.slice(next.current.indexOf(":") + 1) : next.current;
      const prefix = next.current.includes(":") ? next.current.slice(0, next.current.indexOf(":")) : "";
      const startAgent = next.purpose === "agent" && getCachedCodingAgents().some((a) => a.id === prefix) ? prefix : "ducky";
      agentRef.current = startAgent;
      chosenRef.current = null;
      setAgent(startAgent);
      setPicked(current);
      setProvider(own || startAgent !== "ducky" ? "" : prefix);
      setEffort(next.effort);
      setOpenSignal((n) => n + 1);
    };
    return () => {
      openPicker = null;
    };
  }, []);

  // The dropdown's selector mounts with the request, so it is opened once it is there.
  useEffect(() => {
    if (request?.anchor) setOpenSignal((n) => n + 1);
  }, [request]);

  if (!request) return null;
  const rows = [
    ...request.extra,
    ...(getCachedModels() ?? []).filter(
      (row) =>
        (request.purpose !== "video" || row.supportsVision) &&
        !request.hide.includes((row.providerKey || "").toLowerCase().replace(/-/g, "_")),
    ),
  ];
  const finish = (result: ModelPickResult | null) => {
    request.resolve(result && effort !== null ? { ...result, effort } : result);
    setRequest(null);
  };
  const effortProps =
    effort !== null ? { thinkingEffort: effort, onEffortChange: (next: string) => setEffort(next) } : {};
  // Only the slider moved: answer with the model already picked, so the new effort is kept.
  const sliderOnly = () =>
    effort !== null && effort !== request.effort && request.current
      ? { model: request.current, name: picked || request.current }
      : null;
  const confirm = () => {
    const id = picked.trim();
    if (!id) return finish(null);
    const row = rows.find((r) => r.id === id);
    if (request.extra.some((r) => r.id === id)) return finish({ model: id, name: row?.name || id });
    const viaAgent = request.purpose === "agent" ? agent || "ducky" : "ducky";
    finish({ model: qualifyModelPick(viaAgent, id), name: row?.name || id });
  };

  if (request.anchor) {
    const { left, top, width, height } = request.anchor;
    // The gateway it was picked under wins: Claude Opus sits under Anthropic and under Cursor.
    const pick = (id: string, providerKey = "") => {
      const row = rows.find((r) => r.id === id && (!providerKey || r.providerKey === providerKey)) || rows.find((r) => r.id === id);
      if (request.extra.some((r) => r.id === id)) return finish({ model: id, name: row?.name || id });
      const gw = providerKey.trim().toLowerCase().replace(/-/g, "_");
      const viaLogin = request.purpose === "agent" && agentRef.current !== "ducky";
      const result = {
        model: viaLogin ? qualifyModelPick(agentRef.current, id) : gw ? `${gw}:${id}` : qualifyModelPick("ducky", id),
        name: row?.name || id,
      };
      if (request.purpose !== "agent") return finish(result);
      chosenRef.current = result;
      setPicked(id);
      setProvider(viaLogin ? "" : gw);
    };
    return (
      <div style={{ position: "fixed", left, top, width, height, opacity: 0, pointerEvents: "none" }} aria-hidden="true">
        <ModelSelector
          selectedModel={picked}
          selectedProvider={provider}
          setSelectedModel={pick}
          extraRows={request.extra}
          hideProviders={request.hide}
          requireVision={request.purpose === "video"}
          {...(request.purpose === "agent"
            ? {
                codingAgent: agent,
                setCodingAgent: (id: string) => {
                  agentRef.current = id;
                  setAgent(id);
                },
              }
            : { apiOnly: true })}
          closeOnPick={request.purpose !== "agent"}
          preserveSelection
          openSignal={openSignal}
          menuPlacement="bottom"
          {...effortProps}
          onOpenChange={(open) => {
            if (!open) finish(chosenRef.current ?? sliderOnly());
          }}
        />
      </div>
    );
  }
  const title =
    request.title ||
    (request.purpose === "video"
      ? "Pick the model that plans your video"
      : request.purpose === "agent"
        ? "Pick the AI agent"
        : "Pick a model");

  return (
    <Modal
      open
      onClose={() => finish(null)}
      title={title}
      width={460}
      footer={
        <ModalActions
          onCancel={() => finish(null)}
          onConfirm={confirm}
          confirmLabel="Use this model"
          confirmDisabled={!picked.trim()}
        />
      }
    >
      <p className="confirm-modal-message">
        {request.purpose === "video"
          ? rows.length
            ? "Only models that can read pictures are listed: they judge your footage frames."
            : "No model that can read pictures is set up. Add one in Settings → LLMs."
          : request.purpose === "agent"
            ? "Any model or coding agent, like a workflow node. The job runs in a chat you can watch."
            : "The same models as the chat."}
      </p>
      {request.purpose === "agent" ? (
        <ModelSelector
          selectedModel={picked}
          setSelectedModel={setPicked}
          codingAgent={agent}
          setCodingAgent={setAgent}
          preserveSelection
          openSignal={openSignal}
          labeled
        />
      ) : (
        <ModelSelector
          selectedModel={picked}
          setSelectedModel={setPicked}
          catalogRows={rows}
          preserveSelection
          openSignal={openSignal}
          labeled
          {...effortProps}
        />
      )}
    </Modal>
  );
}
