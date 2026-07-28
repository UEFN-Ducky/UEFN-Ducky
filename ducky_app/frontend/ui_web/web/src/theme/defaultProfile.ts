import { defaultFoundation, type FoundationKey } from "./defaultTokens";
import builtinJson from "./appearanceBuiltinProfiles.json";

/** Built-in profile id — never persisted in appearance_profiles. */
export const DEFAULT_PROFILE_ID = "__default__";

export const DEFAULT_PROFILE_NAME = "Default";

/** Legacy built-in ids (removed from host — now Store plugins `light` / `hacker`). */
export const LIGHT_PROFILE_ID = "__light__";
export const HACKER_PROFILE_ID = "__hacker__";

const LEGACY_BUILT_IN_PROFILE_IDS = new Set([LIGHT_PROFILE_ID, HACKER_PROFILE_ID]);

export type BuiltInAppearanceProfile = {
  id: string;
  name: string;
  foundation: Record<string, string>;
  overrides: Record<string, string>;
  statusOverrides: Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;
};

type BuiltinJsonProfile = {
  id: string;
  name: string;
  foundation?: Record<string, string>;
  overrides?: Record<string, string>;
  status_overrides?: Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;
};

function profileFromJson(raw: BuiltinJsonProfile): BuiltInAppearanceProfile {
  const isDefault = raw.id === DEFAULT_PROFILE_ID;
  return {
    id: raw.id,
    name: raw.name,
    foundation: isDefault
      ? defaultFoundation()
      : { ...defaultFoundation(), ...(raw.foundation || {}) },
    overrides: { ...(raw.overrides || {}) },
    statusOverrides: { ...(raw.status_overrides || {}) },
  };
}

/** Shipped read-only appearance preset (Default only). Light / Hacker are Store plugins. */
export const BUILT_IN_PROFILES: BuiltInAppearanceProfile[] = (
  builtinJson.profiles as BuiltinJsonProfile[]
).map(profileFromJson);

export const BUILT_IN_PROFILE_IDS = new Set(BUILT_IN_PROFILES.map((p) => p.id));

export function getBuiltInProfile(id: string | null | undefined): BuiltInAppearanceProfile | null {
  const trimmed = (id || "").trim();
  if (!trimmed) return null;
  return BUILT_IN_PROFILES.find((p) => p.id === trimmed) ?? null;
}

export function isBuiltInProfile(id: string | null | undefined): boolean {
  return BUILT_IN_PROFILE_IDS.has((id || "").trim());
}

export function isLegacyBuiltInProfileId(id: string | null | undefined): boolean {
  return LEGACY_BUILT_IN_PROFILE_IDS.has((id || "").trim());
}

/** Map removed built-ins → Store plugin package ids. */
export function legacyBuiltInToPluginId(id: string | null | undefined): "light" | "hacker" | null {
  const trimmed = (id || "").trim();
  if (trimmed === LIGHT_PROFILE_ID) return "light";
  if (trimmed === HACKER_PROFILE_ID) return "hacker";
  return null;
}

export function normalizeActiveProfileId(id: string | null | undefined): string {
  const trimmed = (id || "").trim();
  if (!trimmed) return DEFAULT_PROFILE_ID;
  if (isLegacyBuiltInProfileId(trimmed)) return DEFAULT_PROFILE_ID;
  if (isBuiltInProfile(trimmed)) return trimmed;
  return trimmed;
}

/** Persist payload for a built-in: Default uses empty foundation so FE defaults apply. */
export function builtInPersistPayload(id: string): {
  foundation: Record<string, string>;
  overrides: Record<string, string>;
  statusOverrides: Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;
} | null {
  const profile = getBuiltInProfile(id);
  if (!profile) return null;
  if (profile.id === DEFAULT_PROFILE_ID) {
    return { foundation: {}, overrides: {}, statusOverrides: {} };
  }
  const foundation: Record<string, string> = {};
  for (const key of Object.keys(profile.foundation) as FoundationKey[]) {
    foundation[key] = profile.foundation[key];
  }
  return {
    foundation,
    overrides: { ...profile.overrides },
    statusOverrides: { ...profile.statusOverrides },
  };
}

export function createDefaultProfile(): BuiltInAppearanceProfile {
  return getBuiltInProfile(DEFAULT_PROFILE_ID) ?? {
    id: DEFAULT_PROFILE_ID,
    name: DEFAULT_PROFILE_NAME,
    foundation: defaultFoundation(),
    overrides: {},
    statusOverrides: {},
  };
}

export function listBuiltInProfiles(): BuiltInAppearanceProfile[] {
  return BUILT_IN_PROFILES.map((p) => ({
    ...p,
    foundation: { ...p.foundation },
    overrides: { ...p.overrides },
    statusOverrides: { ...p.statusOverrides },
  }));
}

export function filterStoredProfiles<T extends { id: string }>(profiles: T[]): T[] {
  return profiles.filter(
    (p) => !isBuiltInProfile(p.id) && !isLegacyBuiltInProfileId(p.id),
  );
}
