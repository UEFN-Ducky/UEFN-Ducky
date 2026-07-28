import {
  INSTALL_STEPS,
  installProgressPct,
  installStepIndex,
  type CardBusy,
} from "./storeData";

type Props = {
  busy: CardBusy;
  /** card = compact overlay on a store tile; detail = larger panel overlay */
  variant?: "card" | "detail";
};

/** Shared step-by-step install overlay for store cards and the detail pane. */
export function StoreInstallOverlay({ busy, variant = "card" }: Props) {
  const active = installStepIndex(busy.step);
  const pct = installProgressPct(busy);
  const working = busy.phase === "working";
  const errored = busy.phase === "error";
  const queued = Boolean(busy.queued && working);

  return (
    <div
      className={`ds-install-overlay ds-install-overlay--${variant}${errored ? " ds-install-overlay--error" : ""}${queued ? " ds-install-overlay--queued" : ""}`}
      aria-live="polite"
      aria-busy={working}
    >
      <ol className="ds-install-steps">
        {INSTALL_STEPS.map((s, i) => {
          const state = queued
            ? i === 0
              ? "active"
              : "todo"
            : errored && i === active
              ? "error"
              : i < active || busy.phase === "done"
                ? "done"
                : i === active
                  ? "active"
                  : "todo";
          const label = queued && s.id === "download" ? "Pending" : s.label;
          return (
            <li key={s.id} className={`ds-install-step ds-install-step--${state}`}>
              <span className="ds-install-step-mark" aria-hidden>
                {state === "done" ? "✓" : state === "error" ? "!" : state === "active" ? "●" : "○"}
              </span>
              <span>{label}</span>
            </li>
          );
        })}
      </ol>
      <p className="ds-install-overlay-label">{busy.label}</p>
      <div className="ds-progress-block">
        <div
          className="ds-progress-track"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          aria-label={`${pct}%`}
        >
          <div
            className={`ds-progress-fill${working ? "" : " ds-progress-fill--done"}${
              errored ? " ds-progress-fill--error" : ""
            }`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="ds-progress-pct">{pct}%</span>
      </div>
    </div>
  );
}
