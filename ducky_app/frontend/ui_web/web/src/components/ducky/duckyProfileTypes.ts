export interface DuckyEditTarget {
  id: string;
  name: string;
  duckyStyle?: string;
  duckyName?: string;
  /** Stable library agent-profile id — prefer over duckyName for identity. */
  profileId?: string;
  duckyPersonality?: string;
  ttsVoice?: string;
  ttsSpeed?: number;
  thinkingEffort?: string;
}
