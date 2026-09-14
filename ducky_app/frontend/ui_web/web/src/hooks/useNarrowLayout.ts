import { useSyncExternalStore } from "react";

export const NARROW_LAYOUT_MQ = "(max-width: 720px)";

export function useNarrowLayout() {
  return useSyncExternalStore(
    (onChange) => {
      const mq = window.matchMedia(NARROW_LAYOUT_MQ);
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    },
    () => window.matchMedia(NARROW_LAYOUT_MQ).matches,
    () => false,
  );
}
