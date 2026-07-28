import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { ScopedCss, useScopedClass } from "../utils/scopedCss";
interface TruncatedTextProps {
  children: ReactNode;
  /** Full text for the hover popover. Defaults to string children. */
  title?: string;
  className?: string;
}

function tipText(children: ReactNode, title?: string): string {
  if (title) return title;
  if (typeof children === "string" || typeof children === "number") return String(children);
  return "";
}

export function TruncatedText({ children, title, className }: TruncatedTextProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const tipScopeClass = useScopedClass("truncate-hover-tip");
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const fullText = tipText(children, title);

  const isTruncated = useCallback(() => {
    const el = ref.current;
    if (!el) return false;
    return el.scrollWidth > el.clientWidth + 1;
  }, []);

  const showTip = useCallback(() => {
    if (!fullText || !isTruncated()) return;
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const maxLeft = Math.max(8, window.innerWidth - 368);
    setTip({
      x: Math.min(rect.left, maxLeft),
      y: rect.bottom + 6,
      text: fullText,
    });
  }, [fullText, isTruncated]);

  const hideTip = useCallback(() => setTip(null), []);

  useEffect(() => {
    if (!tip) return;
    const onScroll = () => hideTip();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [tip, hideTip]);

  return (
    <>
      <span
        ref={ref}
        className={`truncate-text ${className ?? ""}`.trim()}
        onMouseEnter={showTip}
        onMouseLeave={hideTip}
        onFocus={showTip}
        onBlur={hideTip}
      >
        {children}
      </span>
      {tip
        ? createPortal(
            <>
              <ScopedCss
                selector={`.${tipScopeClass}`}
                rules={{
                  "--truncate-tip-left": `${tip.x}px`,
                  "--truncate-tip-top": `${tip.y}px`,
                }}
              />
              <div className={`truncate-hover-tip no-drag is-positioned ${tipScopeClass}`} role="tooltip">
                {tip.text}
              </div>
            </>,
            document.body,
          )
        : null}
    </>
  );
}
