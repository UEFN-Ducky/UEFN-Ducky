/** After save / delete / duplicate — DuckiesTab + open profile tabs reload. */

export type DuckyProfileChange =
  | { type: "saved" | "duplicated"; profileId: string; name?: string; duckyStyle?: string }
  | { type: "deleted"; profileId: string };

type Listener = (ev: DuckyProfileChange) => void;

const listeners = new Set<Listener>();

export function emitDuckyProfileChanged(ev: DuckyProfileChange): void {
  for (const fn of [...listeners]) fn(ev);
}

export function onDuckyProfileChanged(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}
