/**
 * Each job that has its own default model: what it is called, what to pick for it, and where
 * it is saved. The welcome setup (Your defaults) and Settings → LLMs → Default models both
 * list these, so the two never drift apart.
 */
import { getApi } from "../hooks/usePanelApi";
import { loadVoiceSettings, saveVoiceSettings } from "../voice/voiceSettings";

export type ModelJob = { id: "chat" | "live" | "spoken" | "memory"; title: string; suggest: string; empty: string };

export const MODEL_JOBS: ModelJob[] = [
  {
    id: "chat",
    title: "Chats and duckies",
    suggest: "Your strongest model: it writes Verse, builds levels and uses tools. Try Claude Sonnet or Opus, GPT or Gemini Pro.",
    empty: "Pick a model",
  },
  {
    id: "live",
    title: "Live voice",
    suggest: "A fast, small model that answers you out loud right away while the ducky keeps working. Try Claude Haiku, GPT mini or Gemini Flash.",
    empty: "The chat's model",
  },
  {
    id: "spoken",
    title: "Spoken summaries",
    suggest: "Your cheapest model: it only shortens replies before they are read aloud.",
    empty: "Chats model",
  },
  {
    id: "memory",
    title: "Memory summaries",
    suggest: "A cheap model that shrinks older messages in long chats. Empty uses the spoken summaries model, then the chats model.",
    empty: "Spoken summaries model",
  },
];

export type JobModels = Record<ModelJob["id"], string>;

export const NO_JOB_MODELS: JobModels = { chat: "", live: "", spoken: "", memory: "" };

export function saveJobModel(id: ModelJob["id"], next: string): Promise<unknown> {
  const api = getApi();
  if (id === "chat") return Promise.resolve(api?.save_agent_settings({ default_model: next }));
  if (id === "live") return saveVoiceSettings({ liveModel: next });
  if (id === "spoken") return saveVoiceSettings({ summaryModel: next });
  return Promise.resolve(api?.set_memory_settings?.({ memory_summary_model: next }));
}

/** Reads each job's saved model; `put` gets them as they arrive. Resolves once the chats model is in. */
export function loadJobModels(put: (patch: Partial<JobModels>) => void): Promise<void> {
  const api = getApi();
  if (!api?.get_settings) return Promise.resolve();
  void loadVoiceSettings()
    .then((v) => put({ live: v.liveModel, spoken: v.summaryModel }))
    .catch(() => undefined);
  void Promise.resolve(api.get_memory_settings?.())
    .then((m) => put({ memory: String(m?.memory_summary_model || "") }))
    .catch(() => undefined);
  return Promise.resolve(api.get_settings())
    .then((s) => put({ chat: ((s as { default_model?: string })?.default_model || "").trim() }))
    .catch(() => undefined);
}
