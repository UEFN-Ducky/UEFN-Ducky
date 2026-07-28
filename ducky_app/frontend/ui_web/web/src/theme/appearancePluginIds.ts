/** Stable ids for plugin-contributed appearance profiles / effects. */

export const PLUGIN_PROFILE_PREFIX = "__plugin__:";

export function pluginProfileId(pluginId: string, localId: string): string {
  return `${PLUGIN_PROFILE_PREFIX}${pluginId.trim().toLowerCase()}:${localId.trim()}`;
}

export function isPluginProfileId(id: string | null | undefined): boolean {
  return (id || "").startsWith(PLUGIN_PROFILE_PREFIX);
}

export function parsePluginProfileId(
  id: string,
): { pluginId: string; localId: string } | null {
  if (!isPluginProfileId(id)) return null;
  const rest = id.slice(PLUGIN_PROFILE_PREFIX.length);
  const colon = rest.indexOf(":");
  if (colon <= 0) return null;
  return { pluginId: rest.slice(0, colon), localId: rest.slice(colon + 1) };
}

export function pluginEffectId(pluginId: string, effectId: string): string {
  return `plugin:${pluginId.trim().toLowerCase()}:${effectId.trim()}`;
}

export function parsePluginEffectId(
  id: string,
): { pluginId: string; effectId: string } | null {
  if (!id.startsWith("plugin:")) return null;
  const rest = id.slice("plugin:".length);
  const colon = rest.indexOf(":");
  if (colon <= 0) return null;
  return { pluginId: rest.slice(0, colon), effectId: rest.slice(colon + 1) };
}

/** Same ``plugin:<pluginId>:<id>`` shape as effects. */
export function pluginSkinId(pluginId: string, skinId: string): string {
  return pluginEffectId(pluginId, skinId);
}

export function parsePluginSkinId(
  id: string,
): { pluginId: string; skinId: string } | null {
  const parsed = parsePluginEffectId(id);
  if (!parsed) return null;
  return { pluginId: parsed.pluginId, skinId: parsed.effectId };
}
