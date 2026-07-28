import { getApi } from "./usePanelApi";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Run an allowlisted PanelApi method on a Python worker thread.
 * Polls every ~100ms so the pywebview bridge stays free (file tree, chat, agents).
 */
export async function runBridgeJob<T = Record<string, unknown>>(
  name: string,
  args: unknown[] = [],
  timeoutMs = 180_000,
): Promise<T> {
  const api = getApi() as Record<string, unknown> | null;
  if (!api) throw new Error("Panel API unavailable");

  const start = api.bridge_job_start as
    | ((n: string, a?: unknown[]) => Promise<Record<string, unknown>>)
    | undefined;
  const poll = api.bridge_job_poll as
    | ((id: string) => Promise<Record<string, unknown>>)
    | undefined;

  if (start && poll) {
    const started = await start(name, args);
    if (!started?.ok || !started.job_id) {
      throw new Error(String(started?.error || `${name} failed to start`));
    }
    const jobId = String(started.job_id);
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await sleep(100);
      const polled = await poll(jobId);
      if (polled?.pending) continue;
      // Non-dict Python returns arrive as { ok, result }.
      if (polled && "result" in polled && polled.result !== undefined) {
        return polled.result as T;
      }
      return polled as T;
    }
    throw new Error(`${name} timed out after ${Math.round(timeoutMs / 1000)}s`);
  }

  // Older app builds: call the sync method directly (blocks bridge).
  const fn = api[name];
  if (typeof fn !== "function") {
    throw new Error(`${name} unavailable — rebuild the app`);
  }
  return (await (fn as (...a: unknown[]) => Promise<T>).apply(api, args)) as T;
}
