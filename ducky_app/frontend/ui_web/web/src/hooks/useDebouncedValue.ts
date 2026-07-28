import { useEffect, useState } from "react";

/** Returns `value`, delayed by `delayMs` after it last changed. Used to keep filter
 * inputs controlled/immediate while debouncing the value handed to expensive consumers
 * (e.g. sidebar tree filtering). */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
