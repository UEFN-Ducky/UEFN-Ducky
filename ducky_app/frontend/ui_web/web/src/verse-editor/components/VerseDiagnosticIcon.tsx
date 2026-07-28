interface VerseDiagnosticIconProps {
  errors: number;
  warnings: number;
  size?: number;
}

/** Tab/sidebar icon: error > warning > default Verse icon is chosen by parent. */
export function VerseDiagnosticIcon({ errors, warnings, size = 13 }: VerseDiagnosticIconProps) {
  if (errors > 0) {
    return (
      <img
        src="/verse-workflow/verse-icon-error.svg"
        alt=""
        width={size}
        height={size}
        className="verse-diagnostic-icon verse-diagnostic-icon--error"
        draggable={false}
      />
    );
  }
  if (warnings > 0) {
    return (
      <img
        src="/verse-workflow/verse-icon-warning.svg"
        alt=""
        width={size}
        height={size}
        className="verse-diagnostic-icon verse-diagnostic-icon--warning"
        draggable={false}
      />
    );
  }
  return null;
}
