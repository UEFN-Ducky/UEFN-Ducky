import { useSyncExternalStore } from "react";
import type { ToolCallData } from "../types/panel";

export function toolActivityId(tool?: ToolCallData): string {
  return tool?.id || `${tool?.name || "tool"}:${JSON.stringify(tool?.arguments || {})}`;
}
type Target = { toolId: string; serial: number };
const targets = new Map<string, Target>();
const listeners = new Set<() => void>();
let serial = 0;
export function requestToolActivity(convId: string, toolId: string): void {
  targets.delete(convId);
  targets.set(convId, { toolId, serial: ++serial });
  if (targets.size > 40) targets.delete(targets.keys().next().value!);
  for (const listener of listeners) listener();
}
export function useToolActivityTarget(convId: string): Target | undefined {
  return useSyncExternalStore((fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    () => targets.get(convId), () => undefined);
}
