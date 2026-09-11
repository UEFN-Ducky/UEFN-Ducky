/**
 * First-enable walkthroughs must not fire when a Store update reloads a plugin.
 * enabled_ids can go [] → full (or drop one slug then add it back); a shrinking
 * "prev" set treats that as a brand-new enable and pops Anthropic / Cursor / …
 */

let seen = new Set<string>();
let seeded = false;

function norm(pluginId: string): string {
  return pluginId.trim().toLowerCase().replace(/^plugin\./, "");
}

export function resetFirstEnableSeenForTests(): void {
  seen = new Set();
  seeded = false;
}

/** Remember a slug so a later contrib refresh cannot look like first-enable. */
export function rememberEnabledPlugin(pluginId: string): void {
  const id = norm(pluginId);
  if (id) seen.add(id);
}

/**
 * Return slugs that should auto-start a first-enable tour.
 * First non-empty snapshot only seeds. Later, only a single new slug starts
 * (Update All / reload adding many back at once is not a user Enable).
 */
export function newlyEnabledForWalkthrough(enabledIds: readonly string[]): string[] {
  const enabled = [
    ...new Set(enabledIds.map(norm).filter(Boolean)),
  ];
  if (!seeded) {
    if (!enabled.length) return [];
    for (const id of enabled) seen.add(id);
    seeded = true;
    return [];
  }
  const fresh: string[] = [];
  for (const id of enabled) {
    if (seen.has(id)) continue;
    seen.add(id);
    fresh.push(id);
  }
  return fresh.length === 1 ? fresh : [];
}
