/** Scope bar wording (plan §8). Pure so the 80% / full rules have one test. */
import type { PluginScopeStatus, PluginScopeUsage } from "../types/panel";

const UNITS = ["B", "KB", "MB", "GB", "TB"];

export function formatBytes(n: number): string {
  let v = Math.max(0, n || 0);
  let i = 0;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 || v >= 10 ? Math.round(v) : v.toFixed(1).replace(/\.0$/, "")} ${UNITS[i]}`;
}

export type StorageLine = { text: string; pct: number; level: "ok" | "warn" | "full"; note: string };

/** "34 MB of 5 GB" + level: warn from 80%, full at the limit. The note carries the
 * level as text so it never rests on colour alone. */
export function storageLine(usage: PluginScopeUsage | undefined): StorageLine | null {
  const used = usage?.usedBytes ?? 0;
  const limit = usage?.limitBytes ?? 0;
  if (!limit) return null;
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const level = used >= limit ? "full" : used * 10 >= limit * 8 ? "warn" : "ok";
  const note = level === "full" ? "Storage full" : level === "warn" ? `${pct}% used` : "";
  return { text: `${formatBytes(used)} of ${formatBytes(limit)}`, pct, level, note };
}

export function agoText(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

/** Sync part of a team bar: synced time, queued changes, or why it isn't syncing. */
export function syncText(status: PluginScopeStatus, nowMs: number): string {
  const pending = status.pending ?? 0;
  const waiting = pending ? `${pending} change${pending === 1 ? "" : "s"} waiting` : "";
  if (status.state === "offline") return `Offline${waiting ? ` · ${waiting}` : ""}`;
  if (status.state === "error") return `Not synced${waiting ? ` · ${waiting}` : ""}`;
  const synced = status.syncedAt ? `Synced ${agoText(nowMs / 1000 - status.syncedAt)}` : "Not synced yet";
  return waiting ? `${synced} · ${waiting}` : synced;
}

/** The question before a plugin's data switches: nothing is copied, and the plugin
 * restarts on the other copy. */
export function switchConfirm(plugin: string, to: { kind: string; label: string }): { title: string; message: string } {
  const team = to.kind === "team";
  return {
    title: `Switch ${plugin} to ${team ? to.label : "Local"} data?`,
    message: `Nothing is copied. ${plugin} restarts with ${team ? `${to.label}'s` : "its Local"} data.`,
  };
}
