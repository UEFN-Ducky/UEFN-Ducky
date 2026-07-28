/** Open a plan editor tab from anywhere (Settings → Plans, etc.). */

export interface OpenPlanRequest {
  chatId: string;
  title?: string;
  /** Explicit project root; "" = app-data. Omit = active project. */
  projectRoot?: string;
}

let openPlanTabFn: ((req: OpenPlanRequest) => void) | null = null;

export function registerOpenPlanTab(fn: (req: OpenPlanRequest) => void): () => void {
  openPlanTabFn = fn;
  return () => {
    if (openPlanTabFn === fn) openPlanTabFn = null;
  };
}

export function requestOpenPlanTab(req: OpenPlanRequest): void {
  const chatId = (req.chatId || "").trim();
  if (!chatId) return;
  openPlanTabFn?.({ ...req, chatId });
}
