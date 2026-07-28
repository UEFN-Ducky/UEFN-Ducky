import { useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Icons } from "../../../icons/Icons";

type Props = {
  title: string;
  onOpenSection: () => void;
  /** Optional action beside the section title (e.g. Update All on Installed). */
  headerAction?: ReactNode;
  children: ReactNode;
};

/** One landing row: clickable section title + drag-to-scroll horizontal card strip. */
export function StoreRow({ title, onOpenSection, headerAction, children }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const drag = useRef({ down: false, startX: 0, startScroll: 0, moved: false });
  const [dragging, setDragging] = useState(false);

  const onMouseDown = (e: MouseEvent<HTMLDivElement>) => {
    const el = scrollRef.current;
    if (!el || e.button !== 0) return;
    drag.current = { down: true, startX: e.pageX, startScroll: el.scrollLeft, moved: false };
    setDragging(true);
  };

  const endDrag = () => {
    if (!drag.current.down) return;
    drag.current.down = false;
    setDragging(false);
  };

  const onMouseMove = (e: MouseEvent<HTMLDivElement>) => {
    const el = scrollRef.current;
    if (!drag.current.down || !el) return;
    e.preventDefault();
    const walk = (e.pageX - drag.current.startX) * 1.5;
    if (Math.abs(walk) > 5) drag.current.moved = true;
    el.scrollLeft = drag.current.startScroll - walk;
  };

  // A drag must not fire the card click underneath the pointer.
  const onClickCapture = (e: MouseEvent<HTMLDivElement>) => {
    if (drag.current.moved) {
      e.preventDefault();
      e.stopPropagation();
      drag.current.moved = false;
    }
  };

  return (
    <section className="ds-section">
      <div className="ds-section-head">
        <button type="button" className="ds-section-title" onClick={onOpenSection}>
          <span>{title}</span>
          <span className="ds-section-chevron" aria-hidden>
            <Icons.ChevronRight />
          </span>
        </button>
        {headerAction ? <div className="ds-section-head-action">{headerAction}</div> : null}
      </div>
      <div
        ref={scrollRef}
        className={`ds-row${dragging ? " is-dragging" : ""}`}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
        onClickCapture={onClickCapture}
      >
        {children}
      </div>
    </section>
  );
}
