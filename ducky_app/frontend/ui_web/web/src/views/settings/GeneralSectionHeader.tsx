import type { ReactNode } from "react";

interface GeneralSectionHeaderProps {
  icon: ReactNode;
  title: string;
  description?: ReactNode;
  /** e.g. plugin walkthrough replay — sits beside the title text. */
  trailing?: ReactNode;
}

export function GeneralSectionHeader({ icon, title, description, trailing }: GeneralSectionHeaderProps) {
  return (
    <div className="general-tab-section-intro">
      <h3 className="general-tab-section-title">
        <span className="general-tab-section-icon" aria-hidden>
          {icon}
        </span>
        <span className="general-tab-section-title-text">{title}</span>
        {trailing}
      </h3>
      {description ? <div className="general-tab-section-desc">{description}</div> : null}
    </div>
  );
}
