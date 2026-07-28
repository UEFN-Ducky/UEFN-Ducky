import { getApi } from "./usePanelApi";

/** Run callback once when pywebview API is available (immediate or on pywebviewready). */
export function onApiReady(cb: (api: NonNullable<ReturnType<typeof getApi>>) => void): () => void {
  const existing = getApi();
  if (existing) {
    cb(existing);
    return () => {};
  }

  let done = false;
  let intervalId: number | undefined;

  const stop = () => {
    window.removeEventListener("pywebviewready", tryReady);
    if (intervalId !== undefined) {
      window.clearInterval(intervalId);
      intervalId = undefined;
    }
  };

  const fire = (api: NonNullable<ReturnType<typeof getApi>>) => {
    if (done) return;
    done = true;
    stop();
    cb(api);
  };

  const tryReady = () => {
    const api = getApi();
    if (api) fire(api);
  };

  window.addEventListener("pywebviewready", tryReady);
  intervalId = window.setInterval(tryReady, 100);

  return stop;
}
