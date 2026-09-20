/**
 * Host mounts for LLM plugins (Settings slide + model picker footer).
 * Fire once on open / provider / model change — plugins must not poll the DOM.
 *
 * Mount is a sibling of a React marker so plugin innerHTML survives picker re-renders.
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

function ensureMount(
  marker: HTMLElement,
  surface: LlmPluginSurface,
  existing: HTMLDivElement | null,
): HTMLDivElement {
  const mount = existing || document.createElement("div");
  const parent = marker.parentNode;
  if (parent && mount.parentNode !== parent) {
    parent.insertBefore(mount, marker.nextSibling);
  }
  mount.className = surface === "picker" ? "model-selector-plugin-slot" : "llms-plugin-slot";
  return mount;
}

/** Empty mount. Plugins listen for `ducky:llm-slot` and render into `detail.mount`. */
export function LlmPluginSlot({ surface, open, providerId, pluginId, model }: SlotProps) {
  const markerRef = useRef<HTMLSpanElement>(null);
  const mountRef = useRef<HTMLDivElement | null>(null);
  const live = open && !!(providerId || "").trim();
  const pid = (pluginId || "").trim();
  const mid = (model || "").trim();
  const id = (providerId || "").trim();

  useLayoutEffect(() => {
    const marker = markerRef.current;
    if (!live || !marker?.parentNode) {
      emitLlmPluginSlot({
        surface,
        open: false,
        providerId: id,
        pluginId: pid,
        model: mid,
        mount: null,
      });
      return;
    }
    const mount = ensureMount(marker, surface, mountRef.current);
    mountRef.current = mount;
    mount.setAttribute("data-ducky-llm-slot", surface);
    mount.setAttribute("data-provider", id);
    mount.setAttribute("data-plugin", pid);
    mount.setAttribute("data-model", mid);
    emitLlmPluginSlot({
      surface,
      open: true,
      providerId: id,
      pluginId: pid,
      model: mid,
      mount,
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

  useLayoutEffect(() => {
    return () => {
      mountRef.current?.remove();
      mountRef.current = null;
    };
  }, []);

  return <span ref={markerRef} className="model-selector-plugin-slot-marker" hidden />;
}
