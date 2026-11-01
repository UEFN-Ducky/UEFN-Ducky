/**
 * Shown once, right after the first-run setup is finished: "How was getting started?" with
 * 0–5 stars. Submit is the only way on, and it hands over to the welcome ducky.
 */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

const WORDS = ["No stars", "Not good", "Okay", "Good", "Great", "Loved it"];

function Star({ on }: { on: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="32" height="32" aria-hidden>
      <path
        d="M12 2.8l2.8 5.7 6.3.9-4.6 4.4 1.1 6.2L12 17l-5.6 3 1.1-6.2-4.6-4.4 6.3-.9z"
        className={on ? "setup-rating-star is-on" : "setup-rating-star"}
      />
    </svg>
  );
}

export function SetupRatingModal({ onSubmit }: { onSubmit: (stars: number) => void }) {
  const [stars, setStars] = useState(0);
  const [hover, setHover] = useState(0);
  const group = useRef<HTMLDivElement>(null);
  const shown = hover || stars;

  useEffect(() => {
    group.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, []);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowUp" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = Math.max(0, Math.min(5, stars + step));
    setStars(next);
    group.current?.querySelectorAll<HTMLButtonElement>("button")[Math.max(0, next - 1)]?.focus();
  };

  return createPortal(
    <div className="setup-wizard-backdrop no-drag">
      <section className="setup-wizard setup-rating" role="dialog" aria-modal="true" aria-labelledby="setup-rating-title">
        <div className="setup-rating-body">
          <img className="setup-rating-duck" src="./OnlineMCPIcon.png" alt="" draggable={false} />
          <h2 id="setup-rating-title">How was getting started?</h2>
          <p className="setup-wizard-lead">Rate the setup you just went through.</p>
          <div
            ref={group}
            className="setup-rating-stars"
            role="radiogroup"
            aria-label="Stars"
            onKeyDown={onKey}
            onMouseLeave={() => setHover(0)}
          >
            {[1, 2, 3, 4, 5].map((n) => (
              <button
                key={n}
                type="button"
                role="radio"
                aria-checked={stars === n}
                aria-label={`${n} star${n === 1 ? "" : "s"}`}
                tabIndex={stars === n || (stars === 0 && n === 1) ? 0 : -1}
                className="setup-rating-btn"
                onMouseEnter={() => setHover(n)}
                onClick={() => setStars(stars === n ? 0 : n)}
              >
                <Star on={n <= shown} />
              </button>
            ))}
          </div>
          <p className="setup-rating-word" aria-live="polite">
            {shown} of 5 · {WORDS[shown]}
          </p>
        </div>
        <footer className="setup-wizard-foot">
          <span />
          <button type="button" className="setup-wizard-btn is-primary" onClick={() => onSubmit(stars)}>
            Submit
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
