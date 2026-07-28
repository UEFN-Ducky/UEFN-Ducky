import { getApi } from "../hooks/usePanelApi";

export type PluginTranslateResult = {
  ok: boolean;
  language?: string;
  map?: Record<string, string>;
  missing?: string[];
  error?: string;
  provider?: string;
  model?: string;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * UI translate batch via the same pipeline as MCP ``translate_ui_batch``.
 * Non-blocking start/poll so the bridge stays free.
 */
export async function pluginTranslateBatchAsync(
  pluginId: string,
  language: string,
  strings: Record<string, string>,
  model = "",
  timeoutMs = 180_000,
): Promise<PluginTranslateResult> {
  const api = getApi();
  if (api?.plugin_translate_batch_start && api?.plugin_translate_batch_poll) {
    const started = await api.plugin_translate_batch_start(pluginId, language, strings, model);
    if (!started?.ok || !started.job_id) {
      return { ok: false, error: started?.error || "Translate start failed" };
    }
    const jobId = String(started.job_id);
    const deadline = Date.now() + Math.max(30_000, timeoutMs);
    while (Date.now() < deadline) {
      await sleep(100);
      const polled = await api.plugin_translate_batch_poll(jobId);
      if (polled?.pending) continue;
      return {
        ok: !!polled?.ok,
        language: typeof polled?.language === "string" ? polled.language : language,
        map:
          polled?.map && typeof polled.map === "object"
            ? (polled.map as Record<string, string>)
            : undefined,
        missing: Array.isArray(polled?.missing) ? (polled.missing as string[]) : undefined,
        error: polled?.error ? String(polled.error) : undefined,
        provider: polled?.provider ? String(polled.provider) : undefined,
        model: polled?.model ? String(polled.model) : undefined,
      };
    }
    return { ok: false, error: "Translation timed out — try a faster API model." };
  }
  return {
    ok: false,
    error: "plugin_translate_batch unavailable — update UEFN Ducky (needs 1.0.475+)",
  };
}
