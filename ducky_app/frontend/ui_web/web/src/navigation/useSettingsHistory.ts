import { useCallback, useEffect } from "react";
import {
  sameLocation,
  useNavigationHistoryOptional,
  type NavLocation,
} from "./NavigationHistoryContext";
import {
  clearPendingSettingsDrill,
  subscribeSettingsDrillApply,
  type SettingsNavLocation,
} from "./settingsHistory";

/** Record the current settings location into the global back/forward stack. */
export function useRecordSettingsLocation(loc: SettingsNavLocation | null): void {
  const nav = useNavigationHistoryOptional();
  useEffect(() => {
    if (loc) nav?.record(loc);
  }, [loc, nav]);
}

function isStoreSettings(loc: NavLocation | null): loc is SettingsNavLocation {
  return Boolean(loc && loc.kind === "settings" && loc.tab === "Store");
}

/** True when ``current`` is the Store list under ``detail`` (main or matching section). */
export function isStoreListUnderDetail(
  current: NavLocation | null,
  detail: SettingsNavLocation,
): boolean {
  if (!isStoreSettings(current) || detail.drill?.type !== "store" || !detail.drill.slug) {
    return false;
  }
  const section = detail.drill.section;
  if (section) {
    return (
      current.drill?.type === "store" &&
      current.drill.section === section &&
      !current.drill.slug
    );
  }
  return !current.drill;
}

/**
 * Record Store main → section → detail with parents inserted when jumping in from
 * another Settings tab (e.g. Support → Installed plugin detail).
 */
export function useRecordStoreSettingsLocation(loc: SettingsNavLocation): void {
  const nav = useNavigationHistoryOptional();
  useEffect(() => {
    if (!nav) return;
    const current = nav.peekCurrent();
    if (current && sameLocation(current, loc)) return;

    if (loc.drill?.type === "store" && loc.drill.slug) {
      // Support → Installed row opens detail without a Plugins list entry — insert one.
      if (!isStoreListUnderDetail(current, loc)) {
        const parent: SettingsNavLocation = loc.drill.section
          ? {
              kind: "settings",
              tab: "Store",
              drill: { type: "store", section: loc.drill.section, slug: null },
              name: loc.drill.section,
            }
          : { kind: "settings", tab: "Store", name: "Store" };
        nav.record(parent);
      }
    } else if (loc.drill?.type === "store" && loc.drill.section) {
      const root: SettingsNavLocation = { kind: "settings", tab: "Store", name: "Store" };
      if (!(isStoreSettings(current) && !current.drill)) {
        nav.record(root);
      }
    }
    nav.record(loc);
  }, [loc, nav]);
}

/** Restore slide state when history applies a settings location for this tab. */
export function useApplySettingsDrill(
  tab: string,
  apply: (loc: SettingsNavLocation) => void,
): void {
  useEffect(
    () =>
      subscribeSettingsDrillApply((loc) => {
        if (loc.tab !== tab) return;
        clearPendingSettingsDrill(tab);
        apply(loc);
      }),
    [tab, apply],
  );
}

/** In-pane Back: prefer history so forward still works; fall back to local close. */
export function useSettingsHistoryBack(fallback: () => void): () => void {
  const nav = useNavigationHistoryOptional();
  return useCallback(() => {
    if (nav?.canBack) nav.back();
    else fallback();
  }, [nav, fallback]);
}

/**
 * Store layer Back: walk history only when the previous entry is still Plugins/Store.
 * Otherwise close locally and replace the current history entry (never jump to Support).
 */
export function useStoreSettingsLayerBack(
  parentLoc: SettingsNavLocation,
  closeLocal: () => void,
): () => void {
  const nav = useNavigationHistoryOptional();
  return useCallback(() => {
    const prev = nav?.peekBack() ?? null;
    if (nav?.canBack && isStoreSettings(prev)) {
      nav.back();
      return;
    }
    closeLocal();
    nav?.replace(parentLoc);
  }, [nav, parentLoc, closeLocal]);
}
