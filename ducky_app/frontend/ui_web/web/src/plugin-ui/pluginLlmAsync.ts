import { getApi } from "../hooks/usePanelApi";

export type PluginLlmResult = {
  ok: boolean;
  text?: string;
  error?: string;
  provider?: string;
  model?: string;
  cancelled?: boolean;
};

export type PluginLlmAbort = {
  /** When true, stop polling and cancel the bridge job. */
  aborted: boolean;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * LLM complete that does not hold the pywebview bridge for the whole call.
 * Starts a worker job, then polls every ~100ms so the file tree / agents stay live.
 * Pass ``abort`` and set ``abort.aborted = true`` to stop waiting (and cancel the job).
 */
export async function pluginLlmCompleteAsync(
  pluginId: string,
  system: string,
  user: string,
  model = "",
  timeoutMs = 180_000,
  abort?: PluginLlmAbort,
): Promise<PluginLlmResult> {
  const api = getApi();
  const limit = Math.max(30_000, timeoutMs || 180_000);
  if (api?.plugin_llm_start && api?.plugin_llm_poll) {
    const started = await api.plugin_llm_start(pluginId, system, user, model);
    if (!started?.ok || !started.job_id) {
      return { ok: false, error: started?.error || "LLM start failed" };
    }
    const jobId = String(started.job_id);
    const deadline = Date.now() + limit;
    while (Date.now() < deadline) {
      if (abort?.aborted) {
        if (api.plugin_llm_cancel) {
          try {
            await api.plugin_llm_cancel(jobId);
          } catch {
            /* ignore */
          }
        }
        return { ok: false, cancelled: true, error: "Stopped" };
      }
      await sleep(100);
      const polled = await api.plugin_llm_poll(jobId);
      if (polled?.cancelled || (polled?.error === "cancelled" && !polled?.pending)) {
        return { ok: false, cancelled: true, error: "Stopped" };
      }
      if (!polled?.pending) {
        return {
          ok: !!polled?.ok,
          text: typeof polled?.text === "string" ? polled.text : "",
          error: polled?.error ? String(polled.error) : undefined,
          provider: polled?.provider,
          model: polled?.model,
        };
      }
    }
    if (api.plugin_llm_cancel) {
      try {
        await api.plugin_llm_cancel(jobId);
      } catch {
        /* ignore */
      }
    }
    return {
      ok: false,
      error: `Translation timed out after ${Math.round(limit / 1000)}s — try a faster API model or smaller batches.`,
    };
  }
  // Older app builds: sync path (blocks bridge — avoid when possible).
  if (!api?.plugin_llm_complete) {
    return { ok: false, error: "plugin_llm_complete unavailable — rebuild the app" };
  }
  if (abort?.aborted) return { ok: false, cancelled: true, error: "Stopped" };
  const res = await api.plugin_llm_complete(pluginId, system, user, model);
  return {
    ok: !!res?.ok,
    text: typeof res?.text === "string" ? res.text : "",
    error: res?.error ? String(res.error) : undefined,
    provider: res?.provider,
    model: res?.model,
  };
}
