import { useSyncExternalStore } from "react";
import {
  readOverlayRailSession,
  subscribeOverlayRailSession,
} from "../workspace/overlayRailSession";

export function useOverlayRailSession() {
  return useSyncExternalStore(
    subscribeOverlayRailSession,
    readOverlayRailSession,
    readOverlayRailSession,
  );
}
