import { useSyncExternalStore } from "react";
import {
  dismissPluginCrashNotice,
  getPluginCrashNotices,
  subscribePluginCrashNotices,
} from "./pluginCrashGuard";
import { requestOpenSettings } from "../navigation/openSettingsTab";

/** Sticky banner naming which plugin/theme faulted and what we did. */
export function PluginCrashBanner() {
  const notices = useSyncExternalStore(
    subscribePluginCrashNotices,
    getPluginCrashNotices,
    () => [],
  );
  if (!notices.length) return null;

  return (
    <div className="plugin-crash-banner-stack" role="status">
      {notices.map((n) => (
        <div key={n.id} className="plugin-crash-banner">
          <div className="plugin-crash-banner-text">
            <strong>
              {n.kind === "theme" ? "Theme" : "Plugin"} “{n.pluginId}”
            </strong>
            {n.action === "disabled"
              ? " crashed and was auto-disabled"
              : " theme crashed and was cleared"}
            <span className="plugin-crash-banner-surface"> ({n.surface})</span>
            {n.message ? (
              <span className="plugin-crash-banner-msg"> — {n.message}</span>
            ) : null}
          </div>
          <div className="plugin-crash-banner-actions">
            {n.action === "disabled" ? (
              <button
                type="button"
                className="plugin-crash-banner-btn"
                onClick={() => requestOpenSettings("Store")}
              >
                Open Store
              </button>
            ) : null}
            <button
              type="button"
              className="plugin-crash-banner-btn"
              onClick={() => dismissPluginCrashNotice(n.id)}
            >
              Dismiss
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
