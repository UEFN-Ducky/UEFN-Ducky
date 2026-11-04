/**
 * The sidebar's "agent working" ring. Only one of them turns at a time: the first one
 * mounted; the others show the same ring standing still. With 10 agents working the
 * sidebar ran 11 spin animations, and each one kept the compositor making frames for it.
 * When the turning one goes away, the next one takes over.
 */
import { useEffect, useId, useSyncExternalStore } from "react";

const order: string[] = [];
const listeners = new Set<() => void>();

function emit(): void {
  for (const fn of [...listeners]) fn();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** The spinner that turns now ("" when none is mounted). */
export function leadSpinner(): string {
  return order[0] ?? "";
}

export function SidebarBusySpinner({ className = "", title, label }: { className?: string; title?: string; label?: string }) {
  const id = useId();
  useEffect(() => {
    order.push(id);
    if (order.length === 1) emit();
    return () => {
      const at = order.indexOf(id);
      if (at >= 0) order.splice(at, 1);
      if (at === 0) emit();
    };
  }, [id]);
  const lead = useSyncExternalStore(subscribe, leadSpinner, leadSpinner) === id;
  return (
    <span
      className={`sidebar-agent-spinner sidebar-agent-spinner--shared${lead ? " is-lead" : ""}${className ? ` ${className}` : ""}`}
      title={title}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}
