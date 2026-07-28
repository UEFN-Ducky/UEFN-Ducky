import { describe, expect, it } from "vitest";
import {
  isPluginProfileId,
  parsePluginProfileId,
  parsePluginSkinId,
  pluginProfileId,
  pluginSkinId,
} from "./appearancePluginIds";

/**
 * Mirrors AppearanceContext fallback guards: never wipe a saved plugin theme
 * until contributions are ready AND the plugin is actually disabled.
 */
function shouldFallBackPluginProfile(opts: {
  appearanceReady: boolean;
  contribReady: boolean;
  activeProfileId: string;
  pluginProfileIds: string[];
  enabledIds: string[];
}): boolean {
  if (!opts.appearanceReady || !opts.contribReady) return false;
  if (!isPluginProfileId(opts.activeProfileId)) return false;
  if (opts.pluginProfileIds.includes(opts.activeProfileId)) return false;
  const parsed = parsePluginProfileId(opts.activeProfileId);
  if (!parsed) return true;
  const enabled = opts.enabledIds.some((id) => id.trim().toLowerCase() === parsed.pluginId);
  return !enabled;
}

function shouldClearMissingSkin(opts: {
  appearanceReady: boolean;
  contribReady: boolean;
  skinId: string;
  skinKeys: string[];
  enabledIds: string[];
}): boolean {
  if (!opts.appearanceReady || !opts.contribReady || !opts.skinId) return false;
  const parsed = parsePluginSkinId(opts.skinId);
  if (!parsed) return false;
  if (opts.skinKeys.includes(`${parsed.pluginId}:${parsed.skinId}`)) return false;
  const enabled = opts.enabledIds.some((id) => id.trim().toLowerCase() === parsed.pluginId);
  return !enabled;
}

describe("appearance persist guards", () => {
  const galaxyId = pluginProfileId("galaxycraft", "space");
  const galaxySkin = pluginSkinId("galaxycraft", "chrome");

  it("does not wipe plugin theme before contributions are ready", () => {
    expect(
      shouldFallBackPluginProfile({
        appearanceReady: true,
        contribReady: false,
        activeProfileId: galaxyId,
        pluginProfileIds: [],
        enabledIds: [],
      }),
    ).toBe(false);
  });

  it("keeps theme while plugin is still enabled even if profile list is empty", () => {
    expect(
      shouldFallBackPluginProfile({
        appearanceReady: true,
        contribReady: true,
        activeProfileId: galaxyId,
        pluginProfileIds: [],
        enabledIds: ["galaxycraft"],
      }),
    ).toBe(false);
  });

  it("falls back only after plugin is disabled", () => {
    expect(
      shouldFallBackPluginProfile({
        appearanceReady: true,
        contribReady: true,
        activeProfileId: galaxyId,
        pluginProfileIds: [],
        enabledIds: [],
      }),
    ).toBe(true);
  });

  it("does not clear skin while plugin is still enabled", () => {
    expect(
      shouldClearMissingSkin({
        appearanceReady: true,
        contribReady: true,
        skinId: galaxySkin,
        skinKeys: [],
        enabledIds: ["GalaxyCraft"],
      }),
    ).toBe(false);
  });

  it("clears skin after plugin uninstall", () => {
    expect(
      shouldClearMissingSkin({
        appearanceReady: true,
        contribReady: true,
        skinId: galaxySkin,
        skinKeys: [],
        enabledIds: [],
      }),
    ).toBe(true);
  });
});
