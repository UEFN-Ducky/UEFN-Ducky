/**
 * Open the Pipelines tab — same graph editor as Automations, pipeline catalog.
 */

let openPipelinesTabFn: (() => void) | null = null;

export function registerOpenPipelinesTab(fn: () => void): () => void {
  openPipelinesTabFn = fn;
  return () => {
    if (openPipelinesTabFn === fn) openPipelinesTabFn = null;
  };
}

export function requestOpenPipelinesTab(): void {
  openPipelinesTabFn?.();
}
