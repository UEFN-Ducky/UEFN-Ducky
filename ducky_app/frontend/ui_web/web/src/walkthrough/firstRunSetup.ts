/**
 * The first-run setup (Welcome → plugins → AI) runs before the Welcome tour of the app.
 * GatewaySetupNotice reports whether it is showing; WalkthroughHost waits for it.
 */

type Listener = () => void;

let decided = false;
let open = false;
const listeners = new Set<Listener>();

function emit(): void {
  for (const fn of [...listeners]) fn();
}

/** The setup has read the first-run status; `isOpen` is whether it is on screen. */
export function reportFirstRunSetup(isOpen: boolean): void {
  if (decided && open === isOpen) return;
  decided = true;
  open = isOpen;
  emit();
}

export function isFirstRunSetupOpen(): boolean {
  return open;
}

/**
 * Resolves once the setup is not on screen. If it never reports (no panel API),
 * `decideWithinMs` lets the Welcome tour go ahead anyway.
 */
export function whenFirstRunSetupDone(decideWithinMs = 8000): Promise<void> {
  if (decided && !open) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = decided ? undefined : globalThis.setTimeout(() => {
      if (!decided) {
        decided = true;
        emit();
      }
    }, decideWithinMs);
    const check = () => {
      if (decided && !open) {
        if (timer !== undefined) globalThis.clearTimeout(timer);
        listeners.delete(check);
        resolve();
      }
    };
    listeners.add(check);
  });
}

/** Test helper. */
export function _resetFirstRunSetupForTests(): void {
  decided = false;
  open = false;
  listeners.clear();
}
