import type { CSSProperties, ReactNode } from "react";
import { usePlanOutlinePanelWidth } from "../hooks/useOutlinePanelWidth";
import { SplitResizeHandle } from "./SplitResizeHandle";

interface PlanDetailSplitProps {
  /** Plan title, overview, markdown body. */
  main: ReactNode;
  /** Steps outline — always stacked under the plan. */
  steps: ReactNode;
  /** Right panel when a subplan is open (resizable). */
  aside?: ReactNode | null;
  className?: string;
}

/** Plan details + steps stacked; optional resizable subplan panel on the right. */
export function PlanDetailSplit({ main, steps, aside = null, className = "" }: PlanDetailSplitProps) {
  const { width, onResize, persistWidth } = usePlanOutlinePanelWidth();
  const style = { "--plan-outline-width": `${width}px` } as CSSProperties;
  const showAside = aside != null;
  return (
    <div
      className={`plan-detail-split${showAside ? " has-aside" : ""}${className ? ` ${className}` : ""}`}
      style={showAside ? style : undefined}
    >
      <div className="plan-detail-main">
        <div className="plan-detail-main-body">{main}</div>
        <section className="plan-detail-steps" aria-label="Plan steps">
          {steps}
        </section>
      </div>
      {showAside ? (
        <aside className="plan-detail-aside" aria-label="Subplan">
          <SplitResizeHandle
            className="plan-detail-aside-resize"
            onDrag={onResize}
            onDragEnd={persistWidth}
            ariaLabel="Resize subplan panel"
          />
          <div className="plan-detail-aside-inner">{aside}</div>
        </aside>
      ) : null}
    </div>
  );
}
