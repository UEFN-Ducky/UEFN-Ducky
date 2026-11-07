/**
 * Shown once, right after the first-run setup is finished: "How was getting started?" with
 * 1–5 stars. Picking a star opens an optional suggestions box below it. Submit (enabled once a
 * star is picked) is the only way on, and it hands over to the welcome ducky.
 */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

const WORDS = ["Pick 1 to 5 stars", "Not good", "Okay", "Good", "Great", "Loved it"];
export const SUGGESTION_MAX = 2000;

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

export function SetupRatingModal({ onSubmit }: { onSubmit: (stars: number, suggestion: string) => void }) {
  const [stars, setStars] = useState(0);
  const [hover, setHover] = useState(0);
  const [suggestion, setSuggestion] = useState("");
  const group = useRef<HTMLDivElement>(null);
  const more = useRef<HTMLDivElement>(null);
  const shown = hover || stars;
  const picked = stars > 0;

  useEffect(() => {
    group.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, []);

  // The suggestions box slides open under the stars the first time one is picked.
  useEffect(() => {
    if (picked) more.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, [picked]);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowUp" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = Math.max(1, Math.min(5, stars + step));
    setStars(next);
    group.current?.querySelectorAll<HTMLButtonElement>("button")[next - 1]?.focus();
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
                tabIndex={stars === n || (!picked && n === 1) ? 0 : -1}
                className="setup-rating-btn"
                onMouseEnter={() => setHover(n)}
                onClick={() => setStars(n)}
              >
                <Star on={n <= shown} />
              </button>
            ))}
          </div>
          <p className="setup-rating-word" aria-live="polite">
            {shown ? `${shown} of 5 · ` : ""}
            {WORDS[shown]}
          </p>
          {picked && (
            <div ref={more} className="setup-rating-more">
              <label htmlFor="setup-rating-suggestion">
                Any suggestions? <span className="setup-rating-optional">Optional</span>
              </label>
              <textarea
                id="setup-rating-suggestion"
                className="setup-rating-text"
                rows={4}
                maxLength={SUGGESTION_MAX}
                placeholder="What would have made getting started better?"
                value={suggestion}
                onChange={(e) => setSuggestion(e.target.value)}
              />
            </div>
          )}
        </div>
        <footer className="setup-wizard-foot">
          <span />
          <button
            type="button"
            className="setup-wizard-btn is-primary"
            disabled={!picked}
            title={picked ? undefined : "Pick 1 to 5 stars first"}
            onClick={() => picked && onSubmit(stars, suggestion.trim())}
          >
            Submit
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
