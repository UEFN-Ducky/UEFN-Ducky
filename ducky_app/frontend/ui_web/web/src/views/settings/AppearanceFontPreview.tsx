import { useAppearance } from "../../theme/AppearanceContext";
import { fontsForRole, getActiveEntry } from "../../theme/fontLibrary";

export function AppearanceFontPreview() {
  const { cssVars, fontLibrary } = useAppearance();
  const active = getActiveEntry(fontLibrary, "ui");
  const enabled = fontLibrary.uiEnabled && !!active;
  const fontFamily = enabled ? active!.stack : cssVars["font-ui"];
  const fontSize = cssVars["font-ui-size"] || "12px";

  return (
    <div className="appearance-live-preview">
      <div className="appearance-live-preview-label">Live preview</div>
      <div
        className="appearance-font-preview-card"
        style={{ fontFamily, fontSize }}
      >
        <div className="appearance-font-preview-title">The quick brown fox</div>
        <p className="appearance-font-preview-body">
          Sidebar labels, chat messages, and headers render at {fontSize}.
        </p>
        <div className="appearance-font-preview-meta">
          {enabled ? active!.name : "Inter (default)"}
        </div>
      </div>
      {fontsForRole(fontLibrary, "ui").length > 0 ? (
        <div className="appearance-font-preview-samples">
          {fontsForRole(fontLibrary, "ui").map((entry) => (
            <div
              key={entry.id}
              className="appearance-font-preview-sample"
              style={{ fontFamily: entry.stack, fontSize }}
            >
              {entry.name}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
