import type { ReactNode } from "react";

export function AppearanceAccordionSplit({
  preview,
  children,
}: {
  preview: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="appearance-details-split">
      <div className="appearance-details-split-main">{children}</div>
      <aside className="appearance-details-split-preview" aria-label="Live preview">
        {preview}
      </aside>
    </div>
  );
}
