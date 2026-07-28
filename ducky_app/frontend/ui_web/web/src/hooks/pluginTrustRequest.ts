/** Pending AI/local plugin trust prompts (agent enable → user confirm in Store). */

export type PluginTrustRequest = {
  pluginId: string;
  source: string;
  detail: string;
};

let pending: PluginTrustRequest | null = null;
const listeners = new Set<(req: PluginTrustRequest) => void>();

export function queuePluginTrustRequest(req: PluginTrustRequest): void {
  pending = req;
  for (const fn of listeners) {
    try {
      fn(req);
    } catch {
      /* ignore */
    }
  }
}

export function takePluginTrustRequest(): PluginTrustRequest | null {
  const next = pending;
  pending = null;
  return next;
}

export function subscribePluginTrustRequest(fn: (req: PluginTrustRequest) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
