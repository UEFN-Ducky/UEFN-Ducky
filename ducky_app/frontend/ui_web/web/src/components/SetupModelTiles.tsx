/**
 * The setup's model picks as tiles: for each job, the few connected models that suit it best,
 * one click each (no scrolling a long list). Anything else stays one click away under "Other model".
 */
import { useSyncExternalStore, type ReactNode } from "react";
import { qualifyFavorite } from "../hooks/favoriteModelsCatalog";
import { getCachedModels, subscribeModelsCatalog, type CatalogModelRow } from "../hooks/modelsCatalogCache";
import { Icons } from "../icons/Icons";

export type ModelJobId = "chat" | "live" | "spoken" | "memory";

// The strongest models first for chats; small, fast ones first for everything else.
const STRONG = /opus|sonnet|gpt-5(?!.*(mini|nano))|gpt-4\.1(?!.*(mini|nano))|\bo3\b|gemini.*pro|grok-4|deepseek.*(r1|v3)/i;
const FAST = /haiku|mini|nano|flash|lite|small|fast/i;
const MAX_TILES = 4;

/** The best few models for a job, at most one per name. */
export function suggestModels(rows: CatalogModelRow[], job: ModelJobId): CatalogModelRow[] {
  const usable = job === "chat" ? rows.filter((r) => r.supportsTools) : rows;
  const score = (r: CatalogModelRow) => {
    const name = `${r.id} ${r.name}`;
    if (job === "chat") return STRONG.test(name) ? 2 : FAST.test(name) ? 0 : 1;
    return FAST.test(name) ? 2 : STRONG.test(name) ? 0 : 1;
  };
  const seen = new Set<string>();
  return usable
    .map((r, i) => ({ r, s: score(r), i }))
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .map((x) => x.r)
    .filter((r) => {
      const k = (r.name || r.id).toLowerCase();
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    })
    .slice(0, MAX_TILES);
}

function bare(value: string): string {
  return value.includes(":") ? value.slice(value.indexOf(":") + 1) : value;
}

export function SetupModelTiles({
  job,
  value,
  onPick,
  sameLabel,
  other,
}: {
  job: ModelJobId;
  value: string;
  onPick: (next: string) => void;
  /** Non-chat jobs: the "use the fallback" tile (value ""). */
  sameLabel?: string;
  /** The full picker, behind "Other model". */
  other: ReactNode;
}) {
  const rows = useSyncExternalStore(subscribeModelsCatalog, getCachedModels) ?? [];
  const tiles = suggestModels(rows, job);
  const picked = bare(value);
  const pickedIsTile = !value || tiles.some((t) => t.id === picked);

  return (
    <div className="setup-model-tiles" role="radiogroup" aria-label="Models">
      {sameLabel ? (
        <button
          type="button"
          role="radio"
          aria-checked={!value}
          className={`setup-model-tile${!value ? " is-on" : ""}`}
          onClick={() => onPick("")}
        >
          <span className="setup-model-tile-name">{sameLabel}</span>
          <span className="setup-model-tile-sub">no extra model</span>
          {!value ? <Icons.Check /> : null}
        </button>
      ) : null}
      {tiles.map((t) => {
        const on = picked === t.id;
        return (
          <button
            key={`${t.providerKey}:${t.id}`}
            type="button"
            role="radio"
            aria-checked={on}
            className={`setup-model-tile${on ? " is-on" : ""}`}
            onClick={() => onPick(qualifyFavorite(t.providerKey.trim().toLowerCase().replace(/-/g, "_"), t.id))}
          >
            <span className="setup-model-tile-name">{t.name || t.id}</span>
            <span className="setup-model-tile-sub">{t.provider}</span>
            {on ? <Icons.Check /> : null}
          </button>
        );
      })}
      <div className={`setup-model-tile is-other${value && !pickedIsTile ? " is-on" : ""}`}>
        <span className="setup-model-tile-sub">Other model</span>
        {other}
      </div>
    </div>
  );
}
