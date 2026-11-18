import { useSyncExternalStore } from "react";

/** "Launching UEFN…" label while the phone's View menu waits for UEFN's window ("" = none).
 *  Its own module so the app shell reads it without loading the remote view. */
let uefnLaunchLabel = "";
const launchSubs = new Set<() => void>();

export function setUeFnLaunching(label: string) {
  if (uefnLaunchLabel === label) return;
  uefnLaunchLabel = label;
  launchSubs.forEach((fn) => fn());
}

export function useUeFnLaunching(): string {
  return useSyncExternalStore(
    (onChange) => {
      launchSubs.add(onChange);
      return () => launchSubs.delete(onChange);
    },
    () => uefnLaunchLabel,
    () => uefnLaunchLabel,
  );
}
