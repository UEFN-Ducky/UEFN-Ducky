import { useEffect } from "react";
import type { ListenerStatus } from "../types/panel";
import { CONNECTION_ICONS, connectionModeFromStatus } from "../connectionIcons";

/** Swap the browser tab favicon to match listener connection state. */
export function useConnectionIcon(status: ListenerStatus) {
  useEffect(() => {
    const mode = connectionModeFromStatus(status);
    let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    link.type = "image/png";
    link.href = CONNECTION_ICONS[mode];
  }, [status.online, status.wedged]);
}