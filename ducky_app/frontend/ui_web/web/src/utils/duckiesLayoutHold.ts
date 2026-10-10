/**
 * While a Duckies move is being saved, a sidebar reload that started before (or during)
 * the save would paint the old tree back for a moment. Moves hold the layout; loads check
 * the hold and skip their result, and the last release asks for one fresh load.
 */
export interface LayoutHold {
  /** Hold until the returned release runs (safe to call twice). */
  hold: () => () => void;
  /** Bumps on every hold and release: a load that saw another value is stale. */
  epoch: () => number;
  held: () => boolean;
  onReleased: (listener: () => void) => () => void;
}

export function createLayoutHold(): LayoutHold {
  let pending = 0;
  let epoch = 0;
  const listeners = new Set<() => void>();
  return {
    hold: () => {
      pending += 1;
      epoch += 1;
      let released = false;
      return () => {
        if (released) return;
        released = true;
        pending = Math.max(0, pending - 1);
        epoch += 1;
        if (pending === 0) for (const listener of [...listeners]) listener();
      };
    },
    epoch: () => epoch,
    held: () => pending > 0,
    onReleased: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** The one hold shared by this window's Duckies tree and its loader. */
export const duckiesLayoutHold = createLayoutHold();
