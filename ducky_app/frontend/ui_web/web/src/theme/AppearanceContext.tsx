import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { defaultFoundation, type FoundationKey } from "./defaultTokens";
import {
  DEFAULT_PROFILE_ID,
  builtInPersistPayload,
  filterStoredProfiles,
  getBuiltInProfile,
  isBuiltInProfile,
  listBuiltInProfiles,
  normalizeActiveProfileId,
} from "./defaultProfile";
import {
  computeCssVars,
  cssVarsToStyleBlock,
  type AppearanceState,
} from "./tokenEngine";
import {
  addFontEntry,
  createFontEntry,
  fontLibraryToOverridesPatch,
  migrateLegacyFonts,
  parseFontLibrary,
  removeFontEntry,
  setActiveFont,
  setFontEnabled,
  setFontSize as setFontSizeState,
  type FontLibraryState,
  type FontRole,
} from "./fontLibrary";
import { parseGoogleFontInput } from "./googleFont";
import { getApi } from "../hooks/usePanelApi";
import { onApiReady } from "../hooks/onApiReady";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { UnsavedChangesModal } from "../components/UnsavedChangesModal";
import { usePluginContributions } from "../hooks/usePluginContributions";
import {
  isPluginProfileId,
  parsePluginProfileId,
  parsePluginSkinId,
  pluginEffectId,
  pluginProfileId,
  pluginSkinId,
} from "./appearancePluginIds";
import type {
  AppearanceDto,
  AppearanceProfileDto,
  AppearanceProfilePatchDto,
  PanelPushEvent,
} from "../types/panel";
import { DEFAULT_SOUNDS, normalizeSounds, type SoundsSettings } from "../sfx/soundFx";

export interface AppearanceProfile {
  id: string;
  name: string;
  foundation: Record<string, string>;
  overrides: Record<string, string>;
  statusOverrides: Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;
  /** Set when the profile comes from an enabled plugin contribution. */
  pluginId?: string;
}

type StatusOverrideMap = Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;

interface ProfileBaseline {
  foundation: Record<FoundationKey, string>;
  overrides: Record<string, string>;
  statusOverrides: StatusOverrideMap;
}

interface AppearanceContextValue {
  foundation: Record<FoundationKey, string>;
  overrides: Record<string, string>;
  statusOverrides: StatusOverrideMap;
  profiles: AppearanceProfile[];
  activeProfileId: string;
  /** Empty = none; ``matrix`` or ``plugin:<id>:<effect>``. */
  effectId: string;
  setEffectId: (id: string) => Promise<void>;
  /** When false, keep effectId but do not mount the background animation. */
  effectsEnabled: boolean;
  setEffectsEnabled: (enabled: boolean) => Promise<void>;
  /** Empty = none; ``plugin:<pluginId>:<skinId>`` full chrome skin. */
  skinId: string;
  setSkinId: (id: string) => Promise<void>;
  /** Global sound-effect settings (preview live; disk only on Save). */
  sounds: SoundsSettings;
  setSounds: (next: SoundsSettings) => Promise<void>;
  cssVars: Record<string, string>;
  styleBlock: string;
  /** False until the first get_appearance round-trip finishes (or API has no appearance endpoint). */
  appearanceReady: boolean;
  setFoundation: (key: FoundationKey, value: string) => void;
  resetFoundation: (key: FoundationKey) => void;
  isFoundationCustom: (key: FoundationKey) => boolean;
  setOverride: (tokenId: string, value: string) => void;
  resetOverride: (tokenId: string) => void;
  resetOverrides: (tokenIds: string[]) => void;
  setStatusOverride: (statusId: string, field: "bg" | "border" | "text" | "dim", value: string) => void;
  resetStatus: (statusId: string) => void;
  resetStatuses: (statusIds: string[]) => void;
  save: () => Promise<void>;
  saveAsProfile: (name: string) => Promise<void>;
  loadProfile: (id: string) => Promise<void>;
  renameProfile: (id: string, name: string) => Promise<void>;
  deleteProfile: (id: string) => Promise<void>;
  canEditActiveProfile: boolean;
  hasUnsavedChanges: boolean;
  discardChanges: () => void;
  guardUnsavedChanges: () => Promise<boolean>;
  saving: boolean;
  saveMsg: string;
  fontLibrary: FontLibraryState;
  addFont: (role: FontRole, raw: string) => string | null;
  selectFont: (role: FontRole, id: string) => void;
  removeFont: (id: string) => void;
  setFontRoleEnabled: (role: FontRole, enabled: boolean) => void;
  setFontSize: (role: FontRole, px: number) => void;
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

function profileFromDto(dto: AppearanceProfileDto): AppearanceProfile {
  return {
    id: dto.id,
    name: dto.name,
    foundation: { ...(dto.foundation || {}) },
    overrides: { ...(dto.overrides || {}) },
    statusOverrides: { ...(dto.status_overrides || {}) },
  };
}

type AppearanceUiState = AppearanceState & {
  profiles: AppearanceProfile[];
  activeProfileId: string;
  effectId: string;
  effectsEnabled: boolean;
  skinId: string;
  profilePatches: Record<string, AppearanceProfilePatchDto>;
  sounds: SoundsSettings;
};

function toState(dto: Partial<AppearanceDto> | null): AppearanceUiState {
  const userProfiles = filterStoredProfiles((dto?.profiles || []).map(profileFromDto));
  const migratedOverrides = migrateLegacyFonts({ ...(dto?.overrides || {}) });
  const effectId = String(dto?.effect_id || "").trim();
  const effectsEnabled =
    typeof dto?.effects_enabled === "boolean" ? dto.effects_enabled : !!effectId;
  return {
    foundation: { ...defaultFoundation(), ...(dto?.foundation || {}) },
    overrides: migratedOverrides,
    statusOverrides: { ...(dto?.status_overrides || {}) },
    profiles: userProfiles,
    activeProfileId: normalizeActiveProfileId(dto?.active_profile_id),
    effectId,
    effectsEnabled,
    skinId: String(dto?.skin_id || "").trim(),
    profilePatches: { ...(dto?.profile_patches || {}) },
    sounds: normalizeSounds(dto?.sounds),
  };
}

function profilesForUi(
  userProfiles: AppearanceProfile[],
  pluginProfiles: AppearanceProfile[],
): AppearanceProfile[] {
  return [...listBuiltInProfiles(), ...pluginProfiles, ...userProfiles];
}

function syncPluginPatch(state: AppearanceUiState): AppearanceUiState {
  if (!isPluginProfileId(state.activeProfileId)) return state;
  return {
    ...state,
    profilePatches: {
      ...state.profilePatches,
      [state.activeProfileId]: {
        foundation: { ...state.foundation },
        overrides: { ...state.overrides },
        status_overrides: { ...state.statusOverrides },
        effect_id: state.effectId || "",
        effects_enabled: state.effectsEnabled,
        skin_id: state.skinId || "",
      },
    },
  };
}

function stateToDto(state: AppearanceUiState): AppearanceDto {
  const synced = syncPluginPatch(state);
  return {
    foundation: synced.foundation,
    overrides: synced.overrides,
    status_overrides: synced.statusOverrides,
    profiles: filterStoredProfiles(synced.profiles || []).map((p) => ({
      id: p.id,
      name: p.name,
      foundation: p.foundation,
      overrides: p.overrides,
      status_overrides: p.statusOverrides,
    })),
    active_profile_id: synced.activeProfileId || DEFAULT_PROFILE_ID,
    effect_id: synced.effectId || "",
    effects_enabled: synced.effectsEnabled,
    skin_id: synced.skinId || "",
    profile_patches: synced.profilePatches,
    sounds: synced.sounds || { ...DEFAULT_SOUNDS, mapping: { ...DEFAULT_SOUNDS.mapping } },
  };
}

async function persistAppearance(state: AppearanceUiState): Promise<void> {
  const api = getApi();
  if (!api?.save_appearance) return;
  const dto = stateToDto(state);
  // Plain JSON object — pywebview can choke on nested React/proxy objects.
  await api.save_appearance(JSON.parse(JSON.stringify(dto)) as AppearanceDto);
}

function serializeAppearanceState(state: AppearanceUiState): string {
  return JSON.stringify(stateToDto(state));
}

/** Built-in themes stay locked; plugin themes are editable with contribution fallback. */
function isReadOnlyProfile(id: string): boolean {
  return isBuiltInProfile(id);
}

function isStoredUserProfile(id: string): boolean {
  return !isBuiltInProfile(id) && !isPluginProfileId(id);
}

function resolveBaseline(
  profileId: string,
  pluginProfiles: AppearanceProfile[],
): ProfileBaseline {
  if (isPluginProfileId(profileId)) {
    const pluginProfile = pluginProfiles.find((p) => p.id === profileId);
    if (pluginProfile) {
      return {
        foundation: {
          ...defaultFoundation(),
          ...pluginProfile.foundation,
        } as Record<FoundationKey, string>,
        overrides: migrateLegacyFonts({ ...pluginProfile.overrides }),
        statusOverrides: { ...pluginProfile.statusOverrides },
      };
    }
  }
  if (isBuiltInProfile(profileId)) {
    const payload = builtInPersistPayload(profileId);
    if (payload) {
      return {
        foundation: {
          ...defaultFoundation(),
          ...payload.foundation,
        } as Record<FoundationKey, string>,
        overrides: migrateLegacyFonts({ ...payload.overrides }),
        statusOverrides: { ...payload.statusOverrides },
      };
    }
  }
  return {
    foundation: defaultFoundation(),
    overrides: {},
    statusOverrides: {},
  };
}

function applyProfilePatch(
  baseline: ProfileBaseline,
  patch: AppearanceProfilePatchDto | undefined,
): Pick<AppearanceUiState, "foundation" | "overrides" | "statusOverrides"> {
  if (!patch) {
    return {
      foundation: baseline.foundation,
      overrides: baseline.overrides,
      statusOverrides: baseline.statusOverrides,
    };
  }
  // Patches store the full live snapshot (not a delta), so prefer them wholesale.
  return {
    foundation: (patch.foundation
      ? { ...defaultFoundation(), ...patch.foundation }
      : baseline.foundation) as Record<FoundationKey, string>,
    overrides: patch.overrides
      ? migrateLegacyFonts({ ...patch.overrides })
      : baseline.overrides,
    statusOverrides: patch.status_overrides
      ? { ...patch.status_overrides }
      : baseline.statusOverrides,
  };
}

function isFocusWindow(): boolean {
  return new URLSearchParams(window.location.search).has("focus");
}

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const contrib = usePluginContributions();
  const [state, setState] = useState(() => toState(null));
  const [appearanceReady, setAppearanceReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  const pluginProfiles = useMemo<AppearanceProfile[]>(
    () =>
      contrib.appearance_profiles.map((p) => ({
        id: pluginProfileId(p.plugin_id, p.id),
        name: p.name,
        foundation: { ...(p.foundation || {}) },
        overrides: { ...(p.overrides || {}) },
        statusOverrides: { ...(p.status_overrides || {}) },
        pluginId: p.plugin_id,
      })),
    [contrib.appearance_profiles],
  );
  const pluginProfilesRef = useRef(pluginProfiles);
  pluginProfilesRef.current = pluginProfiles;
  const skinsRef = useRef(contrib.appearance_skins);
  skinsRef.current = contrib.appearance_skins;
  const effectsRef = useRef(contrib.appearance_effects);
  effectsRef.current = contrib.appearance_effects;

  useEffect(() => {
    if (!saveMsg) return;
    const timer = window.setTimeout(() => setSaveMsg(""), 2800);
    return () => window.clearTimeout(timer);
  }, [saveMsg]);

  const lastLivePushRef = useRef<string>("");
  const savedSnapshotRef = useRef<string>(serializeAppearanceState(toState(null)));
  const [savedRevision, setSavedRevision] = useState(0);
  const [leavePrompt, setLeavePrompt] = useState<{ resolve: (allowed: boolean) => void } | null>(null);
  const [leaveSaving, setLeaveSaving] = useState(false);

  const markPersisted = useCallback((next: AppearanceUiState) => {
    savedSnapshotRef.current = serializeAppearanceState(next);
    setSavedRevision((revision) => revision + 1);
  }, []);

  // If the active plugin profile disappears (plugin disabled), fall back to Default.
  // Wait until contributions have loaded once — otherwise a cold start races and
  // wipes the saved GalaxyCraft/etc. theme before plugins appear.
  useEffect(() => {
    if (!appearanceReady || !contrib.ready || !isPluginProfileId(state.activeProfileId)) return;
    if (pluginProfiles.some((p) => p.id === state.activeProfileId)) return;
    const parsed = parsePluginProfileId(state.activeProfileId);
    if (parsed) {
      const enabled = contrib.enabled_ids.some(
        (id) => id.trim().toLowerCase() === parsed.pluginId,
      );
      // Plugin still enabled but profile row not listed yet — keep waiting.
      if (enabled) return;
    }
    setState((prev) => {
      const fallback: AppearanceUiState = {
        ...prev,
        foundation: defaultFoundation(),
        overrides: {},
        statusOverrides: {},
        activeProfileId: DEFAULT_PROFILE_ID,
        effectId: "",
        effectsEnabled: false,
        skinId: "",
      };
      void persistAppearance(fallback).then(() => markPersisted(fallback));
      return fallback;
    });
  }, [
    appearanceReady,
    contrib.ready,
    contrib.enabled_ids,
    pluginProfiles,
    state.activeProfileId,
    markPersisted,
  ]);

  // Clear skin when the contributing plugin is disabled / uninstalled.
  useEffect(() => {
    if (!appearanceReady || !contrib.ready || !state.skinId) return;
    const parsed = parsePluginSkinId(state.skinId);
    if (!parsed) return;
    const stillThere = contrib.appearance_skins.some(
      (s) =>
        s.plugin_id.trim().toLowerCase() === parsed.pluginId && s.id === parsed.skinId,
    );
    if (stillThere) return;
    const enabled = contrib.enabled_ids.some(
      (id) => id.trim().toLowerCase() === parsed.pluginId,
    );
    // Enabled plugin may still be registering skins — don't wipe yet.
    if (enabled) return;
    setState((prev) => {
      const next = { ...prev, skinId: "" };
      void persistAppearance(next).then(() => markPersisted(next));
      return next;
    });
  }, [
    appearanceReady,
    contrib.ready,
    state.skinId,
    contrib.appearance_skins,
    contrib.enabled_ids,
    markPersisted,
  ]);

  // Chrome skin stays with plugin themes only — never leave it on Default/user profiles.
  useEffect(() => {
    if (!appearanceReady || !contrib.ready) return;
    if (isPluginProfileId(state.activeProfileId)) return;
    if (!state.skinId) return;
    setState((prev) => {
      const next = { ...prev, skinId: "" };
      void persistAppearance(next).then(() => markPersisted(next));
      return next;
    });
  }, [appearanceReady, contrib.ready, state.activeProfileId, state.skinId, markPersisted]);

  useEffect(() => {
    return onApiReady((api) => {
      if (!api.get_appearance) {
        setAppearanceReady(true);
        return;
      }
      void api
        .get_appearance()
        .then((dto) => {
          lastLivePushRef.current = JSON.stringify(dto);
          const loaded = toState(dto);
          setState(loaded);
          markPersisted(loaded);
        })
        .finally(() => setAppearanceReady(true));
    });
  }, []);

  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event: PanelPushEvent) => {
      if (event.type !== "appearance_changed") return;
      if (event.appearance) {
        lastLivePushRef.current = JSON.stringify(event.appearance);
        setState(toState(event.appearance));
        return;
      }
      const api = getApi();
      if (!api?.get_appearance) return;
      void api.get_appearance().then((dto) => setState(toState(dto)));
    });
  }, []);

  useEffect(() => {
    if (!appearanceReady || isFocusWindow()) return;
    const api = getApi();
    if (!api?.push_appearance_live) return;
    const dto = stateToDto(state);
    const serialized = JSON.stringify(dto);
    if (serialized === lastLivePushRef.current) return;
    const timer = window.setTimeout(() => {
      lastLivePushRef.current = serialized;
      void api.push_appearance_live(JSON.parse(serialized) as AppearanceDto);
    }, 80);
    return () => window.clearTimeout(timer);
  }, [state, appearanceReady]);

  // Appearance never auto-saves. Live preview uses push_appearance_live above;
  // disk write only via Save / Save as profile / intentional profile switches.

  const hasUnsavedChanges = useMemo(
    () => serializeAppearanceState(state) !== savedSnapshotRef.current,
    [state, savedRevision],
  );

  const discardChanges = useCallback(() => {
    const dto = JSON.parse(savedSnapshotRef.current) as AppearanceDto;
    const restored = toState(dto);
    setState(restored);
    setSaveMsg("");
  }, []);

  const guardUnsavedChanges = useCallback((): Promise<boolean> => {
    if (serializeAppearanceState(state) === savedSnapshotRef.current) {
      return Promise.resolve(true);
    }
    return new Promise((resolve) => {
      setLeavePrompt({ resolve });
    });
  }, [state]);

  const dismissLeavePrompt = (allowed: boolean) => {
    if (!leavePrompt) return;
    leavePrompt.resolve(allowed);
    setLeavePrompt(null);
    setLeaveSaving(false);
  };

  const handleLeaveSave = async () => {
    setLeaveSaving(true);
    try {
      const synced = syncPluginPatch(state);
      await persistAppearance(synced);
      const api = getApi();
      if (api?.update_active_appearance_profile && isStoredUserProfile(synced.activeProfileId)) {
        const updated = await api.update_active_appearance_profile();
        if (updated) {
          setState((prev) => {
            const next = {
              ...synced,
              profiles: prev.profiles.map((p) => (p.id === updated.id ? profileFromDto(updated) : p)),
            };
            markPersisted(next);
            return next;
          });
        } else {
          setState(synced);
          markPersisted(synced);
        }
      } else {
        setState(synced);
        markPersisted(synced);
      }
      setSaveMsg("Saved");
      dismissLeavePrompt(true);
    } catch {
      setSaveMsg("Failed to save");
      setLeaveSaving(false);
    }
  };

  const handleLeaveDiscard = () => {
    discardChanges();
    dismissLeavePrompt(true);
  };

  const cssVars = useMemo(() => computeCssVars(state), [state]);
  const styleBlock = useMemo(() => cssVarsToStyleBlock(cssVars), [cssVars]);

  const setFoundation = useCallback((key: FoundationKey, value: string) => {
    setState((prev) => ({ ...prev, foundation: { ...prev.foundation, [key]: value } }));
    setSaveMsg("");
  }, []);

  const resetFoundation = useCallback((key: FoundationKey) => {
    setState((prev) => {
      const baseline = resolveBaseline(prev.activeProfileId, pluginProfilesRef.current);
      return {
        ...prev,
        foundation: { ...prev.foundation, [key]: baseline.foundation[key] },
      };
    });
    setSaveMsg("");
  }, []);

  const isFoundationCustom = useCallback(
    (key: FoundationKey) => {
      const baseline = resolveBaseline(state.activeProfileId, pluginProfiles);
      return (state.foundation[key] || "") !== (baseline.foundation[key] || "");
    },
    [pluginProfiles, state.activeProfileId, state.foundation],
  );

  const setOverride = useCallback((tokenId: string, value: string) => {
    setState((prev) => ({ ...prev, overrides: { ...prev.overrides, [tokenId]: value } }));
    setSaveMsg("");
  }, []);

  const resetOverride = useCallback((tokenId: string) => {
    setState((prev) => {
      const baseline = resolveBaseline(prev.activeProfileId, pluginProfilesRef.current);
      const overrides = { ...prev.overrides };
      const baseVal = baseline.overrides[tokenId];
      if (baseVal) overrides[tokenId] = baseVal;
      else delete overrides[tokenId];
      return { ...prev, overrides };
    });
    setSaveMsg("");
  }, []);

  const resetOverrides = useCallback((tokenIds: string[]) => {
    if (tokenIds.length === 0) return;
    setState((prev) => {
      const baseline = resolveBaseline(prev.activeProfileId, pluginProfilesRef.current);
      const overrides = { ...prev.overrides };
      for (const id of tokenIds) {
        const baseVal = baseline.overrides[id];
        if (baseVal) overrides[id] = baseVal;
        else delete overrides[id];
      }
      return { ...prev, overrides };
    });
    setSaveMsg("");
  }, []);

  const setStatusOverride = useCallback(
    (statusId: string, field: "bg" | "border" | "text" | "dim", value: string) => {
      setState((prev) => {
        const statusOverrides = { ...prev.statusOverrides };
        statusOverrides[statusId] = { ...(statusOverrides[statusId] || {}), [field]: value };
        return { ...prev, statusOverrides };
      });
      setSaveMsg("");
    },
    [],
  );

  const resetStatus = useCallback((statusId: string) => {
    setState((prev) => {
      const baseline = resolveBaseline(prev.activeProfileId, pluginProfilesRef.current);
      const statusOverrides = { ...prev.statusOverrides };
      const baseRow = baseline.statusOverrides[statusId];
      if (baseRow) statusOverrides[statusId] = { ...baseRow };
      else delete statusOverrides[statusId];
      return { ...prev, statusOverrides };
    });
    setSaveMsg("");
  }, []);

  const resetStatuses = useCallback((statusIds: string[]) => {
    if (statusIds.length === 0) return;
    setState((prev) => {
      const baseline = resolveBaseline(prev.activeProfileId, pluginProfilesRef.current);
      const statusOverrides = { ...prev.statusOverrides };
      for (const id of statusIds) {
        const baseRow = baseline.statusOverrides[id];
        if (baseRow) statusOverrides[id] = { ...baseRow };
        else delete statusOverrides[id];
      }
      return { ...prev, statusOverrides };
    });
    setSaveMsg("");
  }, []);

  const applyFontLibrary = useCallback((updater: (lib: FontLibraryState) => FontLibraryState) => {
    setState((prev) => {
      const lib = parseFontLibrary(prev.overrides);
      const nextLib = updater(lib);
      const patch = fontLibraryToOverridesPatch(nextLib);
      const overrides = { ...prev.overrides, ...patch };
      if (!nextLib.uiEnabled) delete overrides["font-ui"];
      if (!nextLib.monoEnabled) delete overrides["font-mono"];
      return { ...prev, overrides };
    });
    setSaveMsg("");
  }, []);

  const addFont = useCallback((role: FontRole, raw: string): string | null => {
    const parsed = parseGoogleFontInput(raw);
    if (!parsed) return null;
    const entry = createFontEntry(role, parsed.family, parsed.href);
    applyFontLibrary((lib) => addFontEntry(lib, entry));
    return entry.name;
  }, [applyFontLibrary]);

  const selectFont = useCallback((role: FontRole, id: string) => {
    applyFontLibrary((lib) => setActiveFont(lib, role, id));
  }, [applyFontLibrary]);

  const removeFont = useCallback((id: string) => {
    applyFontLibrary((lib) => removeFontEntry(lib, id));
  }, [applyFontLibrary]);

  const setFontRoleEnabled = useCallback((role: FontRole, enabled: boolean) => {
    applyFontLibrary((lib) => setFontEnabled(lib, role, enabled));
  }, [applyFontLibrary]);

  const setFontSize = useCallback((role: FontRole, px: number) => {
    applyFontLibrary((lib) => setFontSizeState(lib, role, px));
  }, [applyFontLibrary]);

  const fontLibrary = useMemo(() => parseFontLibrary(state.overrides), [state.overrides]);

  const setEffectId = useCallback(async (id: string) => {
    const nextId = (id || "").trim();
    setState((prev) =>
      syncPluginPatch({
        ...prev,
        effectId: nextId,
        effectsEnabled: nextId ? true : prev.effectsEnabled,
      }),
    );
    setSaveMsg("");
  }, []);

  const setEffectsEnabled = useCallback(async (enabled: boolean) => {
    setState((prev) => {
      let effectId = prev.effectId;
      if (enabled && !effectId && isPluginProfileId(prev.activeProfileId)) {
        const parsed = parsePluginProfileId(prev.activeProfileId);
        if (parsed) {
          const effectMatch =
            effectsRef.current.find(
              (e) =>
                e.plugin_id === parsed.pluginId &&
                (e.id === "planets" || e.id === "matrix"),
            ) || effectsRef.current.find((e) => e.plugin_id === parsed.pluginId);
          if (effectMatch) {
            effectId = pluginEffectId(effectMatch.plugin_id, effectMatch.id);
          }
        }
      }
      return syncPluginPatch({ ...prev, effectsEnabled: enabled, effectId });
    });
    setSaveMsg("");
  }, []);

  const setSkinId = useCallback(async (id: string) => {
    const nextId = (id || "").trim();
    setState((prev) => syncPluginPatch({ ...prev, skinId: nextId }));
    setSaveMsg("");
  }, []);

  const setSounds = useCallback(async (nextSounds: SoundsSettings) => {
    const normalized = normalizeSounds(nextSounds);
    setState((prev) => ({ ...prev, sounds: normalized }));
    setSaveMsg("");
  }, []);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const synced = syncPluginPatch(state);
      await persistAppearance(synced);
      const api = getApi();
      if (api?.update_active_appearance_profile && isStoredUserProfile(synced.activeProfileId)) {
        const updated = await api.update_active_appearance_profile();
        if (updated) {
          setState((prev) => {
            const next = {
              ...synced,
              profiles: prev.profiles.map((p) => (p.id === updated.id ? profileFromDto(updated) : p)),
            };
            markPersisted(next);
            return next;
          });
        } else {
          setState(synced);
          markPersisted(synced);
        }
      } else {
        setState(synced);
        markPersisted(synced);
      }
      setSaveMsg("Saved");
    } catch {
      setSaveMsg("Failed to save");
    } finally {
      setSaving(false);
    }
  }, [markPersisted, state]);

  const saveAsProfile = useCallback(async (name: string) => {
    const api = getApi();
    if (!api?.save_appearance_profile) return;
    setSaving(true);
    try {
      await persistAppearance(state);
      const profile = await api.save_appearance_profile(name);
      setState((prev) => {
        const next = {
          ...prev,
          profiles: [...prev.profiles, profileFromDto(profile)],
          activeProfileId: profile.id,
        };
        markPersisted(next);
        return next;
      });
      setSaveMsg(`Profile "${profile.name}" saved`);
    } catch {
      setSaveMsg("Failed to save profile");
    } finally {
      setSaving(false);
    }
  }, [markPersisted, state]);

  const loadProfile = useCallback(async (id: string) => {
    setSaving(true);
    try {
      if (isBuiltInProfile(id)) {
        const builtIn = getBuiltInProfile(id);
        const payload = builtInPersistPayload(id);
        if (!builtIn || !payload) {
          throw new Error(`Built-in profile not found: ${id}`);
        }
        const loaded: AppearanceUiState = {
          foundation: { ...defaultFoundation(), ...payload.foundation } as Record<FoundationKey, string>,
          overrides: migrateLegacyFonts({ ...payload.overrides }),
          statusOverrides: { ...payload.statusOverrides },
          profiles: state.profiles,
          activeProfileId: normalizeActiveProfileId(id),
          effectId: "",
          effectsEnabled: false,
          skinId: "",
          profilePatches: state.profilePatches,
          sounds: state.sounds,
        };
        await persistAppearance(loaded);
        setState(loaded);
        markPersisted(loaded);
        setSaveMsg("");
        return;
      }

      if (isPluginProfileId(id)) {
        const pluginProfile = pluginProfilesRef.current.find((p) => p.id === id);
        if (!pluginProfile) {
          throw new Error(`Plugin profile not found: ${id}`);
        }
        const parsed = parsePluginProfileId(id);
        let defaultSkin = "";
        let defaultEffect = "";
        if (parsed) {
          const skinMatch =
            skinsRef.current.find(
              (s) => s.plugin_id === parsed.pluginId && s.id === parsed.localId,
            ) || skinsRef.current.find((s) => s.plugin_id === parsed.pluginId);
          if (skinMatch) defaultSkin = pluginSkinId(skinMatch.plugin_id, skinMatch.id);
          const effectMatch =
            effectsRef.current.find(
              (e) =>
                e.plugin_id === parsed.pluginId &&
                (e.id === "planets" || e.id === "matrix"),
            ) || effectsRef.current.find((e) => e.plugin_id === parsed.pluginId);
          if (effectMatch) defaultEffect = pluginEffectId(effectMatch.plugin_id, effectMatch.id);
        }
        const baseline = resolveBaseline(id, pluginProfilesRef.current);
        const patch = state.profilePatches[id];
        const colors = applyProfilePatch(baseline, patch);
        const nextEffect =
          typeof patch?.effect_id === "string" ? patch.effect_id : defaultEffect;
        const nextEffectsEnabled =
          typeof patch?.effects_enabled === "boolean"
            ? patch.effects_enabled
            : !!nextEffect;
        const nextSkin =
          typeof patch?.skin_id === "string" && patch.skin_id
            ? patch.skin_id
            : defaultSkin;
        const loaded: AppearanceUiState = {
          ...colors,
          profiles: state.profiles,
          activeProfileId: id,
          effectId: nextEffect,
          effectsEnabled: nextEffectsEnabled,
          skinId: nextSkin,
          profilePatches: state.profilePatches,
          sounds: state.sounds,
        };
        const synced = syncPluginPatch(loaded);
        await persistAppearance(synced);
        setState(synced);
        markPersisted(synced);
        setSaveMsg("");
        return;
      }

      const api = getApi();
      if (!api?.load_appearance_profile) return;
      const dto = await api.load_appearance_profile(id);
      // User profiles are colors-only — never carry plugin chrome/effects across.
      const loaded = toState({
        ...dto,
        effect_id: "",
        effects_enabled: false,
        skin_id: "",
        profile_patches: state.profilePatches,
        sounds: state.sounds,
      });
      setState(loaded);
      markPersisted(loaded);
      setSaveMsg("");
    } catch {
      setSaveMsg("Failed to load profile");
    } finally {
      setSaving(false);
    }
  }, [
    markPersisted,
    state.profiles,
    state.effectId,
    state.effectsEnabled,
    state.skinId,
    state.profilePatches,
    state.sounds,
  ]);

  const renameProfile = useCallback(async (id: string, name: string) => {
    if (!isStoredUserProfile(id)) return;
    const api = getApi();
    if (!api?.rename_appearance_profile) return;
    try {
      const updated = await api.rename_appearance_profile(id, name);
      setState((prev) => ({
        ...prev,
        profiles: prev.profiles.map((p) => (p.id === id ? profileFromDto(updated) : p)),
      }));
      setSaveMsg(`Renamed to "${updated.name}"`);
    } catch {
      setSaveMsg("Failed to rename profile");
    }
  }, []);

  const deleteProfile = useCallback(async (id: string) => {
    if (!isStoredUserProfile(id)) return;
    const api = getApi();
    if (!api?.delete_appearance_profile) return;
    try {
      const wasActive = state.activeProfileId === id;
      await api.delete_appearance_profile(id);
      if (wasActive && api.get_appearance) {
        // Backend snaps working colors to Default when the active profile is removed.
        const dto = await api.get_appearance();
        const loaded = toState(dto);
        setState(loaded);
        markPersisted(loaded);
      } else {
        setState((prev) => {
          const next = {
            ...prev,
            profiles: prev.profiles.filter((p) => p.id !== id),
            activeProfileId: prev.activeProfileId === id ? DEFAULT_PROFILE_ID : prev.activeProfileId,
          };
          markPersisted(next);
          return next;
        });
      }
      setSaveMsg("Profile deleted");
    } catch {
      setSaveMsg("Failed to delete profile");
    }
  }, [markPersisted, state.activeProfileId]);

  // Theme/skin/fx faults clear the appearance surface only — never brick the app.
  useEffect(() => {
    const onFault = (ev: Event) => {
      const detail = (ev as CustomEvent<{ pluginId?: string; surface?: string }>).detail;
      const surface = String(detail?.surface || "");
      if (surface === "fx") {
        void setEffectId("");
        return;
      }
      // skin / css / default → drop skin (css follows allowedPlugins)
      void setSkinId("");
    };
    window.addEventListener("ducky-plugin-theme-fault", onFault);
    return () => window.removeEventListener("ducky-plugin-theme-fault", onFault);
  }, [setEffectId, setSkinId]);

  const value: AppearanceContextValue = {
    foundation: state.foundation,
    overrides: state.overrides,
    statusOverrides: state.statusOverrides,
    profiles: profilesForUi(state.profiles, pluginProfiles),
    activeProfileId: state.activeProfileId,
    effectId: state.effectId,
    setEffectId,
    effectsEnabled: state.effectsEnabled,
    setEffectsEnabled,
    skinId: state.skinId,
    setSkinId,
    sounds: state.sounds,
    setSounds,
    cssVars,
    styleBlock,
    appearanceReady,
    setFoundation,
    resetFoundation,
    isFoundationCustom,
    setOverride,
    resetOverride,
    resetOverrides,
    setStatusOverride,
    resetStatus,
    resetStatuses,
    save,
    saveAsProfile,
    loadProfile,
    renameProfile,
    deleteProfile,
    canEditActiveProfile: !isReadOnlyProfile(state.activeProfileId),
    hasUnsavedChanges,
    discardChanges,
    guardUnsavedChanges,
    saving,
    saveMsg,
    fontLibrary,
    addFont,
    selectFont,
    removeFont,
    setFontRoleEnabled,
    setFontSize,
  };

  return (
    <AppearanceContext.Provider value={value}>
      {children}
      {leavePrompt ? (
        <UnsavedChangesModal
          message="Appearance has unsaved changes. Save before leaving?"
          saving={leaveSaving}
          onSave={() => void handleLeaveSave()}
          onDiscard={handleLeaveDiscard}
          onCancel={() => dismissLeavePrompt(false)}
        />
      ) : null}
    </AppearanceContext.Provider>
  );
}

export function useAppearance(): AppearanceContextValue {
  const ctx = useContext(AppearanceContext);
  if (!ctx) throw new Error("useAppearance must be used within AppearanceProvider");
  return ctx;
}
