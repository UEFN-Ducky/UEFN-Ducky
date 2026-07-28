import { useAppearance } from "../../theme/AppearanceContext";
import { DEFAULT_CSS_VARS } from "../../theme/defaultTokens";
import { buildMonoFontStack } from "../../theme/googleFont";
import { fontsForRole, type FontEntry } from "../../theme/fontLibrary";
import type { AppearanceFontToken } from "../../theme/appearanceSections";

const RESET_ICON = (
  <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" fill="none" strokeWidth="2">
    <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
    <path d="M3 3v5h5" />
  </svg>
);

const DEFAULT_UI_LABEL = "Inter";
const DEFAULT_MONO_LABEL = "JetBrains Mono";
const DEFAULT_REGIONAL_LABEL = "App font";

function stackForEntry(entry: FontEntry, role: "ui" | "mono"): string {
  return role === "mono" ? buildMonoFontStack(entry.name) : entry.stack;
}

function isActiveStack(current: string, stack: string): boolean {
  return current.trim() === stack.trim();
}

export function SectionFontPicker({ fontToken }: { fontToken: AppearanceFontToken }) {
  const {
    cssVars,
    overrides,
    setOverride,
    resetOverride,
    fontLibrary,
    canEditActiveProfile,
  } = useAppearance();

  const disabled = !canEditActiveProfile;
  const defaultStack = DEFAULT_CSS_VARS[fontToken.id] ?? DEFAULT_CSS_VARS["font-ui"]!;
  const defaultLabel =
    fontToken.role === "mono"
      ? DEFAULT_MONO_LABEL
      : fontToken.id === "font-ui"
        ? DEFAULT_UI_LABEL
        : DEFAULT_REGIONAL_LABEL;
  const current = cssVars[fontToken.id] || defaultStack;
  const isCustom = !!overrides[fontToken.id];
  const libraryFonts = fontsForRole(fontLibrary, "ui");

  const selectDefault = () => resetOverride(fontToken.id);

  const selectEntry = (entry: FontEntry) => {
    setOverride(fontToken.id, stackForEntry(entry, fontToken.role));
  };

  const defaultActive = !isCustom;

  return (
    <div className={`appearance-adv-item appearance-section-font ${isCustom ? "is-custom" : "is-auto"}`}>
      <div className="appearance-section-font-body">
        <div className="appearance-tab-color-token-name">Font</div>
        <p className="appearance-section-font-hint">{fontToken.hint}</p>
        <div className="appearance-font-tabs appearance-section-font-tabs" role="tablist" aria-label="Section font">
          <button
            type="button"
            role="tab"
            aria-selected={defaultActive}
            className={`appearance-font-tab${defaultActive ? " is-active" : ""}`}
            disabled={disabled}
            onClick={selectDefault}
          >
            <span className="appearance-font-tab-label">{defaultLabel}</span>
          </button>
          {libraryFonts.map((entry) => {
            const stack = stackForEntry(entry, fontToken.role);
            const isActive = isCustom && isActiveStack(current, stack);
            return (
              <button
                key={entry.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                className={`appearance-font-tab${isActive ? " is-active" : ""}`}
                style={{ fontFamily: stack }}
                disabled={disabled}
                onClick={() => selectEntry(entry)}
              >
                <span className="appearance-font-tab-label">{entry.name}</span>
              </button>
            );
          })}
        </div>
        {libraryFonts.length === 0 ? (
          <p className="appearance-section-font-empty">Add fonts in the Fonts section above to pick them here.</p>
        ) : null}
      </div>
      <div className="appearance-tab-token-actions">
        <span className="appearance-adv-badge" />
        <button
          type="button"
          className="appearance-reset-btn"
          title="Revert to default"
          disabled={disabled || !isCustom}
          onClick={selectDefault}
        >
          {RESET_ICON}
        </button>
      </div>
    </div>
  );
}
