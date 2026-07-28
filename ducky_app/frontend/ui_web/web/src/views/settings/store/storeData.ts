import type { DuckyOSStoreItemDto } from "../../../types/panel";
import {
  categoryLabel,
  HIDDEN_BROWSE_CATEGORIES,
  isOwnedPurchase,
  itemCategories,
  itemKind,
} from "../storeFilters";

/** One horizontally-scrolling row on the Store landing view. */
export type StoreSection = {
  key: string;
  title: string;
  items: DuckyOSStoreItemDto[];
};

export type HeroVariant = "update" | "emerald" | "blue" | "purple";

/** Install pipeline steps shown on card + detail overlays. */
export const INSTALL_STEPS = [
  { id: "download", label: "Downloading pack" },
  { id: "install", label: "Installing" },
  { id: "sync", label: "Refreshing store" },
  { id: "done", label: "Done" },
] as const;

export type InstallStepId = (typeof INSTALL_STEPS)[number]["id"];

/** Per-card install/update progress marker (owned by storeInstallJobs module). */
export type CardBusy = {
  slug: string;
  label: string;
  phase: "working" | "done" | "error";
  step: InstallStepId;
  /** Display name for the floating job stack. */
  name?: string;
  /** Waiting for the exclusive Store download slot (Update All). */
  queued?: boolean;
};

export function installStepIndex(step: InstallStepId): number {
  const i = INSTALL_STEPS.findIndex((s) => s.id === step);
  return i < 0 ? 0 : i;
}

/** Determinate bar % for the active step (done/error = 100). */
export function installProgressPct(busy: CardBusy): number {
  if (busy.phase === "done" || busy.phase === "error" || busy.step === "done") return 100;
  // Queued behind another Update All download — not "stuck at 14%".
  if (busy.queued) return 4;
  const i = installStepIndex(busy.step);
  return Math.round(((i + 0.55) / INSTALL_STEPS.length) * 100);
}

export type HeroSlide = {
  item: DuckyOSStoreItemDto;
  tag: string;
  variant: HeroVariant;
};

export const SECTION_PAGE_SIZE = 12;
/** Infinite-scroll / Show more batch — never dump the full list into the DOM at once. */
export const SCROLL_BATCH = 50;
export const PAGE_SIZE_OPTIONS = [12, 24, 50] as const;
export type PageSizeChoice = (typeof PAGE_SIZE_OPTIONS)[number] | "all";

export function parsePageSizeChoice(raw: string): PageSizeChoice {
  if (raw === "all") return "all";
  const n = Number(raw);
  return (PAGE_SIZE_OPTIONS as readonly number[]).includes(n)
    ? (n as (typeof PAGE_SIZE_OPTIONS)[number])
    : SECTION_PAGE_SIZE;
}

/** Fixed browse-category rows, in display order. Extra categories append after. */
const CATEGORY_ROWS: ReadonlyArray<readonly [string, string]> = [
  ["gateways", "Gateways"],
  ["skills", "Skills"],
  ["themes", "Themes"],
  ["games", "Games"],
  ["mcps", "MCPs"],
  ["3d", "3D"],
];

export function installCount(item: DuckyOSStoreItemDto): number {
  return typeof item.install_count === "number" && isFinite(item.install_count)
    ? item.install_count
    : -1;
}

export function isInstalled(item: DuckyOSStoreItemDto): boolean {
  return item.installed_version != null;
}

/** Paid cloud purchase for the signed-in account (excludes local unpaid installs). */
export function isOwned(item: DuckyOSStoreItemDto): boolean {
  return isOwnedPurchase(item);
}

/**
 * Derive the landing rows from the raw catalog:
 * Gateways first (everything is a plugin — no Plugins row), then
 * Trending → Owned → Installed → other browse categories.
 */
export function deriveSections(items: DuckyOSStoreItemDto[]): StoreSection[] {
  const sections: StoreSection[] = [];
  if (!items.length) return sections;

  const byCategory = new Map<string, DuckyOSStoreItemDto[]>();
  for (const item of items) {
    for (const cat of itemCategories(item)) {
      if (HIDDEN_BROWSE_CATEGORIES.has(cat)) continue;
      const list = byCategory.get(cat);
      if (list) list.push(item);
      else byCategory.set(cat, [item]);
    }
  }

  // Gateways lead — LLM / coding-agent entry points before the rest of the catalog.
  const gateways = byCategory.get("gateways");
  if (gateways?.length) sections.push({ key: "gateways", title: "Gateways", items: gateways });

  const trending = [...items]
    .filter((i) => installCount(i) > 0)
    .sort((a, b) => installCount(b) - installCount(a))
    .slice(0, 10);
  if (trending.length) sections.push({ key: "trending", title: "Trending", items: trending });

  const owned = items.filter(isOwned);
  if (owned.length) sections.push({ key: "owned", title: "Owned", items: owned });

  const installed = items.filter(isInstalled);
  if (installed.length) sections.push({ key: "installed", title: "Installed", items: installed });

  const seen = new Set<string>(["gateways"]);
  for (const [key, title] of CATEGORY_ROWS) {
    if (seen.has(key)) continue;
    const list = byCategory.get(key);
    if (list?.length) {
      sections.push({ key, title, items: list });
      seen.add(key);
    }
  }
  const extras = [...byCategory.keys()].filter((k) => !seen.has(k)).sort();
  for (const key of extras) {
    sections.push({ key, title: categoryLabel(key), items: byCategory.get(key)! });
  }
  return sections;
}

/** Items for one section key — used by the paginated section view. */
export function sectionItems(items: DuckyOSStoreItemDto[], key: string): DuckyOSStoreItemDto[] {
  const match = deriveSections(items).find((s) => s.key === key);
  return match ? match.items : [];
}

/**
 * Promo slides: an available update first (if any), then the most-installed items.
 * At most 3 slides, no duplicate items.
 */
export function deriveHeroSlides(items: DuckyOSStoreItemDto[]): HeroSlide[] {
  const slides: HeroSlide[] = [];
  const update = items.find((i) => i.state === "update");
  if (update) slides.push({ item: update, tag: "Update available", variant: "update" });

  const featured = [...items]
    .filter((i) => i.state !== "unsupported")
    .sort((a, b) => installCount(b) - installCount(a));
  const tags = ["Featured", "Most installed", "Community favorite"] as const;
  const variants: HeroVariant[] = ["emerald", "blue", "purple"];
  let rank = 0;
  for (const item of featured) {
    if (slides.length >= 3) break;
    if (slides.some((s) => s.item.slug === item.slug)) continue;
    slides.push({ item, tag: tags[rank] ?? "Featured", variant: variants[rank % variants.length]! });
    rank += 1;
  }
  return slides;
}

export function pageCount(total: number, perPage: number = SECTION_PAGE_SIZE): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, perPage)));
}

export function pageSlice<T>(items: T[], page: number, perPage: number = SECTION_PAGE_SIZE): T[] {
  const clamped = Math.min(Math.max(1, page), pageCount(items.length, perPage));
  const start = (clamped - 1) * perPage;
  return items.slice(start, start + perPage);
}

export function formatInstalls(count: number | null | undefined): string {
  if (typeof count !== "number" || !isFinite(count) || count < 0) return "—";
  if (count >= 1_000_000) return `${trimZero((count / 1_000_000).toFixed(1))}M`;
  if (count >= 10_000) return `${Math.round(count / 1000)}k`;
  if (count >= 1000) return `${trimZero((count / 1000).toFixed(1))}k`;
  return String(count);
}

function trimZero(value: string): string {
  return value.replace(/\.0$/, "");
}

export function itemInitials(item: DuckyOSStoreItemDto): string {
  const name = (item.name || item.slug || "?").trim();
  const parts = name.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return `${parts[0]![0] ?? ""}${parts[1]![0] ?? ""}`.toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

/** The "author" line under a card title — closest real analog to a publisher. */
export function authorLabelFor(item: DuckyOSStoreItemDto): string {
  const source = item.source || "";
  if (source === "store") return "DuckyOS Store";
  if (source === "local") return "Local file";
  if (source === "ai") return "AI-made";
  if (source === "bundled") return "Bundled";
  // Catalog hit with no local source stamp yet (or older panel) — still Store.
  if (item.stripe_product_key || item.latest_version) return "DuckyOS Store";
  return itemKind(item) === "plugin" ? "Plugin" : "Skill pack";
}

export function formatPrice(item: DuckyOSStoreItemDto): string {
  const cents = Number(item.price_cents || 0);
  if (!item.paid && cents <= 0) return "Free";
  const currency = (item.currency || "usd").toUpperCase();
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
    }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

export function needsPurchase(item: DuckyOSStoreItemDto): boolean {
  return Boolean(item.paid) && item.owned !== true;
}
