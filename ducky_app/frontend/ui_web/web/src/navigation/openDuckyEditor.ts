import { getApi } from "../hooks/usePanelApi";
import { requestOpenSettings } from "./openSettingsTab";

/** Chat / hover target fields used to find a library profile. */
export type DuckyOpenTarget = {
  name?: string;
  duckyName?: string;
  /** Stable library agent-profile id — preferred over display name. */
  profileId?: string;
  /** Ignored — kept so chat objects can be passed through. */
  id?: string;
  duckyStyle?: string;
  duckyPersonality?: string;
  ttsVoice?: string;
  ttsSpeed?: number;
  thinkingEffort?: string;
};

/**
 * Resolve chat → library profile id.
 * Prefer stored ``profileId`` (survives rename / duplicate names); fall back to
 * a unique case-insensitive name match only when exactly one profile matches.
 */
export function matchDuckyProfileId(
  chat: DuckyOpenTarget,
  profiles: Array<{ id: string; name: string }>,
): string | null {
  const byId = (chat.profileId || "").trim();
  if (byId && profiles.some((p) => p.id === byId)) {
    return byId;
  }
  const label = (chat.duckyName || chat.name || "").trim().toLowerCase();
  if (!label) return null;
  const hits = profiles.filter((p) => p.name.trim().toLowerCase() === label);
  return hits.length === 1 ? hits[0].id : null;
}

/** Match chat → library profile id (id first, then unique name). */
export async function resolveDuckyProfileId(chat: DuckyOpenTarget): Promise<string | null> {
  const api = getApi();
  if (!api?.list_agent_profiles) return null;
  try {
    const res = await api.list_agent_profiles();
    return matchDuckyProfileId(chat, res.profiles ?? []);
  } catch {
    return null;
  }
}

/** Icon / Change ducky: open Settings → Duckies → matching library profile. */
export function requestOpenDuckyEditor(chat: DuckyOpenTarget): void {
  void (async () => {
    const profileId = await resolveDuckyProfileId(chat);
    requestOpenSettings("Duckies", profileId ? { duckyProfileId: profileId } : undefined);
  })();
}
