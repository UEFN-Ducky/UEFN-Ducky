import type { DuckyOSStoreItemDto } from "../../types/panel";

/** Browse filter seeds (not install package types). Everything is a plugin — omit that bucket. */
export const CORE_STORE_CATEGORIES = ["gateways", "skills", "themes", "games"] as const;

/** Redundant browse slug — package kind already covers “plugin”; never show in the UI. */
export const HIDDEN_BROWSE_CATEGORIES = new Set(["plugins", "plugin"]);

/** Virtual category: items present on this PC (any state). */
export const INSTALLED_CATEGORY = "installed";

/** Virtual category: desktop plugins authored by duckies (`source=ai`). */
export const AI_MADE_CATEGORY = "ai-made";

/** Virtual category: paid cloud purchases for the signed-in Ducky account. */
export const OWNED_CATEGORY = "owned";

const CATEGORY_LABELS: Record<string, string> = {
  skills: "Skills",
  themes: "Themes",
  gateways: "Gateways",
  games: "Games",
  mcps: "MCPs",
  "3d": "3D",
  trending: "Trending",
  owned: "Owned",
  installed: "Installed",
  "ai-made": "AI-made",
};

/** Nice Title Case (or acronym) label for a category slug. */
export function categoryLabel(raw: string): string {
  const key = String(raw || "")
    .trim()
    .toLowerCase();
  if (!key) return "";
  if (CATEGORY_LABELS[key]) return CATEGORY_LABELS[key]!;
  return key
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((w) => w[0]!.toUpperCase() + w.slice(1))
    .join(" ");
}

/** Paid purchase entitlement (not local-only free installs). */
export function isOwnedPurchase(item: DuckyOSStoreItemDto): boolean {
  return item.owned === true && item.paid === true;
}

export function asLabelList(raw: string[] | undefined | null): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((x) => String(x || "").trim()).filter(Boolean);
}

/** Install package type (plugin zip vs skill pack) — not a browse filter. */
export function itemKind(item: DuckyOSStoreItemDto): "plugin" | "skill" | string {
  const kind = (item.kind || item.category || "skill").toLowerCase();
  if (kind === "plugin" || kind === "plugins" || kind === "feature" || kind === "features") {
    return "plugin";
  }
  if (kind === "skill" || kind === "skills" || kind === "") return "skill";
  return kind;
}

/** Browse categories (multi). Falls back to package bucket when empty. */
export function itemCategories(item: DuckyOSStoreItemDto): string[] {
  const cats = asLabelList(item.categories).map((c) => c.toLowerCase());
  if (cats.length) return cats;
  return itemKind(item) === "plugin" ? ["plugins"] : ["skills"];
}

export function filterStoreItems(
  items: DuckyOSStoreItemDto[],
  opts: { q?: string; category?: string },
): DuckyOSStoreItemDto[] {
  const q = (opts.q || "").trim().toLowerCase();
  const category = (opts.category || "").trim().toLowerCase();
  return items.filter((item) => {
    const cats = itemCategories(item);
    const tags = asLabelList(item.tags).map((t) => t.toLowerCase());
    if (category === INSTALLED_CATEGORY) {
      if (item.installed_version == null) return false;
    } else if (category === AI_MADE_CATEGORY) {
      if ((item.source || "").toLowerCase() !== "ai") return false;
    } else if (category === OWNED_CATEGORY) {
      if (!isOwnedPurchase(item)) return false;
    } else if (category && !cats.includes(category)) return false;
    if (!q) return true;
    const hay = [
      item.name,
      item.description,
      item.slug,
      ...cats,
      ...tags,
      ...(item.contributes_summary || []),
    ]
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });
}
