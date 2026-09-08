import type { CodingAgentDto } from "../types/panel";

/**
 * The coding agents the host currently knows about.
 *
 * Whether `cursor:composer-2.5` names a coding agent or an API provider cannot
 * be decided from the string: both are `backend:model`, and the set of coding
 * agents is whatever gateway plugins are installed. The functions that need to
 * tell them apart all take a `CodingAgentDto[]`, but the ones reached from a
 * plain form helper have no component to thread it from — so the list is cached
 * here when `ModelSelector` loads it, exactly as the model catalog is.
 *
 * Empty is the honest default: before anything has loaded, nothing is known to
 * be a coding agent, and every caller treats that as "Ducky's own API".
 */
let cached: CodingAgentDto[] = [];

export function getCachedCodingAgents(): CodingAgentDto[] {
  return cached;
}

export function setCachedCodingAgents(agents: CodingAgentDto[] | null | undefined): void {
  cached = Array.isArray(agents) ? agents : [];
}

/** Tests only: forget what was loaded. */
export function clearCachedCodingAgents(): void {
  cached = [];
}
