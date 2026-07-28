import { BLANK_PROFILE_ID } from "../../generated/bundledAgentProfiles";
import {
  isCodingAgentFavoriteId,
  isLegacyAgentOnlyFavorite,
  parseFavoriteSelection,
} from "../../hooks/favoriteModelsCatalog";
import type { AgentProfileDto, AgentProfileEditorCatalogDto, DuckyConfigDto } from "../../types/panel";

export interface DuckyProfileFormState {
  name: string;
  duckyStyle: string;
  personality: string;
  whenToUse: string;
  /** Pack ids denied for this ducky (empty = all packs available, lazy-loaded). */
  disabledPacks: string[];
  /** Per-pack toggleable subskill ids this ducky may load (omit pack = all). */
  enabledSubskills: Record<string, string[]>;
  /** Tool/MCP group ids denied for this ducky (empty = all available). */
  disabledToolIds: string[];
  /** Qualified "backend:model" override; empty uses the global Default Model. */
  model: string;
  /** TTS voice id (builtin:… / plugin:…); empty uses Settings → Voice default. */
  ttsVoice: string;
  /** Talking-speed multiplier; 0 uses Settings → Voice default speed. */
  ttsSpeed: number;
  /** Anthropic extended-thinking effort (off|low|medium|high). */
  thinkingEffort: string;
}

/** Backend when the model targets a coding agent, else ducky (API models). */
export function codingAgentFromModel(model: string): string {
  const text = (model || "").trim();
  if (!text) return "ducky";
  const parsed = parseFavoriteSelection(text);
  if (parsed && isCodingAgentFavoriteId(parsed.backend)) return parsed.backend;
  if (isLegacyAgentOnlyFavorite(text)) {
    return text.toLowerCase().replace(/-/g, "_");
  }
  return "ducky";
}

/** True when the Effort control should show (gateway plugins that opt in). */
export function modelShowsThinkingEffort(
  model: string,
  codingAgents: { id: string; shows_thinking_effort?: boolean }[] = [],
  thinkingProviderIds: string[] = [],
): boolean {
  const agent = codingAgentFromModel(model);
  if (agent !== "ducky") {
    const key = agent.trim().toLowerCase().replace(/-/g, "_");
    const row = codingAgents.find(
      (a) => String(a.id || "").trim().toLowerCase().replace(/-/g, "_") === key,
    );
    if (row?.shows_thinking_effort) return true;
  }
  const parsed = parseFavoriteSelection(model);
  const backend = (parsed?.backend || "").trim().toLowerCase();
  if (backend && thinkingProviderIds.includes(backend)) return true;
  // Model-name heuristic when contributions are not loaded yet.
  const m = (model || "").toLowerCase();
  return m.includes("claude");
}

/** Empty is fine (global Default Model applies); reject unusable saved values. */
export function validateModelSelection(model: string): string | null {
  const text = (model || "").trim();
  if (!text) return null;
  if (isLegacyAgentOnlyFavorite(text)) {
    return `“${text}” is an agent name, not an exact model. Pick a concrete model (e.g. Cursor → composer-2.5).`;
  }
  if (!parseFavoriteSelection(text) && text.includes(":")) {
    return `Saved choice “${text}” is invalid. Re-pick an exact model.`;
  }
  // Bare legacy API ids are allowed to remain stored; spawn will validate live.
  return null;
}

/** True when the error should open the in-dialog model picker (not a plain OK alert). */
export function isModelGateError(message: string): boolean {
  return /no model selected|default model|exact model|re-pick/i.test(message);
}

/** First saved model from a profile's favorite_models list (legacy shape). */
export function modelFromFavorites(raw?: string[]): string {
  const src = Array.isArray(raw) ? raw : [];
  for (const item of src) {
    const text = (item || "").trim();
    if (text) return text;
  }
  return "";
}

export function profileToForm(profile: AgentProfileDto): DuckyProfileFormState {
  return {
    name: profile.name,
    duckyStyle: profile.ducky_style,
    personality: profile.ducky_personality || "",
    whenToUse: profile.when_to_use || "",
    disabledPacks: [...(profile.disabled_packs || [])],
    enabledSubskills: { ...(profile.enabled_subskills || {}) },
    disabledToolIds: [...(profile.disabled_tool_ids || [])],
    model: modelFromFavorites(profile.favorite_models),
    ttsVoice: (profile.tts_voice || "").trim(),
    ttsSpeed: Number(profile.tts_speed) || 0,
    thinkingEffort: "off",
  };
}

export function catalogDefaults(catalog: AgentProfileEditorCatalogDto): DuckyProfileFormState {
  return {
    name: "",
    duckyStyle: "artist",
    personality: "",
    whenToUse: "",
    disabledPacks: [...(catalog.default_disabled_packs || [])],
    enabledSubskills: { ...(catalog.default_enabled_subskills || {}) },
    disabledToolIds: [...(catalog.default_disabled_tool_ids || [])],
    model: "",
    ttsVoice: "",
    ttsSpeed: 0,
    thinkingEffort: "off",
  };
}

export function formToConfig(
  form: DuckyProfileFormState,
  chatTitle?: string,
  profileId?: string,
): DuckyConfigDto {
  const model = form.model.trim();
  const config: DuckyConfigDto = {
    ducky_name: form.name.trim(),
    ducky_style: form.duckyStyle,
    ducky_personality: form.personality.trim(),
    disabled_packs: [...form.disabledPacks],
    enabled_subskills: { ...form.enabledSubskills },
    disabled_tool_ids: form.disabledToolIds,
    favorite_models: model ? [model] : [],
    coding_agent: codingAgentFromModel(model),
    tts_voice: form.ttsVoice.trim(),
    tts_speed: form.ttsSpeed || 0,
    thinking_effort: modelShowsThinkingEffort(model) ? form.thinkingEffort || "off" : "off",
  };
  if (chatTitle) {
    config.title = chatTitle;
  }
  const pid = (profileId || "").trim();
  if (pid) {
    config.profile_id = pid;
  }
  return config;
}

export function formToProfilePatch(form: DuckyProfileFormState): Partial<AgentProfileDto> {
  const model = form.model.trim();
  return {
    name: form.name.trim() || "Untitled",
    ducky_style: form.duckyStyle,
    ducky_personality: form.personality.trim(),
    when_to_use: form.whenToUse.trim(),
    disabled_packs: [...form.disabledPacks],
    enabled_subskills: { ...form.enabledSubskills },
    disabled_tool_ids: form.disabledToolIds,
    favorite_models: model ? [model] : [],
    tts_voice: form.ttsVoice.trim(),
    tts_speed: form.ttsSpeed || 0,
  };
}

/** Stable snapshot for dirty-checking the ducky details editor. */
export function serializeDuckyForm(form: DuckyProfileFormState): string {
  return JSON.stringify({
    name: (form.name || "").trim(),
    duckyStyle: form.duckyStyle || "",
    personality: form.personality || "",
    whenToUse: form.whenToUse || "",
    disabledPacks: [...(form.disabledPacks || [])].map(String).sort(),
    enabledSubskills: Object.keys(form.enabledSubskills || {})
      .sort()
      .reduce<Record<string, string[]>>((acc, key) => {
        acc[key] = [...(form.enabledSubskills[key] || [])].map(String).sort();
        return acc;
      }, {}),
    disabledToolIds: [...(form.disabledToolIds || [])].map(String).sort(),
    model: (form.model || "").trim(),
    ttsVoice: (form.ttsVoice || "").trim(),
    ttsSpeed: Number(form.ttsSpeed) || 0,
    thinkingEffort: (form.thinkingEffort || "off").trim().toLowerCase() || "off",
  });
}

/**
 * Number of per-card accent hues. Each ducky card gets one of these colored
 * hover glows, keyed deterministically off its id so the color is stable
 * across renders. The actual hues live in CSS as `.ducky-card--accent-N`
 * (inline styles are banned by the no-inline-styles lint).
 */
export const DUCKY_ACCENT_COUNT = 8;

export function duckyAccentIndex(id: string): number {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) {
    hash = (Math.imul(hash, 31) + id.charCodeAt(i)) >>> 0;
  }
  return hash % DUCKY_ACCENT_COUNT;
}

/** CSS modifier class carrying this profile's `--card-accent` hue. */
export function duckyAccentClass(id: string): string {
  return `ducky-card--accent-${duckyAccentIndex(id)}`;
}

/** Short one-line descriptor under a card's name. */
export function duckyTagline(profile: AgentProfileDto): string {
  const when = (profile.when_to_use || "").trim();
  if (when) {
    const firstClause = when.split(/[.\n]/)[0].trim();
    if (firstClause) {
      return firstClause.length > 42 ? `${firstClause.slice(0, 41).trimEnd()}…` : firstClause;
    }
  }
  return "";
}

export { BLANK_PROFILE_ID };
