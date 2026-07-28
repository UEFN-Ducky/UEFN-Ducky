import { useEffect, useState, type MouseEvent } from "react";
import type { DuckyOSStoreItemDto } from "../../../types/panel";
import { itemKind } from "../storeFilters";
import { formatPrice, needsPurchase } from "./storeData";
import {
  loadPluginContributes,
  openInstalledPluginSettings,
  pluginHasWalkthrough,
  redoInstalledPluginWalkthrough,
  settingsTargetFromContributes,
} from "./pluginStoreActions";

export type StoreItemHandlers = {
  onInstall: (item: DuckyOSStoreItemDto) => void;
  onBuy: (item: DuckyOSStoreItemDto) => void;
  onToggle: (item: DuckyOSStoreItemDto) => void;
  onUninstall: (item: DuckyOSStoreItemDto) => void;
};

type Props = {
  item: DuckyOSStoreItemDto;
  handlers: StoreItemHandlers;
  busy: boolean;
  size: "card" | "detail";
};

/** State-driven action buttons shared by cards and the detail pane. */
export function StoreActions({ item, handlers, busy, size }: Props) {
  const state = item.state || "available";
  const isPlugin = itemKind(item) === "plugin";
  const mustBuy = needsPurchase(item);
  const canAct = (state === "available" || state === "update") && !mustBuy;
  const installed = state === "installed" || state === "update";
  const stop = (e: MouseEvent) => e.stopPropagation();
  const cls = (variant: string) => `ds-btn ds-btn--${variant} ds-btn--${size}`;

  const [hasSettings, setHasSettings] = useState(false);
  const [hasWalkthrough, setHasWalkthrough] = useState(false);

  useEffect(() => {
    if (!isPlugin || !installed || size !== "detail") {
      setHasSettings(false);
      setHasWalkthrough(false);
      return;
    }
    let cancelled = false;
    void loadPluginContributes(item.slug || "").then((contrib) => {
      if (cancelled) return;
      setHasSettings(Boolean(settingsTargetFromContributes(contrib)));
      setHasWalkthrough(pluginHasWalkthrough(contrib));
    });
    return () => {
      cancelled = true;
    };
  }, [isPlugin, installed, size, item.slug, item.enabled, item.installed_version]);

  return (
    <div className={`ds-actions ds-actions--${size}`} onClick={stop}>
      {mustBuy && (state === "available" || state === "update") ? (
        <button
          type="button"
          className={cls("install")}
          disabled={busy}
          onClick={() => handlers.onBuy(item)}
        >
          {busy ? "Working…" : `Buy ${formatPrice(item)}`}
        </button>
      ) : null}
      {canAct ? (
        <button
          type="button"
          className={cls("install")}
          disabled={busy}
          onClick={() => handlers.onInstall(item)}
        >
          {busy ? "Working…" : state === "update" ? "Update" : "Install"}
        </button>
      ) : null}
      {state === "installed" ? (
        <div className="ds-actions-pair">
          <button
            type="button"
            className={cls(item.enabled ? "toggle-on" : "toggle-off")}
            disabled={busy}
            onClick={() => handlers.onToggle(item)}
          >
            {item.enabled
              ? size === "detail"
                ? isPlugin
                  ? "Disable Plugin"
                  : "Disable Skill"
                : "Disable"
              : size === "detail"
                ? isPlugin
                  ? "Enable Plugin"
                  : "Enable Skill"
                : "Enable"}
          </button>
          <button
            type="button"
            className={cls("uninstall")}
            disabled={busy}
            onClick={() => handlers.onUninstall(item)}
          >
            Uninstall
          </button>
        </div>
      ) : null}
      {isPlugin && size === "detail" && installed && item.enabled && hasSettings ? (
        <button
          type="button"
          className={cls("plain")}
          disabled={busy}
          onClick={() => void openInstalledPluginSettings(item.slug || "")}
        >
          Open Settings
        </button>
      ) : null}
      {isPlugin && size === "detail" && installed && item.enabled && hasWalkthrough ? (
        <button
          type="button"
          className={cls("ghost")}
          disabled={busy}
          onClick={() => void redoInstalledPluginWalkthrough(item.slug || "")}
        >
          Redo Walkthrough
        </button>
      ) : null}
      {state === "unsupported" ? (
        <button type="button" className={cls("ghost")} disabled>
          Coming soon
        </button>
      ) : null}
    </div>
  );
}
