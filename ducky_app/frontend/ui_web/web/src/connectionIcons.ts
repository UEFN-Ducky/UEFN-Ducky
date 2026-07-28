import type { ListenerStatus } from "./types/panel";

export const CONNECTION_ICONS = {
  online: "./OnlineMCPIcon.png",
  wedged: "./WedgedMCPIcon.png",
  offline: "./OfflineMCPIcon.png",
} as const;

export type ConnectionMode = keyof typeof CONNECTION_ICONS;

export function connectionModeFromFlags(online: boolean, wedged?: boolean): ConnectionMode {
  // Wedged listener is still reachable (GET ok) but not processing commands —
  // prefer the wedged icon over a false "online" duck.
  if (wedged) return "wedged";
  if (online) return "online";
  return "offline";
}

export function connectionModeFromStatus(status: ListenerStatus): ConnectionMode {
  return connectionModeFromFlags(status.online, status.wedged);
}

export function connectionIconSrc(online: boolean, wedged?: boolean): string {
  return CONNECTION_ICONS[connectionModeFromFlags(online, wedged)];
}
