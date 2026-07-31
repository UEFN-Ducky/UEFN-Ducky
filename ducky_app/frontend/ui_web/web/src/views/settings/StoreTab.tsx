import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChoiceDropdown } from "../../components/ChoiceDropdown";
import { Modal } from "../../components/Modal";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { Icons } from "../../icons/Icons";
import { onApiReady } from "../../hooks/onApiReady";
import { getApi } from "../../hooks/usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "../../hooks/usePanelPushBus";
import {
  subscribePluginTrustRequest,
  takePluginTrustRequest,
  type PluginTrustRequest,
} from "../../hooks/pluginTrustRequest";
import { notifyStoreCatalogItems } from "../../hooks/useStoreUpdateBadge";
import {
  beginStoreInstall,
  clearStoreJobLater,
  dismissAllStoreJobToasts,
  endStoreInstall,
  hideStoreJobToast,
  isStoreInstallBusy,
  isStoreInstallQueueActive,
  markStoreCatalogDirty,
  patchStoreJob,
  reclaimOrphanStoreJobs,
  revealAllStoreJobToasts,
  revealStoreJobToast,
  runStoreInstallExclusive,
  setStoreInstallQueueIdleHandler,
  takeStoreCatalogDirty,
  useStoreInstallJobs,
} from "../../hooks/storeInstallJobs";
import { consumeStoreCategoryRequest, consumeStoreInstallRequest } from "../../navigation/deepLinks";
import { requestOpenSettings } from "../../navigation/openSettingsTab";
import type { SettingsNavLocation } from "../../navigation/settingsHistory";
import {
  useApplySettingsDrill,
  useRecordStoreSettingsLocation,
  useStoreSettingsLayerBack,
} from "../../navigation/useSettingsHistory";
import type { DuckyOSAccountStatus, DuckyOSStoreCatalog, DuckyOSStoreItemDto } from "../../types/panel";
import {
  categoryLabel,
  CORE_STORE_CATEGORIES,
  filterStoreItems,
  HIDDEN_BROWSE_CATEGORIES,
  AI_MADE_CATEGORY,
  INSTALLED_CATEGORY,
  itemCategories,
  itemKind,
  OWNED_CATEGORY,
} from "./storeFilters";
import { StoreCard } from "./store/StoreCard";
import { StoreDetailView } from "./store/StoreDetailView";
import { StoreHero } from "./store/StoreHero";
import { StoreJobStack } from "./store/StoreJobStack";
import { StoreRow } from "./store/StoreRow";
import { StoreSectionView } from "./store/StoreSectionView";
import type { StoreItemHandlers } from "./store/StoreActions";
import {
  deriveHeroSlides,
  deriveSections,
  needsPurchase,
  patchItemFromLocalPlugin,
} from "./store/storeData";
import { peekStoreCatalogCache, rememberStoreCatalog } from "../../hooks/storeCatalogCache";
import { StoreHeroSkeleton, StoreSkeletonRows } from "./store/StoreSkeleton";
import { useUiTarget } from "../../ui-targets/registry";
import { maybeStartPluginWalkthrough } from "../../walkthrough";

/** Manual catalog refresh cooldown — stop hammering the Store collect endpoint. */
const MANUAL_REFRESH_COOLDOWN_MS = 5000;

/** Bridge calls with no host timeout — never leave Enable/Uninstall stuck forever. */
const STORE_MUTATION_TIMEOUT_MS = 30_000;

/**
 * One toggle pushes ``uefn_plugins_changed`` twice — once synchronously from
 * ``set_uefn_plugin_enabled`` and once from the host's background enable thread.
 * Trailing-edge them into a single refresh pass so a click repaints the Store once.
 */
const PLUGINS_CHANGED_DEBOUNCE_MS = 300;

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error(`${label} timed out — state will re-sync`));
    }, ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer !== undefined) clearTimeout(timer);
  });
}

export function StoreTab() {
  const { confirm } = useConfirmModal();
  const storeRootRef = useUiTarget("settings.store.root", {
    kind: "settings_field",
    label: "Store",
    route: "settings.store",
  });
  const storeCatalogRef = useUiTarget("settings.store.catalog", {
    kind: "settings_field",
    label: "Store catalog",
    route: "settings.store",
  });
  const [status, setStatus] = useState<DuckyOSAccountStatus | null>(null);
  const [catalog, setCatalog] = useState<DuckyOSStoreCatalog | null>(() => peekStoreCatalogCache());
  const [catalogLoading, setCatalogLoading] = useState(() => !peekStoreCatalogCache()?.items?.length);
  const [staggerCards, setStaggerCards] = useState(false);
  /** Concurrent install/update jobs — module-owned so Store tab remount keeps progress. */
  const { jobs, hiddenToasts } = useStoreInstallJobs();
  /** Enable / buy / uninstall / local-file busy flags (not install pipeline). */
  const [actionBusy, setActionBusy] = useState<Record<string, true>>({});
  const [error, setError] = useState("");
  const [detailItem, setDetailItem] = useState<DuckyOSStoreItemDto | null>(null);
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const [uninstallItem, setUninstallItem] = useState<DuckyOSStoreItemDto | null>(null);
  const [uninstallLabels, setUninstallLabels] = useState<string[]>([]);
  const [eraseData, setEraseData] = useState(false);
  const [uninstallBusy, setUninstallBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState(() => {
    return consumeStoreCategoryRequest() || "";
  });
  const fileRef = useRef<HTMLInputElement>(null);
  const sharedRefresh = useRef<Promise<boolean> | null>(null);
  const lastManualRefreshAt = useRef(0);
  const sectionLayerRef = useRef<HTMLDivElement>(null);
  const detailLayerRef = useRef<HTMLDivElement>(null);
  const sawItemsRef = useRef(Boolean(peekStoreCatalogCache()?.items?.length));
  const [catalogRefreshing, setCatalogRefreshing] = useState(false);

  const setActionBusySlug = useCallback((slug: string, on: boolean) => {
    setActionBusy((prev) => {
      if (on) {
        if (prev[slug]) return prev;
        return { ...prev, [slug]: true };
      }
      if (!prev[slug]) return prev;
      const { [slug]: _drop, ...rest } = prev;
      return rest;
    });
  }, []);

  // Install may finish after StoreTab unmounted — pull fresh catalog from cache.
  useEffect(() => {
    const finished = Object.values(jobs).some((j) => j.phase === "done" || j.phase === "error");
    if (!finished) return;
    const cached = peekStoreCatalogCache();
    if (cached?.items?.length) {
      setCatalog(cached);
      notifyStoreCatalogItems(cached.items);
    }
  }, [jobs]);

  const refreshCatalog = useCallback(async (): Promise<boolean> => {
    const api = getApi();
    if (!api || typeof api.duckyos_store_catalog !== "function") {
      setCatalogLoading(false);
      if (!peekStoreCatalogCache()?.items?.length) {
        setCatalog({
          ok: false,
          error: "Store API unavailable — restart the control panel",
          items: [],
        });
      }
      setError("Store API unavailable — restart the control panel");
      return false;
    }
    if (!peekStoreCatalogCache()?.items?.length) setCatalogLoading(true);
    try {
      const next = await api.duckyos_store_catalog();
      // Never wipe a good catalog with a failed empty refresh (e.g. transient "forbidden").
      if (next.ok === false && !(next.items || []).length) {
        const kept = peekStoreCatalogCache();
        if (kept?.items?.length) {
          setCatalog(kept);
          setError(next.error || "Store refresh failed — showing cached catalog");
          setCatalogLoading(false);
          return false;
        }
        setCatalog(next);
        setError(next.error || "Store unavailable");
        setCatalogLoading(false);
        return false;
      }
      setCatalog(next);
      rememberStoreCatalog(next);
      notifyStoreCatalogItems(next.items);
      setError("");
      setCatalogLoading(false);
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const kept = peekStoreCatalogCache();
      if (kept?.items?.length) {
        setCatalog(kept);
      } else {
        setCatalog({
          ok: false,
          error: msg,
          items: [],
        });
      }
      setError(msg);
      setCatalogLoading(false);
      return false;
    }
  }, []);

  /** Coalesce parallel install finish → one catalog refresh. */
  const refreshCatalogShared = useCallback(async (): Promise<boolean> => {
    if (sharedRefresh.current) return sharedRefresh.current;
    const run = refreshCatalog().finally(() => {
      if (sharedRefresh.current === run) sharedRefresh.current = null;
    });
    sharedRefresh.current = run;
    return run;
  }, [refreshCatalog]);

  /** Local disk truth for plugin enabled/installed — no network catalog round-trip. */
  const patchLocalPluginState = useCallback(async (): Promise<boolean> => {
    const api = getApi();
    if (!api || typeof api.list_uefn_plugins !== "function") return false;
    try {
      const res = await withTimeout(
        api.list_uefn_plugins(),
        STORE_MUTATION_TIMEOUT_MS,
        "list_uefn_plugins",
      );
      if (res.ok === false) return false;
      const byId = new Map(
        (res.plugins || [])
          .filter((p) => p?.id)
          .map((p) => [String(p.id).toLowerCase(), p] as const),
      );
      setCatalog((prev) => {
        if (!prev?.items?.length) return prev;
        let changed = false;
        const items = prev.items.map((it) => {
          if (itemKind(it) !== "plugin") return it;
          const slug = (it.slug || "").toLowerCase();
          if (!slug) return it;
          const next = patchItemFromLocalPlugin(it, byId.get(slug));
          if (next !== it) changed = true;
          return next;
        });
        if (!changed) return prev;
        const next: DuckyOSStoreCatalog = { ...prev, items };
        rememberStoreCatalog(next);
        notifyStoreCatalogItems(next.items);
        return next;
      });
      return true;
    } catch {
      return false;
    }
  }, []);

  /** Toolbar refresh: always hits the network, shows spinner, coalesces + cools down. */
  const manualRefreshCatalog = useCallback(async () => {
    if (catalogRefreshing || sharedRefresh.current) return;
    const now = Date.now();
    const elapsed = now - lastManualRefreshAt.current;
    if (lastManualRefreshAt.current > 0 && elapsed < MANUAL_REFRESH_COOLDOWN_MS) {
      const waitS = Math.ceil((MANUAL_REFRESH_COOLDOWN_MS - elapsed) / 1000);
      setError(`Wait ${waitS}s before refreshing again`);
      return;
    }
    lastManualRefreshAt.current = now;
    setError("");
    setCatalogRefreshing(true);
    // Always show loading on user refresh (cached catalogs used to skip this → dead button).
    setCatalogLoading(true);
    try {
      await refreshCatalogShared();
      // Disk installed/enabled flags — catalog versions alone can look stale after updates.
      await patchLocalPluginState();
    } finally {
      setCatalogRefreshing(false);
      setCatalogLoading(false);
    }
  }, [catalogRefreshing, refreshCatalogShared, patchLocalPluginState]);

  // After Update All (or any update queue) drains, refresh catalog once.
  useEffect(() => {
    setStoreInstallQueueIdleHandler(async () => {
      if (!takeStoreCatalogDirty()) return;
      const ok = await refreshCatalogShared();
      if (ok) setError("");
    });
    revealAllStoreJobToasts();
    // Jobs may have finished while Settings was closed — catch up catalog.
    // Don't steal the dirty flag if the exclusive queue is still draining.
    if (!isStoreInstallQueueActive() && takeStoreCatalogDirty()) {
      void refreshCatalogShared().then((ok) => {
        if (ok) setError("");
      });
    }
    return () => setStoreInstallQueueIdleHandler(null);
  }, [refreshCatalogShared]);

  useEffect(() => {
    return onApiReady((api) => {
      void refreshCatalog();
      void (async () => {
        try {
          if (typeof api.duckyos_get_status === "function") {
            setStatus(await api.duckyos_get_status());
          }
        } catch {
          /* ignore */
        }
      })();
    });
  }, [refreshCatalog]);

  // Store mounts after language swap — force Translation to re-apply chrome
  // (hero/cards are large; a single childList budget can miss labels).
  useEffect(() => {
    if (catalogLoading) return;
    const t = window.setTimeout(() => {
      window.dispatchEvent(
        new CustomEvent("uefn-translate-scope", { detail: { selector: ".store-tab" } }),
      );
    }, 40);
    return () => window.clearTimeout(t);
  }, [catalogLoading, catalog?.items?.length, query, categoryFilter]);

  // Re-fetch ownership when login state flips; keep showing cached catalog meanwhile.
  const loggedInPrev = useRef<boolean | null>(null);
  useEffect(() => {
    const loggedIn = Boolean(status?.logged_in);
    if (loggedInPrev.current === null) {
      loggedInPrev.current = loggedIn;
      return;
    }
    if (HIDDEN_BROWSE_CATEGORIES.has(categoryFilter)) setCategoryFilter("");
    if (loggedInPrev.current === loggedIn) return;
    loggedInPrev.current = loggedIn;
    if (!loggedIn && categoryFilter === OWNED_CATEGORY) setCategoryFilter("");
    void refreshCatalog();
  }, [status?.logged_in, refreshCatalog, categoryFilter]);

  useEffect(() => {
    if (!detailItem || !catalog?.items) return;
    const slug = detailItem.slug || "";
    const next = catalog.items.find((i) => i.slug === slug);
    if (next) setDetailItem(next);
  }, [catalog, detailItem?.slug]);

  // Website deep link (uefn-ducky://store/install/<slug>): once the catalog is
  // loaded, open the item's detail page and auto-start the install.
  const deepLinkCheck = useRef<() => void>(() => {});
  deepLinkCheck.current = () => {
    const items = catalog?.items || [];
    if (!items.length) return;
    const req = consumeStoreInstallRequest();
    if (!req) return;
    const item = items.find((i) => (i.slug || "").toLowerCase() === req.slug);
    if (!item) return;
    setActiveSection(null);
    setDetailItem(item);
    if (
      req.autoInstall &&
      (item.state === "available" || item.state === "update") &&
      !needsPurchase(item) &&
      !isStoreInstallBusy(item.slug || "")
    ) {
      void install(item);
    }
  };
  useEffect(() => {
    deepLinkCheck.current();
  }, [catalog]);
  useEffect(() => {
    const handler = () => deepLinkCheck.current();
    window.addEventListener("ducky:store-install", handler);
    return () => window.removeEventListener("ducky:store-install", handler);
  }, []);

  // Sidebar Plugins / LLMs "Open Store" may set a category while Store is already mounted.
  useEffect(() => {
    const applyNav = () => {
      const cat = consumeStoreCategoryRequest();
      if (!cat) return;
      setCategoryFilter(cat);
      setActiveSection(null);
      setDetailItem(null);
    };
    applyNav();
    window.addEventListener("ducky:store-navigate", applyNav);
    return () => window.removeEventListener("ducky:store-navigate", applyNav);
  }, []);

  // Keep Installed in sync with the sidebar Plugins list (enable/disable/install).
  // Local list_uefn_plugins first (disk truth), then best-effort network catalog.
  useEffect(() => {
    installPanelPushBus();
    let timer: number | undefined;
    const unsubscribe = subscribePanelPush((event) => {
      if (event.type !== "uefn_plugins_changed") return;
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = undefined;
        void (async () => {
          await patchLocalPluginState();
          await refreshCatalogShared();
        })();
      }, PLUGINS_CHANGED_DEBOUNCE_MS);
    });
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
      unsubscribe();
    };
  }, [patchLocalPluginState, refreshCatalogShared]);

  // Tell the Settings sidebar which plugin row to highlight on the Plugins page.
  useEffect(() => {
    const slug = (detailItem?.slug || "").trim().toLowerCase() || null;
    window.dispatchEvent(new CustomEvent("ducky:store-focus", { detail: { slug } }));
    return () => {
      window.dispatchEvent(new CustomEvent("ducky:store-focus", { detail: { slug: null } }));
    };
  }, [detailItem?.slug]);

  // Fresh layers always start scrolled to the top.
  useEffect(() => {
    if (detailItem) detailLayerRef.current?.scrollTo({ top: 0 });
  }, [detailItem?.slug]);
  useEffect(() => {
    if (activeSection) sectionLayerRef.current?.scrollTo({ top: 0 });
  }, [activeSection]);

  const install = async (item: DuckyOSStoreItemDto) => {
    const api = getApi();
    const slug = item.slug || "";
    const name = item.name || slug;
    const targetVer = item.latest_version || "";
    // Already on disk → update/re-download must not count as a new install.
    const isUpdate = item.state === "update" || item.installed_version != null;
    if (!api || (!api.duckyos_store_download && !api.bridge_job_start) || !slug) return;
    // Already downloading this slug — leave the existing job alone (survives remount).
    if (!beginStoreInstall(slug)) return;
    if (needsPurchase(item)) {
      endStoreInstall(slug);
      if (!status?.logged_in) {
        setError("Sign in with your DuckyOS account to buy paid Store items.");
        requestOpenSettings("Account");
        return;
      }
      setError("Purchase this item before installing.");
      return;
    }
    setError("");
    const base = { slug, name } as const;
    revealStoreJobToast(slug);
    // Claim slot immediately so Update All shows every item, but only one downloads.
    patchStoreJob(slug, {
      ...base,
      phase: "working",
      step: "download",
      queued: true,
      label: `Waiting to ${isUpdate ? "update" : "install"} ${name}…`,
    });
    try {
      await runStoreInstallExclusive(async () => {
        patchStoreJob(slug, {
          ...base,
          phase: "working",
          step: "download",
          queued: false,
          label: `Downloading ${name}${targetVer ? ` v${targetVer}` : ""}…`,
        });
        const { runBridgeJob } = await import("../../hooks/bridgeJobAsync");
        // isUpdate → Store must not bump install_count for already-installed packs.
        const result = await runBridgeJob<{
          ok?: boolean;
          version?: string;
          code?: string;
          error?: string;
        }>("duckyos_store_download", [slug, item.latest_version || "", isUpdate], 180_000);
        if (result.ok) {
          const ver = result.version || item.latest_version || "";
          patchStoreJob(slug, {
            ...base,
            phase: "working",
            step: "install",
            label: isUpdate
              ? `Applying update${ver ? ` v${ver}` : ""}…`
              : `Installing ${name}${ver ? ` v${ver}` : ""}…`,
          });
          // Brief beat so the Install step is visible before sync.
          await new Promise((r) => setTimeout(r, 280));
          // Updates: defer catalog refresh until the whole Update All queue drains
          // (N× refresh during Update All was freezing / crashing the WebView).
          // Fresh installs still refresh inline so enable + walkthrough can run.
          if (!isUpdate) {
            patchStoreJob(slug, {
              ...base,
              phase: "working",
              step: "sync",
              label: "Refreshing store…",
            });
            let ok = await refreshCatalogShared();
            if (!ok) {
              await new Promise((r) => setTimeout(r, 600));
              ok = await refreshCatalogShared();
            }
            if (ok) setError("");
            // New plugin installs must land enabled (backend does this for
            // default_enabled; retry here if the catalog still shows off).
            if (itemKind(item) === "plugin") {
              const apiNow = getApi();
              const cached = peekStoreCatalogCache();
              const next = cached?.items?.find((i) => i.slug === slug);
              if (next && !next.enabled && apiNow?.set_uefn_plugin_enabled) {
                await setEnabled(next, true);
              } else if (next?.enabled) {
                maybeStartPluginWalkthrough(slug);
              }
            }
          } else {
            markStoreCatalogDirty();
            patchStoreJob(slug, {
              ...base,
              phase: "working",
              step: "sync",
              label: "Applied — finishing queue…",
            });
            await new Promise((r) => setTimeout(r, 120));
          }
          clearStoreJobLater(slug, {
            ...base,
            phase: "done",
            step: "done",
            label: isUpdate
              ? `Updated${ver ? ` to v${ver}` : ""}`
              : `Installed${ver ? ` v${ver}` : ""}`,
          });
        } else if (result.code === "purchase_required") {
          clearStoreJobLater(slug, {
            ...base,
            phase: "error",
            step: "download",
            label: result.error || "Purchase required",
          });
          setError(result.error || "Purchase required");
        } else {
          clearStoreJobLater(slug, {
            ...base,
            phase: "error",
            step: "download",
            label: result.error || "Install failed",
          });
        }
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      clearStoreJobLater(slug, {
        ...base,
        phase: "error",
        step: "download",
        label: msg,
      });
    } finally {
      endStoreInstall(slug);
    }
  };

  const buy = async (item: DuckyOSStoreItemDto) => {
    const api = getApi();
    const slug = item.slug || "";
    if (!api || typeof api.duckyos_store_checkout !== "function" || !slug) return;
    if (!status?.logged_in) {
      setError("Sign in with your DuckyOS account to buy paid Store items.");
      requestOpenSettings("Account");
      return;
    }
    setActionBusySlug(slug, true);
    setError("");
    try {
      const result = await api.duckyos_store_checkout(slug);
      if (result.ok && result.url) {
        // Poll ownership a few times after the user returns.
        for (let i = 0; i < 8; i++) {
          await new Promise((r) => setTimeout(r, 2500));
          const ok = await refreshCatalog();
          if (!ok) continue;
          const next = (await api.duckyos_store_catalog?.())?.items?.find((x) => x.slug === slug);
          if (next?.owned) break;
        }
      } else {
        setError(result.error || "Checkout failed");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionBusySlug(slug, false);
    }
  };

  const patchItemEnabled = useCallback((slug: string, enabled: boolean) => {
    setCatalog((prev) => {
      if (!prev?.items?.length) return prev;
      const next: DuckyOSStoreCatalog = {
        ...prev,
        items: prev.items.map((it) => (it.slug === slug ? { ...it, enabled } : it)),
      };
      rememberStoreCatalog(next);
      notifyStoreCatalogItems(next.items);
      return next;
    });
  }, []);

  const setEnabled = async (item: DuckyOSStoreItemDto, enabled: boolean, trustLocal = false) => {
    const api = getApi();
    const slug = item.slug || "";
    if (!api || !slug) return;
    const isSkill = itemKind(item) === "skill";
    const setSkillEnabled = api.set_skill_pack_enabled?.bind(api);
    const setPluginEnabled = api.set_uefn_plugin_enabled?.bind(api);
    if (isSkill) {
      if (!setSkillEnabled) return;
    } else if (!setPluginEnabled) {
      return;
    }
    const prevEnabled = Boolean(item.enabled);
    const isAi = (item.source || "") === "ai";
    // Optimistic — don't wait on a full Store catalog network refresh.
    patchItemEnabled(slug, enabled);
    setActionBusySlug(slug, true);
    setError("");
    try {
      if (isSkill && setSkillEnabled) {
        const result = await withTimeout(
          setSkillEnabled(slug, enabled),
          STORE_MUTATION_TIMEOUT_MS,
          "Enable/disable skill",
        );
        if (!result.ok) {
          patchItemEnabled(slug, prevEnabled);
          setError(result.error || "Update failed");
        }
        return;
      }
      if (!setPluginEnabled) return;
      const result = await withTimeout(
        setPluginEnabled(slug, enabled, trustLocal),
        STORE_MUTATION_TIMEOUT_MS,
        "Enable/disable plugin",
      );
      if (result.needs_trust) {
        patchItemEnabled(slug, prevEnabled);
        const ok = await confirm({
          title: isAi ? "Enable AI-made plugin?" : "Enable local plugin?",
          message:
            result.error ||
            `Unofficial ${isAi ? "AI-made" : "local"} plugin — enable "${item.name || slug}" anyway? It runs with app permissions.`,
          confirmLabel: "Enable",
          danger: true,
        });
        if (ok) {
          patchItemEnabled(slug, true);
          const trusted = await withTimeout(
            setPluginEnabled(slug, true, true),
            STORE_MUTATION_TIMEOUT_MS,
            "Enable/disable plugin",
          );
          if (trusted.ok) {
            if (enabled) maybeStartPluginWalkthrough(slug);
          } else {
            patchItemEnabled(slug, prevEnabled);
            setError(trusted.error || "Enable failed");
          }
        }
      } else if (result.ok) {
        if (enabled) maybeStartPluginWalkthrough(slug);
      } else {
        patchItemEnabled(slug, prevEnabled);
        setError(result.error || "Update failed");
      }
    } catch (err) {
      patchItemEnabled(slug, prevEnabled);
      setError(err instanceof Error ? err.message : String(err));
      void patchLocalPluginState();
    } finally {
      setActionBusySlug(slug, false);
      if (!isSkill) void patchLocalPluginState();
    }
  };

  const setEnabledRef = useRef(setEnabled);
  setEnabledRef.current = setEnabled;
  const allItemsRef = useRef<DuckyOSStoreItemDto[]>([]);

  const runTrustPrompt = useCallback((req: PluginTrustRequest) => {
    const slug = req.pluginId;
    if (!slug) return;
    const found = allItemsRef.current.find((i) => i.slug === slug);
    const item: DuckyOSStoreItemDto = found || {
      slug,
      name: slug,
      kind: "plugin",
      state: "installed",
      source: req.source || "ai",
      enabled: false,
    };
    void setEnabledRef.current(item, true, false);
  }, []);

  // Agent enable → App queues trust request + opens Store; drain here.
  useEffect(() => {
    const pending = takePluginTrustRequest();
    if (pending) runTrustPrompt(pending);
    return subscribePluginTrustRequest(runTrustPrompt);
  }, [runTrustPrompt]);

  const openUninstall = async (item: DuckyOSStoreItemDto) => {
    const api = getApi();
    const slug = item.slug || "";
    if (!api || !slug) return;
    const isSkill = itemKind(item) === "skill";
    if (isSkill && !api.delete_skill_pack) return;
    if (!isSkill && !api.uninstall_uefn_plugin) return;
    setEraseData(false);
    setUninstallLabels([]);
    setUninstallItem(item);
    if (!isSkill && typeof api.get_uefn_plugin_secret_labels === "function") {
      try {
        const info = await api.get_uefn_plugin_secret_labels(slug);
        if (info.ok !== false) setUninstallLabels(Array.isArray(info.labels) ? info.labels : []);
      } catch {
        setUninstallLabels([]);
      }
    }
  };

  const confirmUninstall = async () => {
    const api = getApi();
    const item = uninstallItem;
    const slug = item?.slug || "";
    if (!api || !item || !slug) return;
    const isSkill = itemKind(item) === "skill";
    const deleteSkill = api.delete_skill_pack?.bind(api);
    const uninstallPlugin = api.uninstall_uefn_plugin?.bind(api);
    if (isSkill) {
      if (!deleteSkill) return;
    } else if (!uninstallPlugin) {
      return;
    }
    setUninstallBusy(true);
    setError("");
    try {
      let result: { ok?: boolean; error?: string };
      if (isSkill && deleteSkill) {
        result = await withTimeout(
          deleteSkill(slug),
          STORE_MUTATION_TIMEOUT_MS,
          "Uninstall skill",
        );
      } else if (uninstallPlugin) {
        result = await withTimeout(
          uninstallPlugin(slug, eraseData),
          STORE_MUTATION_TIMEOUT_MS,
          "Uninstall plugin",
        );
      } else {
        return;
      }
      if (result.ok) {
        setUninstallItem(null);
        if (detailItem?.slug === slug) setDetailItem(null);
        // Disk is already cleared — patch local flags immediately; network catalog is best-effort.
        await patchLocalPluginState();
        void refreshCatalogShared();
      } else {
        setError(result.error || "Uninstall failed");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      void patchLocalPluginState();
    } finally {
      setUninstallBusy(false);
    }
  };

  const installFromFile = async (file: File | null) => {
    if (!file) return;
    const api = getApi();
    if (!api?.install_uefn_plugin_bytes) {
      setError("Plugin install API unavailable — restart the control panel");
      return;
    }
    setActionBusySlug("__local__", true);
    setError("");
    try {
      const buf = await file.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let binary = "";
      for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]!);
      const b64 = btoa(binary);
      const result = await api.install_uefn_plugin_bytes(b64, "local");
      if (result.ok) {
        await refreshCatalog();
      } else {
        const err = result.error || "Local install failed";
        const corrupt = /crc|corrupt|invalid zip/i.test(err);
        setError(
          corrupt
            ? `${err} — nothing was installed. If a broken copy still appears below, Uninstall it, then retry with a fresh zip.`
            : err,
        );
        await refreshCatalog();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionBusySlug("__local__", false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const openPluginsFolder = () => {
    void (async () => {
      const api = getApi();
      if (!api?.open_uefn_plugins_folder) {
        setError("Plugins folder API unavailable — restart the control panel");
        return;
      }
      try {
        await api.open_uefn_plugins_folder();
        setError("");
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
  };

  const allItems = useMemo(() => catalog?.items || [], [catalog]);
  allItemsRef.current = allItems;
  const sections = useMemo(() => deriveSections(allItems), [allItems]);
  const slides = useMemo(() => deriveHeroSlides(allItems), [allItems]);
  const filtering = Boolean(query.trim() || categoryFilter);
  const filteredItems = useMemo(
    () => filterStoreItems(allItems, { q: query, category: categoryFilter }),
    [allItems, query, categoryFilter],
  );
  const categoryOptions = useMemo(
    () =>
      Array.from(
        new Set([...CORE_STORE_CATEGORIES, ...allItems.flatMap((i) => itemCategories(i))]),
      )
        .filter(
          (c) =>
            c !== INSTALLED_CATEGORY &&
            c !== AI_MADE_CATEGORY &&
            c !== OWNED_CATEGORY &&
            !HIDDEN_BROWSE_CATEGORIES.has(c),
        )
        .sort(),
    [allItems],
  );
  const sectionData = activeSection ? sections.find((s) => s.key === activeSection) : undefined;
  const loggedIn = Boolean(status?.logged_in);
  const view = detailItem ? "detail" : activeSection ? "section" : "main";
  const showSkeleton = catalogLoading && allItems.length === 0;

  // First paint of real cards: stagger reveal (skip when hydrating from cache).
  useEffect(() => {
    if (!allItems.length || sawItemsRef.current) return;
    sawItemsRef.current = true;
    setStaggerCards(true);
    const t = window.setTimeout(() => setStaggerCards(false), 900);
    return () => window.clearTimeout(t);
  }, [allItems.length]);

  const storeNavLoc = useMemo<SettingsNavLocation>(() => {
    const slug = detailItem?.slug || null;
    const section = activeSection;
    if (slug) {
      return {
        kind: "settings",
        tab: "Store",
        drill: { type: "store", section, slug },
        name: detailItem?.name || slug,
      };
    }
    if (section) {
      return {
        kind: "settings",
        tab: "Store",
        drill: { type: "store", section, slug: null },
        name: sectionData?.title || section,
      };
    }
    return { kind: "settings", tab: "Store", name: "Store" };
  }, [detailItem, activeSection, sectionData?.title]);
  useRecordStoreSettingsLocation(storeNavLoc);

  const storeDetailParentLoc = useMemo<SettingsNavLocation>(() => {
    if (activeSection) {
      return {
        kind: "settings",
        tab: "Store",
        drill: { type: "store", section: activeSection, slug: null },
        name: sectionData?.title || activeSection,
      };
    }
    return { kind: "settings", tab: "Store", name: "Store" };
  }, [activeSection, sectionData?.title]);
  const storeSectionParentLoc = useMemo<SettingsNavLocation>(
    () => ({ kind: "settings", tab: "Store", name: "Store" }),
    [],
  );

  const pendingStoreSlug = useRef<string | null>(null);
  const applyStoreDrill = useCallback((loc: SettingsNavLocation) => {
    const drill = loc.drill?.type === "store" ? loc.drill : null;
    if (!drill || (!drill.section && !drill.slug)) {
      setDetailItem(null);
      setActiveSection(null);
      pendingStoreSlug.current = null;
      return;
    }
    setActiveSection(drill.section);
    if (!drill.slug) {
      setDetailItem(null);
      pendingStoreSlug.current = null;
      return;
    }
    pendingStoreSlug.current = drill.slug;
    const item = allItems.find((i) => i.slug === drill.slug);
    if (item) {
      setDetailItem(item);
      pendingStoreSlug.current = null;
    } else {
      setDetailItem(null);
    }
  }, [allItems]);
  useApplySettingsDrill("Store", applyStoreDrill);

  useEffect(() => {
    const slug = pendingStoreSlug.current;
    if (!slug || !allItems.length) return;
    const item = allItems.find((i) => i.slug === slug);
    if (!item) return;
    setDetailItem(item);
    pendingStoreSlug.current = null;
  }, [allItems]);

  const closeStoreDetail = useCallback(() => setDetailItem(null), []);
  const closeStoreSection = useCallback(() => setActiveSection(null), []);
  const backFromDetail = useStoreSettingsLayerBack(storeDetailParentLoc, closeStoreDetail);
  const backFromSection = useStoreSettingsLayerBack(storeSectionParentLoc, closeStoreSection);

  const handlers: StoreItemHandlers = {
    onInstall: (item) => void install(item),
    onBuy: (item) => void buy(item),
    onToggle: (item) => void setEnabled(item, !item.enabled),
    onUninstall: (item) => void openUninstall(item),
  };

  const updateCandidates = useMemo(
    () =>
      allItems.filter(
        (item) =>
          (item.state || "") === "update" &&
          !needsPurchase(item) &&
          Boolean(item.slug) &&
          !isStoreInstallBusy(item.slug || "") &&
          jobs[item.slug || ""]?.phase !== "working",
      ),
    [allItems, jobs],
  );

  /** In-flight / queued Update All items — always shown in a Pending row. */
  const pendingItems = useMemo(() => {
    const working = Object.values(jobs).filter((j) => j.phase === "working");
    const out: DuckyOSStoreItemDto[] = [];
    for (const job of working) {
      const found = allItems.find((i) => i.slug === job.slug);
      if (found) {
        out.push(found);
        continue;
      }
      // Catalog lag / reload — still show a card so Pending isn't empty.
      out.push({
        slug: job.slug,
        name: job.name || job.slug,
        kind: "plugin",
        state: "update",
        source: "store",
      });
    }
    return out;
  }, [jobs, allItems]);

  const updateAll = () => {
    // Snapshot once — install() mutates busy flags as it claims each slug.
    const batch = updateCandidates.slice();
    for (const item of batch) {
      void install(item);
    }
  };

  // WebView reload mid–Update All: sessionStorage restored overlays, but the
  // JS exclusive chain died — re-queue orphans one-by-one.
  const installRef = useRef(install);
  installRef.current = install;
  useEffect(() => {
    if (!allItems.length) return;
    reclaimOrphanStoreJobs((slug) => {
      const item = allItems.find((i) => i.slug === slug);
      if (item) void installRef.current(item);
    });
  }, [allItems]);

  const updateAllButton =
    updateCandidates.length > 0 ? (
      <button
        type="button"
        className="ds-update-all"
        onClick={(e) => {
          e.stopPropagation();
          updateAll();
        }}
        title={`Update ${updateCandidates.length} item${updateCandidates.length === 1 ? "" : "s"}`}
      >
        Update All
        {updateCandidates.length > 1 ? (
          <span className="ds-update-all-count">{updateCandidates.length}</span>
        ) : null}
      </button>
    ) : null;

  const renderCard = (item: DuckyOSStoreItemDto, index = 0) => {
    const slug = item.slug || "";
    return (
      <div
        className={staggerCards ? "ds-card-enter" : undefined}
        style={staggerCards ? { animationDelay: `${Math.min(index, 12) * 45}ms` } : undefined}
      >
        <StoreCard
          item={item}
          busy={jobs[slug] ?? null}
          actionBusy={Boolean(actionBusy[slug] || actionBusy.__local__ || jobs[slug])}
          handlers={handlers}
          onOpen={setDetailItem}
        />
      </div>
    );
  };

  return (
    <div
      ref={storeRootRef}
      className="store-tab"
      data-view={view}
      data-section-open={activeSection ? "1" : "0"}
    >
      {error ? (
        <div className="ds-error" role="alert">
          {error}
        </div>
      ) : null}

      {/* MAIN LAYER — hero, controls, rows / filtered grid */}
      <div ref={storeCatalogRef} className="ds-layer ds-layer--main">
        {showSkeleton ? (
          <>
            <StoreHeroSkeleton />
            <div className="ds-controls ds-controls--skeleton" aria-hidden>
              <div className="ds-skel ds-skel--search" />
              <div className="ds-skel ds-skel--cat" />
              <div className="ds-skel ds-skel--tools" />
            </div>
            <StoreSkeletonRows />
          </>
        ) : allItems.length === 0 ? (
          <div className="ds-empty">
            <span className="ds-empty-icon" aria-hidden>
              <Icons.Box />
            </span>
            <p>
              No published items yet. Staff can upload packs in Admin → Ducky Store. You can also
              install a local plugin zip.
            </p>
            <button
              type="button"
              className="ds-link"
              onClick={() => fileRef.current?.click()}
            >
              Install from file…
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".zip,.ducky-plugin,application/zip"
              hidden
              onChange={(e) => void installFromFile(e.target.files?.[0] ?? null)}
            />
          </div>
        ) : (
          <>
            {slides.length ? <StoreHero slides={slides} onOpen={setDetailItem} /> : null}

            <div className="ds-controls">
              <div className="ds-search">
                <span className="ds-search-icon" aria-hidden>
                  <Icons.Search />
                </span>
                <input
                  type="search"
                  className="ds-search-input"
                  placeholder="Search plugins, skills, tags…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  aria-label="Search store"
                />
              </div>
              <div className="ds-controls-sep" aria-hidden />
              <ChoiceDropdown
                className="ds-cat"
                triggerClassName="ds-cat-trigger"
                size="compact"
                aria-label="Filter by category"
                mode="radio"
                value={categoryFilter}
                options={[
                  { value: "", label: "All Categories" },
                  ...(loggedIn ? [{ value: OWNED_CATEGORY, label: "Owned" }] : []),
                  { value: INSTALLED_CATEGORY, label: "Installed" },
                  { value: AI_MADE_CATEGORY, label: "AI-made" },
                  ...categoryOptions.map((c) => ({ value: c, label: categoryLabel(c) })),
                ]}
                onChange={setCategoryFilter}
              />
              <div className="ds-controls-sep" aria-hidden />
              <div className="ds-tools">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".zip,.ducky-plugin,application/zip"
                  hidden
                  onChange={(e) => void installFromFile(e.target.files?.[0] ?? null)}
                />
                <button
                  type="button"
                  className="ds-tool"
                  title="Install from file…"
                  aria-label="Install from file"
                  disabled={Boolean(actionBusy.__local__)}
                  onClick={() => fileRef.current?.click()}
                >
                  <Icons.Upload />
                </button>
                <button
                  type="button"
                  className="ds-tool"
                  title="Open plugins folder"
                  aria-label="Open plugins folder"
                  onClick={openPluginsFolder}
                >
                  <Icons.Folder />
                </button>
                <button
                  type="button"
                  className={`ds-tool${catalogRefreshing ? " is-spinning" : ""}`}
                  title={catalogRefreshing ? "Refreshing…" : "Refresh catalog"}
                  aria-label="Refresh catalog"
                  disabled={catalogRefreshing || catalogLoading}
                  onClick={() => {
                    void manualRefreshCatalog();
                  }}
                >
                  <Icons.Refresh />
                </button>
              </div>
            </div>

            {!loggedIn ? (
              <p className="ds-signin">
                Free items work without an account.{" "}
                <button
                  type="button"
                  className="ds-link"
                  onClick={() => requestOpenSettings("Account")}
                >
                  Sign in
                </button>{" "}
                to buy paid items and sync purchases across machines.
              </p>
            ) : null}

            {filtering ? (
              filteredItems.length > 0 ? (
                <div className="ds-grid">
                  {filteredItems.map((item, i) => (
                    <div key={item.slug || item.name || ""}>{renderCard(item, i)}</div>
                  ))}
                </div>
              ) : (
                <div className="ds-empty">
                  <span className="ds-empty-icon" aria-hidden>
                    <Icons.Box />
                  </span>
                  <p>No items match these filters.</p>
                  <button
                    type="button"
                    className="ds-link"
                    onClick={() => {
                      setQuery("");
                      setCategoryFilter("");
                    }}
                  >
                    Clear filters
                  </button>
                </div>
              )
            ) : (
              <div className="ds-rows">
                {pendingItems.length > 0 ? (
                  <StoreRow
                    key="pending"
                    title={`Pending (${pendingItems.length})`}
                    onOpenSection={() => setActiveSection("installed")}
                  >
                    {pendingItems.map((item, i) => (
                      <div key={`pending-${item.slug || item.name || i}`} className="ds-row-item">
                        {renderCard(item, i)}
                      </div>
                    ))}
                  </StoreRow>
                ) : null}
                {sections.map((section) => (
                  <StoreRow
                    key={section.key}
                    title={section.title}
                    onOpenSection={() => setActiveSection(section.key)}
                    headerAction={section.key === "installed" ? updateAllButton : null}
                  >
                    {section.items.map((item, i) => (
                      <div key={item.slug || item.name || ""} className="ds-row-item">
                        {renderCard(item, i)}
                      </div>
                    ))}
                  </StoreRow>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* SECTION LAYER — paginated grid for one row */}
      <div className="ds-layer ds-layer--section" ref={sectionLayerRef}>
        {activeSection ? (
          <StoreSectionView
            key={activeSection}
            title={sectionData?.title || activeSection}
            items={sectionData?.items || []}
            jobs={jobs}
            actionBusy={actionBusy}
            handlers={handlers}
            onBack={backFromSection}
            onOpen={setDetailItem}
            headerAction={activeSection === "installed" ? updateAllButton : null}
            layerRef={sectionLayerRef}
          />
        ) : null}
      </div>

      {/* DETAIL LAYER — slide-in item page */}
      <div className="ds-layer ds-layer--detail" ref={detailLayerRef}>
        <StoreDetailView
          item={detailItem}
          jobs={jobs}
          actionBusy={actionBusy}
          handlers={handlers}
          onBack={backFromDetail}
        />
      </div>

      <StoreJobStack
        jobs={Object.values(jobs).filter((j) => !hiddenToasts[j.slug])}
        onOpen={(slug) => {
          const item = allItems.find((i) => i.slug === slug);
          if (item) {
            setActiveSection(null);
            setDetailItem(item);
          }
        }}
        onDismiss={(slug) => {
          // In-flight: hide toast only (card overlay keeps progress). Done/error: drop job.
          if (jobs[slug]?.phase === "working") {
            hideStoreJobToast(slug);
            return;
          }
          patchStoreJob(slug, null);
        }}
        onDismissAll={dismissAllStoreJobToasts}
      />

      <Modal
        open={Boolean(uninstallItem)}
        onClose={() => {
          if (!uninstallBusy) setUninstallItem(null);
        }}
        title={`Uninstall ${uninstallItem?.name || uninstallItem?.slug || "item"}?`}
        width={440}
        footer={
          <div className="ds-modal-footer">
            <button
              type="button"
              className="ds-btn ds-btn--plain ds-btn--card"
              disabled={uninstallBusy}
              onClick={() => setUninstallItem(null)}
            >
              Cancel
            </button>
            <button
              type="button"
              className={`ds-btn ds-btn--card ${eraseData ? "ds-btn--danger" : "ds-btn--uninstall"}`}
              disabled={uninstallBusy}
              onClick={() => void confirmUninstall()}
            >
              {uninstallBusy ? "Uninstalling…" : "Uninstall"}
            </button>
          </div>
        }
      >
        {uninstallItem ? (
          <div className="ds-uninstall">
            <p>
              {itemKind(uninstallItem) === "skill"
                ? "This removes the skill pack from this PC. You can reinstall it from the Store anytime."
                : "This removes the plugin package from this PC."}
            </p>
            {itemKind(uninstallItem) === "plugin" ? (
              <>
                {uninstallLabels.length > 0 ? (
                  <>
                    <p className="ds-uninstall-label">Saved for this plugin:</p>
                    <ul>
                      {uninstallLabels.map((label) => (
                        <li key={label}>{label}</li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <p className="ds-muted">This plugin has no saved login or API keys.</p>
                )}
                <div className="ds-uninstall-choices" role="radiogroup" aria-label="Saved data">
                  <label className="ds-uninstall-choice">
                    <input
                      type="radio"
                      name="erase-data"
                      checked={!eraseData}
                      disabled={uninstallBusy}
                      onChange={() => setEraseData(false)}
                    />
                    <span>
                      <strong>Keep saved data</strong>
                      <span className="ds-uninstall-hint">
                        Keep the items above. If you reinstall later, you will still be signed in /
                        configured.
                      </span>
                    </span>
                  </label>
                  <label className="ds-uninstall-choice ds-uninstall-choice--danger">
                    <input
                      type="radio"
                      name="erase-data"
                      checked={eraseData}
                      disabled={uninstallBusy}
                      onChange={() => setEraseData(true)}
                    />
                    <span>
                      <strong>Erase saved data</strong>
                      <span className="ds-uninstall-hint">
                        Permanently erase the items above from this PC. You will need to sign in /
                        paste tokens again after reinstall. This cannot be undone.
                      </span>
                    </span>
                  </label>
                </div>
              </>
            ) : null}
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
