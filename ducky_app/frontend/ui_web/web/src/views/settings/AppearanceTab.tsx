import { useEffect, useRef, useState, type ReactNode } from "react";
import { useAppearance } from "../../theme/AppearanceContext";
import { FOUNDATION_KEYS, FOUNDATION_LABELS, type FoundationKey } from "../../theme/defaultTokens";
import { normalizeHex, isHexColor, colorToHexAndAlpha, formatColorWithAlpha } from "../../theme/colorUtils";
import {
  ALL_COLOR_TOKEN_DEFS,
  LAYOUT_TOKENS,
  STATUS_COLORS,
} from "../../theme/tokenEngine";
import { VERSE_COLOR_TOKENS, VERSE_SYNTAX_CATEGORIES } from "../../theme/verseSyntaxTokens";
import {
  APPEARANCE_UI_SECTIONS,
  SEMANTIC_PALETTE_TOKENS,
  VERSE_SECTION_TOKEN_IDS,
  sectionHasCustomOverrides,
  sectionResetTokenIds,
  type AppearanceUiSection,
} from "../../theme/appearanceSections";
import type { TokenDef } from "../../theme/tokenEngine";
import type { VerseTokenDef } from "../../theme/verseSyntaxTokens";
import { VerseSyntaxPreview } from "./VerseSyntaxPreview";
import { FontLibraryContent } from "./FontLibraryPanel";
import { SectionFontPicker } from "./SectionFontPicker";
import { AppearanceAccordionSplit } from "./AppearanceAccordionSplit";
import { AppearanceFontPreview } from "./AppearanceFontPreview";
import { AppearanceLayoutPreview } from "./AppearanceLayoutPreview";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { SettingsToggleRow } from "./SettingsToggleRow";
import { ScopedCss, useScopedClass } from "../../utils/scopedCss";
import { useDockSidePanelMode } from "../../hooks/useDockSidePanelMode";
import type { DockPanelMode, DockSide } from "../../workspace/workspaceDockStorage";
import {
  useEditorTabOverflowMode,
  type EditorTabOverflowMode,
} from "../../hooks/useEditorTabOverflowMode";
import {
  MAX_CHAT_COLUMN_WIDTH,
  MIN_CHAT_COLUMN_WIDTH,
  useChatColumnWidthSetting,
} from "../../hooks/useChatColumnWidth";
import { usePluginContributions } from "../../hooks/usePluginContributions";
import { ChoiceDropdown } from "../../components/ChoiceDropdown";
import { SoundsSection } from "../../sfx/SoundsSection";
import { MATRIX_EFFECT_ID } from "../../theme/matrixFx";
import { pluginEffectId } from "../../theme/appearancePluginIds";
import { isBuiltInProfile } from "../../theme/defaultProfile";

const DETAILS_CHEVRON = (
  <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" fill="none" strokeWidth="2">
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

const RESET_ICON = (
  <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" fill="none" strokeWidth="2">
    <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
    <path d="M3 3v5h5" />
  </svg>
);

function AppearanceDetailsSection({
  title,
  subtitle,
  defaultOpen = false,
  showSectionReset = false,
  onSectionReset,
  sectionResetDisabled = false,
  children,
}: {
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  showSectionReset?: boolean;
  onSectionReset?: () => void;
  sectionResetDisabled?: boolean;
  children: ReactNode;
}) {
  return (
    <details
      className="appearance-details"
      {...(defaultOpen ? ({ defaultOpen: true } as Record<string, unknown>) : {})}
    >
      <summary>
        <div>
          <div className="appearance-tab-details-title">{title}</div>
          {subtitle ? <div className="appearance-tab-details-subtitle">{subtitle}</div> : null}
        </div>
        <div className="appearance-details-summary-actions">
          {showSectionReset && onSectionReset ? (
            <button
              type="button"
              className="appearance-reset-btn appearance-section-reset-btn"
              title="Reset this section to auto"
              aria-label={`Reset ${title} to auto`}
              disabled={sectionResetDisabled}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onSectionReset();
              }}
            >
              {RESET_ICON}
            </button>
          ) : null}
          <div className="appearance-tab-details-chevron">{DETAILS_CHEVRON}</div>
        </div>
      </summary>
      <div className="appearance-details-content">{children}</div>
    </details>
  );
}

const LOCK_ICON = (
  <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" fill="none" strokeWidth="2">
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </svg>
);

const PALETTE_ICON = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <circle cx="13.5" cy="6.5" r=".5" fill="currentColor" />
    <circle cx="17.5" cy="10.5" r=".5" fill="currentColor" />
    <circle cx="8.5" cy="7.5" r=".5" fill="currentColor" />
    <circle cx="6.5" cy="12.5" r=".5" fill="currentColor" />
    <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.563-2.512 5.563-5.563C22 6.012 17.5 2 12 2z" />
  </svg>
);

function OpacitySlider({
  hex,
  alphaPct,
  onChange,
  compact = false,
  disabled = false,
}: {
  hex: string;
  alphaPct: number;
  onChange: (hex: string, alphaPct: number) => void;
  compact?: boolean;
  disabled?: boolean;
}) {
  const [local, setLocal] = useState(alphaPct);
  const debounceRef = useRef<number | null>(null);
  const hexRef = useRef(hex);
  hexRef.current = hex;

  useEffect(() => {
    setLocal(alphaPct);
  }, [alphaPct]);

  useEffect(
    () => () => {
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    },
    [],
  );

  const queueChange = (next: number) => {
    setLocal(next);
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      onChange(hexRef.current, next);
    }, 40);
  };

  const flushChange = () => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    onChange(hexRef.current, local);
  };

  return (
    <label className={`appearance-rgba-alpha-row${compact ? " appearance-rgba-alpha-row--compact" : ""}`}>
      <span className="appearance-rgba-alpha-meta">
        <span>Opacity</span>
        <span>{local}%</span>
      </span>
      <input
        type="range"
        min={0}
        max={100}
        value={local}
        disabled={disabled}
        onChange={(e) => queueChange(Number(e.target.value))}
        onPointerUp={flushChange}
      />
    </label>
  );
}

function ColorSwatchInput({
  value,
  onPickHex,
  variant = "foundation",
  scopeClass,
  surfaceClass,
  wrapClassName,
  disabled = false,
}: {
  value: string;
  onPickHex: (hex: string) => void;
  variant?: "foundation" | "mini";
  /** Single scoped class from useScopedClass — used only in the CSS selector. */
  scopeClass: string;
  /** Base element class(es), e.g. appearance-swatch or appearance-mini-swatch-color. */
  surfaceClass: string;
  wrapClassName?: string;
  disabled?: boolean;
}) {
  const { hex, alphaPct } = colorToHexAndAlpha(value);
  const pickerValue = isHexColor(hex) ? hex : "#000000";
  const surfaceClasses = `${surfaceClass} ${scopeClass}`;
  const translucent = alphaPct < 100;

  if (variant === "mini") {
    const wrapClasses = [
      "appearance-mini-swatch",
      translucent ? "appearance-swatch--translucent" : "",
      wrapClassName || "",
    ]
      .filter(Boolean)
      .join(" ");

    return (
      <div className={wrapClasses}>
        <input
          type="color"
          value={pickerValue}
          disabled={disabled}
          onChange={(e) => onPickHex(e.target.value)}
        />
        <ScopedCss selector={`.${scopeClass}`} rules={{ "--swatch-bg": value }} />
        <div className={surfaceClasses} />
      </div>
    );
  }

  return (
    <>
      <ScopedCss selector={`.${scopeClass}`} rules={{ "--swatch-bg": value }} />
      <div className={`${surfaceClasses}${translucent ? " appearance-swatch--translucent" : ""}`}>
        <input
          type="color"
          value={pickerValue}
          disabled={disabled}
          onChange={(e) => onPickHex(e.target.value)}
        />
      </div>
    </>
  );
}

function FoundationCard({ keyId }: { keyId: FoundationKey }) {
  const {
    foundation,
    setFoundation,
    resetFoundation,
    isFoundationCustom,
    canEditActiveProfile,
  } = useAppearance();
  const value = foundation[keyId];
  const { hex, alphaPct } = colorToHexAndAlpha(value);
  const scopeClass = useScopedClass("appearance-swatch");
  const disabled = !canEditActiveProfile;
  const isCustom = isFoundationCustom(keyId);

  const applyColor = (nextHex: string, alpha: number) => {
    setFoundation(keyId, formatColorWithAlpha(nextHex, alpha));
  };

  return (
    <div className={`appearance-card${isCustom ? " is-custom" : ""}`}>
      <div className="appearance-tab-status-card-actions">
        <span className="appearance-adv-badge" />
        <button
          type="button"
          className="appearance-reset-btn"
          title="Revert to theme default"
          disabled={disabled || !isCustom}
          onClick={() => resetFoundation(keyId)}
        >
          {RESET_ICON}
        </button>
      </div>
      <ColorSwatchInput
        value={value}
        scopeClass={scopeClass}
        surfaceClass="appearance-swatch"
        disabled={disabled}
        onPickHex={(nextHex) => applyColor(nextHex, alphaPct)}
      />
      <div className="appearance-card-title">{FOUNDATION_LABELS[keyId]}</div>
      <div className="appearance-hex-row">
        <span>#</span>
        <input
          type="text"
          className="appearance-hex-input"
          value={hex.replace("#", "").toUpperCase()}
          maxLength={6}
          disabled={disabled}
          onChange={(e) => {
            const next = normalizeHex(e.target.value);
            if (next) applyColor(next, alphaPct);
          }}
        />
      </div>
      <OpacitySlider hex={hex} alphaPct={alphaPct} onChange={applyColor} compact disabled={disabled} />
      {keyId === "bg" && alphaPct < 100 ? (
        <p className="appearance-opacity-hint">
          Lower opacity tints panels and overlays within the app. Blur comes from glass surfaces (header, modals); it does not show the desktop through the window.
        </p>
      ) : null}
    </div>
  );
}

function VerseColorTokenItem({ tokenId }: { tokenId: string }) {
  return <ColorTokenItem tokenId={tokenId} tokens={VERSE_COLOR_TOKENS} />;
}

function ColorTokenItem({
  tokenId,
  tokens = ALL_COLOR_TOKEN_DEFS,
  displayName,
}: {
  tokenId: string;
  tokens?: TokenDef[] | VerseTokenDef[];
  displayName?: string;
}) {
  const { cssVars, overrides, setOverride, resetOverride, canEditActiveProfile } = useAppearance();
  const token = tokens.find((t) => t.id === tokenId);
  const name = displayName ?? token?.name;
  if (!name) return null;

  const value = cssVars[tokenId] || "";
  const isCustom = !!overrides[tokenId];
  const { hex, alphaPct } = colorToHexAndAlpha(value);
  const scopeClass = useScopedClass("appearance-mini-swatch-color");
  const disabled = !canEditActiveProfile;

  const applyColor = (nextHex: string, alpha: number) => {
    setOverride(tokenId, formatColorWithAlpha(nextHex, alpha));
  };

  return (
    <div className={`appearance-adv-item ${isCustom ? "is-custom" : "is-auto"}`}>
      <div className="appearance-tab-color-token-row">
        <ColorSwatchInput
          value={value}
          variant="mini"
          scopeClass={scopeClass}
          surfaceClass="appearance-mini-swatch-color"
          disabled={disabled}
          onPickHex={(nextHex) => applyColor(nextHex, alphaPct)}
        />
        <div className="appearance-tab-color-token-info">
          <div className="appearance-tab-color-token-name">{name}</div>
          <div className="appearance-hex-row appearance-tab-token-hex-row">
            <span className="appearance-tab-token-var-name">--{tokenId}</span>
          </div>
          <OpacitySlider hex={hex} alphaPct={alphaPct} onChange={applyColor} disabled={disabled} />
        </div>
      </div>
      <div className="appearance-tab-token-actions">
        <span className="appearance-adv-badge" />
        <button
          type="button"
          className="appearance-reset-btn"
          title="Revert to auto"
          disabled={disabled}
          onClick={() => resetOverride(tokenId)}
        >
          {RESET_ICON}
        </button>
      </div>
    </div>
  );
}

function LayoutTokenItem({ tokenId }: { tokenId: string }) {
  const { cssVars, overrides, setOverride, resetOverride, canEditActiveProfile } = useAppearance();
  const token = LAYOUT_TOKENS.find((t) => t.id === tokenId);
  if (!token) return null;

  const value = cssVars[tokenId] || "";
  const isCustom = !!overrides[tokenId];
  const disabled = !canEditActiveProfile;

  return (
    <div className={`appearance-adv-item ${isCustom ? "is-custom" : "is-auto"}`}>
      <div className="appearance-tab-layout-info">
        <div className="appearance-tab-layout-name">{token.name}</div>
        <input
          type="text"
          className="settings-input appearance-tab-layout-input"
          value={value}
          disabled={disabled}
          onChange={(e) => setOverride(tokenId, e.target.value)}
        />
        <div className="appearance-tab-layout-var">
          --{tokenId}
        </div>
      </div>
      <div className="appearance-tab-layout-actions">
        <span className="appearance-adv-badge" />
        <button
          type="button"
          className="appearance-reset-btn"
          title="Revert to default"
          disabled={disabled}
          onClick={() => resetOverride(tokenId)}
        >
          {RESET_ICON}
        </button>
      </div>
    </div>
  );
}

function StatusColorField({
  field,
  value,
  onChange,
  disabled = false,
}: {
  field: "text" | "border" | "bg" | "dim";
  value: string;
  onChange: (field: "text" | "border" | "bg" | "dim", color: string) => void;
  disabled?: boolean;
}) {
  const scopeClass = useScopedClass("appearance-status-field-swatch");
  const { hex, alphaPct } = colorToHexAndAlpha(value);

  const applyColor = (nextHex: string, alpha: number) => {
    onChange(field, formatColorWithAlpha(nextHex, alpha));
  };

  return (
    <div className="appearance-tab-status-field">
      <span className="appearance-tab-status-field-label">{field}</span>
      <div className="appearance-tab-status-field-controls">
        <ColorSwatchInput
          value={value}
          variant="mini"
          scopeClass={scopeClass}
          surfaceClass="appearance-mini-swatch-color"
          wrapClassName="appearance-tab-status-mini-swatch"
          disabled={disabled}
          onPickHex={(nextHex) => applyColor(nextHex, alphaPct)}
        />
        <input
          type="text"
          className="appearance-hex-input appearance-tab-status-hex-input"
          value={hex.replace("#", "").toUpperCase()}
          disabled={disabled}
          onChange={(e) => {
            const next = normalizeHex(e.target.value);
            if (next) applyColor(next, alphaPct);
          }}
        />
      </div>
      <OpacitySlider hex={hex} alphaPct={alphaPct} onChange={applyColor} compact disabled={disabled} />
    </div>
  );
}

function StatusCard({ statusId }: { statusId: string }) {
  const { cssVars, statusOverrides, setStatusOverride, resetStatus, canEditActiveProfile } = useAppearance();
  const status = STATUS_COLORS.find((s) => s.id === statusId);
  if (!status) return null;

  const isCustom = !!statusOverrides[statusId];
  const fields = ["text", "border", "bg", "dim"] as const;
  const statusDotClass = useScopedClass("appearance-status-dot");
  const disabled = !canEditActiveProfile;

  return (
    <div className={`appearance-status-card ${isCustom ? "is-custom" : "is-auto"}`}>
      <div className="appearance-tab-status-card-actions">
        <span className="appearance-adv-badge" />
        <button
          type="button"
          className="appearance-reset-btn"
          title="Revert to auto"
          disabled={disabled}
          onClick={() => resetStatus(statusId)}
        >
          {RESET_ICON}
        </button>
      </div>
      <div className="appearance-tab-status-header">
        <ScopedCss
          selector={`.${statusDotClass}`}
          rules={{ "--swatch-bg": cssVars[status.vars.text] }}
        />
        <div className={`appearance-tab-status-dot ${statusDotClass}`} />
        <span className="appearance-tab-status-name">{status.name}</span>
      </div>
      {fields.map((field) => {
        const varKey = status.vars[field];
        const val = cssVars[varKey] || "";
        return (
          <StatusColorField
            key={field}
            field={field}
            value={val}
            disabled={disabled}
            onChange={(f, hex) => setStatusOverride(statusId, f, hex)}
          />
        );
      })}
    </div>
  );
}

function SemanticPaletteTokenItem({ tokenId, name }: { tokenId: string; name: string }) {
  return <ColorTokenItem tokenId={tokenId} displayName={name} />;
}

function EffectsSectionContent() {
  const { effectId, setEffectId, effectsEnabled, setEffectsEnabled } = useAppearance();
  const contrib = usePluginContributions();
  const options = [
    { id: "", label: "None" },
    // Legacy host id (pre-plugin Hacker). Prefer plugin Matrix once Hacker is installed.
    ...(contrib.appearance_effects.some((e) => e.plugin_id === "hacker" && e.id === "matrix")
      ? []
      : [{ id: MATRIX_EFFECT_ID, label: "Matrix" }]),
    ...contrib.appearance_effects.map((e) => ({
      id: pluginEffectId(e.plugin_id, e.id),
      label: e.label,
    })),
  ];

  return (
    <div className="appearance-effects-panel">
      <SettingsToggleRow
        id="appearance-effects-enabled"
        label="Background effect"
        description="Fullscreen animation behind the UI. Themes set a default; disable anytime without losing your choice."
        checked={effectsEnabled}
        onChange={(checked) => void setEffectsEnabled(checked)}
      />
      <label className="appearance-effect-label">
        <span className="appearance-effect-label-text">Effect</span>
        <ChoiceDropdown
          className="appearance-effect-select"
          size="compact"
          aria-label="Background effect"
          mode="radio"
          value={effectId || ""}
          disabled={!effectsEnabled}
          options={options.map((opt) => ({
            value: opt.id,
            label: opt.label,
          }))}
          onChange={(next) => void setEffectId(next)}
        />
      </label>
    </div>
  );
}

function renderUiSectionContent(section: AppearanceUiSection) {
  if (section.kind === "effects") {
    return <EffectsSectionContent />;
  }

  if (section.kind === "sounds") {
    return <SoundsSection />;
  }

  if (section.kind === "status") {
    return (
      <div className="appearance-status-grid">
        {STATUS_COLORS.map((s) => (
          <StatusCard key={s.id} statusId={s.id} />
        ))}
      </div>
    );
  }

  if (section.kind === "layout") {
    return (
      <div className="appearance-adv-grid">
        {LAYOUT_TOKENS.filter((t) => t.id !== "font-ui" && t.id !== "font-mono").map((t) => (
          <LayoutTokenItem key={t.id} tokenId={t.id} />
        ))}
      </div>
    );
  }

  if (section.kind === "semantic") {
    return (
      <div className="appearance-adv-grid">
        {SEMANTIC_PALETTE_TOKENS.map((t) => (
          <SemanticPaletteTokenItem key={t.id} tokenId={t.id} name={t.name} />
        ))}
      </div>
    );
  }

  const tokenIds = section.tokenIds ?? [];
  return (
    <div className="appearance-adv-grid">
      {tokenIds.map((tokenId) => (
        <ColorTokenItem key={`${section.id}-${tokenId}`} tokenId={tokenId} />
      ))}
    </div>
  );
}

function AppearanceUiSectionBlock({ section }: { section: AppearanceUiSection }) {
  const {
    overrides,
    statusOverrides,
    resetOverrides,
    resetStatuses,
    canEditActiveProfile,
  } = useAppearance();

  const hasCustom = sectionHasCustomOverrides(section, overrides, statusOverrides);

  const handleSectionReset = () => {
    if (section.kind === "status") {
      resetStatuses(STATUS_COLORS.map((s) => s.id));
      return;
    }
    resetOverrides(sectionResetTokenIds(section));
  };

  return (
    <AppearanceDetailsSection
      title={section.title}
      subtitle={section.subtitle}
      defaultOpen={section.defaultOpen}
      showSectionReset={hasCustom}
      onSectionReset={handleSectionReset}
      sectionResetDisabled={!canEditActiveProfile}
    >
      {section.fontToken ? <SectionFontPicker fontToken={section.fontToken} /> : null}
      {renderUiSectionContent(section)}
    </AppearanceDetailsSection>
  );
}

function AppearanceVerseSectionBlock() {
  const { overrides, resetOverrides, canEditActiveProfile } = useAppearance();
  const verseFontToken = {
    id: "font-mono",
    role: "mono" as const,
    hint: "Verse editor, tool cards, and code blocks. Defaults to JetBrains Mono.",
  };
  const hasCustom =
    VERSE_SECTION_TOKEN_IDS.some((id) => overrides[id]) || !!overrides["font-mono"];

  return (
    <AppearanceDetailsSection
      title="Verse editor"
      subtitle="Syntax highlighting and editor chrome."
      showSectionReset={hasCustom}
      onSectionReset={() => resetOverrides([...VERSE_SECTION_TOKEN_IDS, "font-mono"])}
      sectionResetDisabled={!canEditActiveProfile}
    >
      <AppearanceAccordionSplit preview={<VerseSyntaxPreview />}>
        <SectionFontPicker fontToken={verseFontToken} />
        {VERSE_SYNTAX_CATEGORIES.map((cat) => {
          const catTokens = VERSE_COLOR_TOKENS.filter((t) => t.cat === cat.id);
          if (catTokens.length === 0) return null;
          return (
            <div key={cat.id}>
              <h4 className="appearance-category-title">{cat.name}</h4>
              <div className="appearance-adv-grid">
                {catTokens.map((t) => (
                  <VerseColorTokenItem key={t.id} tokenId={t.id} />
                ))}
              </div>
            </div>
          );
        })}
      </AppearanceAccordionSplit>
    </AppearanceDetailsSection>
  );
}

function EditorTabOverflowControl() {
  const { mode, setMode } = useEditorTabOverflowMode();

  const onSelect = (next: EditorTabOverflowMode) => {
    if (next !== mode) setMode(next);
  };

  return (
    <div className="appearance-layout-control">
      <h4 className="appearance-category-title">Editor tabs</h4>
      <p className="appearance-layout-control-desc">
        Chrome-style tabs shrink to share space and scroll with the mouse wheel when full. Scrollbar mode
        keeps fixed tab widths and shows a horizontal scrollbar when they overflow.
      </p>
      <div
        className="content-search-mode-toggle appearance-layout-toggle"
        role="group"
        aria-label="Editor tab overflow"
      >
        <button
          type="button"
          className={`content-search-mode-btn${mode === "chrome" ? " is-active" : ""}`}
          aria-pressed={mode === "chrome"}
          onClick={() => onSelect("chrome")}
        >
          Chrome-style
        </button>
        <button
          type="button"
          className={`content-search-mode-btn${mode === "scrollbar" ? " is-active" : ""}`}
          aria-pressed={mode === "scrollbar"}
          onClick={() => onSelect("scrollbar")}
        >
          Scrollbar
        </button>
      </div>
    </div>
  );
}

function ChatColumnWidthControl() {
  const { width, setWidth } = useChatColumnWidthSetting();

  return (
    <div className="appearance-layout-control">
      <h4 className="appearance-category-title">Chat width</h4>
      <p className="appearance-layout-control-desc">
        Sets the centered chat column width. Chat drag handles are disabled.
      </p>
      <label className="appearance-chat-width-control">
        <input
          type="range"
          min={MIN_CHAT_COLUMN_WIDTH}
          max={MAX_CHAT_COLUMN_WIDTH}
          step={20}
          value={width}
          onChange={(event) => setWidth(Number(event.target.value))}
        />
        <input
          type="number"
          className="settings-input appearance-chat-width-input"
          min={MIN_CHAT_COLUMN_WIDTH}
          max={MAX_CHAT_COLUMN_WIDTH}
          value={width}
          onChange={(event) => setWidth(Number(event.target.value))}
        />
        <span>px</span>
      </label>
    </div>
  );
}

function PanelSideLayoutControl({ side }: { side: DockSide }) {
  const { mode, setMode } = useDockSidePanelMode(side);

  const onSelect = (next: DockPanelMode) => {
    if (next !== mode) setMode(next);
  };

  const label = side === "left" ? "Left panel" : "Right panel";

  return (
    <div className="appearance-layout-control">
      <h4 className="appearance-category-title">{label}</h4>
      <p className="appearance-layout-control-desc">
        How every panel docked on the {side} rail lays out. Tabs show one panel at a time behind a tab
        bar; stacked shows all of them at once, split by resizable dividers. This follows the side, not
        the panel — drag all four panels to the {side} and they all pick up this mode.
      </p>
      <div
        className="content-search-mode-toggle appearance-layout-toggle"
        role="group"
        aria-label={`${label} layout`}
      >
        <button
          type="button"
          className={`content-search-mode-btn${mode === "tabs" ? " is-active" : ""}`}
          aria-pressed={mode === "tabs"}
          onClick={() => onSelect("tabs")}
        >
          Tabs
        </button>
        <button
          type="button"
          className={`content-search-mode-btn${mode === "stacked" ? " is-active" : ""}`}
          aria-pressed={mode === "stacked"}
          onClick={() => onSelect("stacked")}
        >
          Stacked panels
        </button>
      </div>
    </div>
  );
}

function AppearanceLayoutSectionBlock() {
  return (
    <AppearanceDetailsSection title="Layout" subtitle="Editor tabs and dock panel layout.">
      <AppearanceAccordionSplit preview={<AppearanceLayoutPreview />}>
        <div className="appearance-layout-controls">
          <ChatColumnWidthControl />
          <EditorTabOverflowControl />
          <PanelSideLayoutControl side="left" />
          <PanelSideLayoutControl side="right" />
        </div>
      </AppearanceAccordionSplit>
    </AppearanceDetailsSection>
  );
}

function AppearanceFontsSectionBlock() {
  return (
    <AppearanceDetailsSection title="Fonts" subtitle="UI font, size, and per-region fonts.">
      <AppearanceAccordionSplit preview={<AppearanceFontPreview />}>
        <FontLibraryContent />
      </AppearanceAccordionSplit>
    </AppearanceDetailsSection>
  );
}

function FoundationSection() {
  return (
    <section className="general-tab-section">
      <GeneralSectionHeader icon={PALETTE_ICON} title="Foundation" />
      <div className="appearance-foundation-grid">
        {FOUNDATION_KEYS.map((key) => (
          <FoundationCard key={key} keyId={key} />
        ))}
      </div>
    </section>
  );
}

export function AppearanceTab() {
  const { canEditActiveProfile, profiles, activeProfileId } = useAppearance();
  const activeProfile = profiles.find((p) => p.id === activeProfileId);
  const activeProfileName = activeProfile?.name ?? "Default";
  const showBuiltInNotice = !canEditActiveProfile && isBuiltInProfile(activeProfileId);

  return (
    <div className={`appearance-tab-root${canEditActiveProfile ? "" : " appearance-tab-root--read-only"}`}>
      {showBuiltInNotice ? (
        <div className="appearance-read-only-notice" role="status">
          {LOCK_ICON}
          <div>
            <strong>{activeProfileName} is read-only</strong>
            Built-in themes can&apos;t be edited. Use the <strong>+</strong> button above to save a copy, then customize. Plugin themes (like Galaxy Craft) are editable — reset falls back to that theme&apos;s defaults. Chrome skins stay with the plugin theme — no separate skin picker.
          </div>
        </div>
      ) : null}

      <div className="general-tab-shell appearance-general-shell">
        <h2 className="general-tab-page-title">Appearance</h2>
        <FoundationSection />
      </div>

      <hr className="general-tab-divider" />

      {APPEARANCE_UI_SECTIONS.map((section) => (
        <AppearanceUiSectionBlock key={section.id} section={section} />
      ))}

      <AppearanceVerseSectionBlock />

      <AppearanceFontsSectionBlock />

      <AppearanceLayoutSectionBlock />
    </div>
  );
}
