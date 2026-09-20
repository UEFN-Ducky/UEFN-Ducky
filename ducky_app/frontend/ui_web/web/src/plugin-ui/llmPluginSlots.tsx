/**
 * Host mounts for LLM plugins (Settings slide + model picker footer).
 * Fire once on open / provider / model change — plugins must not poll the DOM.
 */
import { useLayoutEffect, useRef } from "react";
import { emitAppHook } from "../sfx/appHooks";

export const LLM_SLOT_EVENT = "ducky:llm-slot";

export type LlmPluginSurface = "settings" | "picker";

export type LlmPluginSlotDetail = {
  surface: LlmPluginSurface;
  open: boolean;
  providerId: string;
  pluginId: string;
  model: string;
  mount: HTMLElement | null;
};

export function emitLlmPluginSlot(detail: LlmPluginSlotDetail): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(LLM_SLOT_EVENT, { detail }));
  emitAppHook(detail.surface === "picker" ? "model.picker" : "llms.settings", {
    open: detail.open,
    providerId: detail.providerId,
    pluginId: detail.pluginId,
    model: detail.model,
  });
}

export function subscribeLlmPluginSlot(fn: (detail: LlmPluginSlotDetail) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const listener = (ev: Event) => {
    const detail = (ev as CustomEvent<LlmPluginSlotDetail>).detail;
    if (!detail?.surface) return;
    fn(detail);
  };
  window.addEventListener(LLM_SLOT_EVENT, listener);
  return () => window.removeEventListener(LLM_SLOT_EVENT, listener);
}

type SlotProps = {
  surface: LlmPluginSurface;
  open: boolean;
  providerId: string;
  pluginId?: string;
  model?: string;
};

/** Empty mount. Plugins listen for `ducky:llm-slot` and render into `detail.mount`. */
export function LlmPluginSlot({ surface, open, providerId, pluginId, model }: SlotProps) {
  const ref = useRef<HTMLDivElement>(null);
  const live = open && !!(providerId || "").trim();
  const pid = (pluginId || "").trim();
  const mid = (model || "").trim();
  const id = (providerId || "").trim();

  useLayoutEffect(() => {
    emitLlmPluginSlot({
      surface,
      open: live,
      providerId: live ? id : "",
      pluginId: pid,
      model: mid,
      mount: live ? ref.current : null,
    });
    return () => {
      emitLlmPluginSlot({
        surface,
        open: false,
        providerId: id,
        pluginId: pid,
        model: mid,
        mount: null,
      });
    };
  }, [surface, live, id, pid, mid]);

  if (!live) return null;
  return (
    <div
      ref={ref}
      className={surface === "picker" ? "model-selector-plugin-slot" : "llms-plugin-slot"}
      data-ducky-llm-slot={surface}
      data-provider={id}
      data-plugin={pid}
      data-model={mid}
    />
  );
}
