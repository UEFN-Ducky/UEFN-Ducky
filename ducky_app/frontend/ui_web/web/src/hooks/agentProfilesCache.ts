import { BLANK_PROFILE_ID } from "../generated/bundledAgentProfiles";
import type { AgentProfileDto, AgentProfileEditorCatalogDto } from "../types/panel";

/** In-memory duckies library — survives Settings tab switches (same session). */
export type AgentProfilesCache = {
  profiles: AgentProfileDto[];
  templateProfiles: AgentProfileDto[];
  blankProfileId: string;
  catalog: AgentProfileEditorCatalogDto | null;
};

let memory: AgentProfilesCache | null = null;

export function peekAgentProfilesCache(): AgentProfilesCache | null {
  return memory;
}

export function rememberAgentProfilesList( partial: {
  profiles: AgentProfileDto[];
  templateProfiles?: AgentProfileDto[];
  blankProfileId?: string;
}): void {
  memory = {
    profiles: partial.profiles,
    templateProfiles: partial.templateProfiles ?? memory?.templateProfiles ?? [],
    blankProfileId: partial.blankProfileId || memory?.blankProfileId || BLANK_PROFILE_ID,
    catalog: memory?.catalog ?? null,
  };
}

export function rememberAgentProfileCatalog(catalog: AgentProfileEditorCatalogDto): void {
  memory = {
    profiles: memory?.profiles ?? [],
    templateProfiles: memory?.templateProfiles ?? [],
    blankProfileId: memory?.blankProfileId || BLANK_PROFILE_ID,
    catalog,
  };
}
