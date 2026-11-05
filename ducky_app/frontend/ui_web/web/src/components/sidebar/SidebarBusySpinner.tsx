/**
 * The sidebar's "agent working" ring. Every ring turns: Nov 5 2026 only the first one mounted
 * turned (to save animations), and that one was often off screen (a panel tab, a folded
 * folder), so the rows a person could see all stood still and working agents looked stuck.
 * Rotations run on the compositor, and a hidden window pauses them all.
 */
export function SidebarBusySpinner({ className = "", title, label }: { className?: string; title?: string; label?: string }) {
  return (
    <span
      className={`sidebar-agent-spinner${className ? ` ${className}` : ""}`}
      title={title}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}
