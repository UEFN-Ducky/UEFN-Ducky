/**
 * Browser → app deep links (uefn-ducky://…), e.g. a site plugin's
 * "Open in UEFN Ducky" button. Any Store slug works — not just Account.
 *
 * Supported forms:
 *   uefn-ducky://login                     → Settings → Account + start pairing
 *   uefn-ducky://store                     → open Settings → Store
 *   uefn-ducky://store/<slug>              → Plugins details for that Store item
 *   uefn-ducky://plugin/<slug>             → same (alias for plugin authors)
 *   uefn-ducky://store/install/<slug>      → open detail + auto-install
 */
import { onApiReady } from "../hooks/onApiReady";
import { emitAppHook } from "../sfx/appHooks";
import { requestOpenSettings } from "./openSettingsTab";

export const ACCOUNT_LOGIN_EVENT = "ducky:account-login";
export const ACCOUNT_LOGIN_KEY = "uefn-account-login";

export function parseLoginDeepLink(raw: string): boolean {
  return /^uefn-ducky:\/\/login\/?(?:[?#].*)?$/i.test(String(raw || "").trim());
}

export function consumeAccountLoginRequest(): boolean {
  try {
    const raw = sessionStorage.getItem(ACCOUNT_LOGIN_KEY) || "";
    if (!raw) return false;
    sessionStorage.removeItem(ACCOUNT_LOGIN_KEY);
    return true;
  } catch {
    return false;
  }
}

/** sessionStorage handshake consumed by StoreTab once the catalog is loaded. */
export const STORE_INSTALL_KEY = "uefn-store-install";

export type StoreDeepLink = {
  slug: string;
  autoInstall: boolean;
};

export function parseStoreDeepLink(raw: string): StoreDeepLink | null {
  const url = String(raw || "").trim();
  const match = url.match(
    /^uefn-ducky:\/\/(?:store|plugin)(?:\/(install)\/?)?(?:\/?([a-z0-9][a-z0-9._-]*))?\/?(?:[?#].*)?$/i,
  );
  if (!match) return null;
  return {
    slug: (match[2] || "").toLowerCase(),
    autoInstall: Boolean(match[1]),
  };
}

export function handleDeepLink(raw: string): boolean {
  if (parseLoginDeepLink(raw)) {
    try {
      sessionStorage.setItem(ACCOUNT_LOGIN_KEY, "1");
    } catch {
      /* ignore */
    }
    requestOpenSettings("Account");
    window.dispatchEvent(new CustomEvent(ACCOUNT_LOGIN_EVENT));
    return true;
  }
  const parsed = parseStoreDeepLink(raw);
  if (!parsed) return false;
  requestOpenStore({
    slug: parsed.slug || undefined,
    autoInstall: parsed.autoInstall,
  });
  return true;
}

export function peekStoreInstallRequest(): StoreDeepLink | null {
  let raw = "";
  try {
    raw = sessionStorage.getItem(STORE_INSTALL_KEY) || "";
  } catch {
    return null;
  }
  if (!raw.trim()) return null;
  try {
    const parsed = JSON.parse(raw) as { slug?: string; install?: boolean };
    const slug = String(parsed.slug || "").toLowerCase();
    if (!slug) return null;
    return { slug, autoInstall: Boolean(parsed.install) };
  } catch {
    return null;
  }
}

export function consumeStoreInstallRequest(): StoreDeepLink | null {
  const parsed = peekStoreInstallRequest();
  if (!parsed) return null;
  try {
    sessionStorage.removeItem(STORE_INSTALL_KEY);
  } catch {
    /* ignore */
  }
  return parsed;
}

/** Pending category filter for Store (e.g. Installed) — consumed by StoreTab. */
export const STORE_CATEGORY_KEY = "uefn-store-category";

export function consumeStoreCategoryRequest(): string | null {
  let raw = "";
  try {
    raw = sessionStorage.getItem(STORE_CATEGORY_KEY) || "";
  } catch {
    return null;
  }
  const cat = raw.trim().toLowerCase();
  if (!cat) return null;
  try {
    sessionStorage.removeItem(STORE_CATEGORY_KEY);
  } catch {
    /* ignore */
  }
  return cat;
}

/**
 * Open Settings → Store (Plugins page). Optional slug opens that item's detail;
 * optional category applies the browse filter (e.g. "installed").
 */
export function requestOpenStore(opts?: {
  slug?: string;
  category?: string;
  autoInstall?: boolean;
}): void {
  const slug = String(opts?.slug || "")
    .trim()
    .toLowerCase();
  const category = String(opts?.category || "")
    .trim()
    .toLowerCase();
  try {
    if (slug) {
      sessionStorage.setItem(
        STORE_INSTALL_KEY,
        JSON.stringify({ slug, install: Boolean(opts?.autoInstall) }),
      );
    }
    if (category) {
      sessionStorage.setItem(STORE_CATEGORY_KEY, category);
    }
  } catch {
    /* ignore */
  }
  requestOpenSettings("Store", slug ? { storeSlug: slug } : undefined);
  if (slug) emitAppHook("store.opened", { slug, autoInstall: Boolean(opts?.autoInstall) });
  window.dispatchEvent(new CustomEvent("ducky:store-install"));
  window.dispatchEvent(new CustomEvent("ducky:store-navigate"));
}

/** Install the live listener + drain the cold-start queue. Returns cleanup. */
export function installDeepLinkListeners(): () => void {
  const onEvent = (e: Event) => {
    const links = (e as CustomEvent<{ links?: string[] }>).detail?.links;
    if (!Array.isArray(links)) return;
    for (const link of links) handleDeepLink(link);
  };
  window.addEventListener("ducky:deep-link", onEvent);
  const stopApi = onApiReady((api) => {
    void (async () => {
      try {
        const pending = await api.consume_pending_deep_links?.();
        if (Array.isArray(pending)) {
          for (const link of pending) handleDeepLink(link);
        }
      } catch {
        /* ignore */
      }
    })();
  });
  return () => {
    window.removeEventListener("ducky:deep-link", onEvent);
    stopApi();
  };
}
