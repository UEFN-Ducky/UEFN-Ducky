import { useEffect, useState, type DragEvent } from "react";
import { useAppearance } from "../../theme/AppearanceContext";
import {
  defaultFontSize,
  fontSizeForRole,
  fontsForRole,
  getActiveEntry,
  FONT_SIZE_MAX,
  FONT_SIZE_MIN,
  type FontRole,
} from "../../theme/fontLibrary";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { SettingsToggleRow } from "./SettingsToggleRow";

const FONT_ICON = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <path d="M4 7V4h16v3" />
    <path d="M9 20h6" />
    <path d="M12 4v16" />
  </svg>
);

const RESET_ICON = (
  <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" fill="none" strokeWidth="2">
    <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
    <path d="M3 3v5h5" />
  </svg>
);

function readDropText(event: DragEvent): string {
  return (
    event.dataTransfer.getData("text/uri-list") ||
    event.dataTransfer.getData("text/plain") ||
    ""
  ).trim();
}

function FontSizeControl({ role, disabled }: { role: FontRole; disabled: boolean }) {
  const { fontLibrary, setFontSize } = useAppearance();
  const size = fontSizeForRole(fontLibrary, role);
  const [draft, setDraft] = useState(String(size));

  useEffect(() => {
    setDraft(String(size));
  }, [size]);

  const commit = (raw: string) => {
    const n = Number(raw);
    if (Number.isFinite(n) && raw.trim() !== "") setFontSize(role, n);
    else setDraft(String(size));
  };

  const isDefault = size === defaultFontSize(role);

  return (
    <div className="general-tab-toggle-row appearance-font-size-row">
      <div className="general-tab-toggle-row-text">
        <span className="general-tab-toggle-label">Base size</span>
        <p className="general-tab-toggle-desc">
          {role === "ui"
            ? "Base text size for the interface. Applies with the default font too."
            : "Base size for the Verse editor, terminal, and code blocks. The editor also zooms with Ctrl+scroll."}
        </p>
      </div>
      <div className="appearance-font-size-control">
        <button
          type="button"
          className="appearance-font-size-step"
          aria-label="Decrease size"
          disabled={disabled || size <= FONT_SIZE_MIN}
          onClick={() => setFontSize(role, size - 1)}
        >
          −
        </button>
        <input
          type="number"
          className="settings-input appearance-font-size-input"
          value={draft}
          min={FONT_SIZE_MIN}
          max={FONT_SIZE_MAX}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit((e.target as HTMLInputElement).value);
            }
          }}
        />
        <span className="appearance-font-size-unit">px</span>
        <button
          type="button"
          className="appearance-font-size-step"
          aria-label="Increase size"
          disabled={disabled || size >= FONT_SIZE_MAX}
          onClick={() => setFontSize(role, size + 1)}
        >
          +
        </button>
        <button
          type="button"
          className="appearance-reset-btn appearance-font-size-reset"
          title="Reset to default"
          aria-label="Reset size to default"
          disabled={disabled || isDefault}
          onClick={() => setFontSize(role, defaultFontSize(role))}
        >
          {RESET_ICON}
        </button>
      </div>
    </div>
  );
}

function FontRoleSection({
  role,
  title,
  hint,
  enabledLabel,
  enabledHint,
}: {
  role: FontRole;
  title: string;
  hint: string;
  enabledLabel: string;
  enabledHint: string;
}) {
  const {
    fontLibrary,
    addFont,
    selectFont,
    removeFont,
    setFontRoleEnabled,
    canEditActiveProfile,
  } = useAppearance();
  const [dragOver, setDragOver] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  const disabled = !canEditActiveProfile;
  const enabled = role === "ui" ? fontLibrary.uiEnabled : fontLibrary.monoEnabled;
  const activeId = role === "ui" ? fontLibrary.uiActive : fontLibrary.monoActive;
  const activeEntry = getActiveEntry(fontLibrary, role);
  const roleFonts = fontsForRole(fontLibrary, role);

  const applyInput = (raw: string) => {
    const name = addFont(role, raw);
    if (!name) {
      setError("Drop a Google Fonts link or type a font name");
      return;
    }
    setError("");
    setDraft("");
  };

  const onDragOver = (event: DragEvent) => {
    if (disabled) return;
    event.preventDefault();
    setDragOver(true);
  };

  const onDrop = (event: DragEvent) => {
    if (disabled) return;
    event.preventDefault();
    setDragOver(false);
    applyInput(readDropText(event));
  };

  const handleRemove = (id: string, name: string) => {
    if (id === activeId && !window.confirm(`Remove active font "${name}"?`)) return;
    if (id !== activeId && !window.confirm(`Remove font "${name}"?`)) return;
    removeFont(id);
  };

  return (
    <div className="appearance-font-role-section">
      <h4 className="appearance-font-role-title">{title}</h4>
      <p className="appearance-font-role-hint">{hint}</p>

      <div className="general-tab-toggle-card appearance-font-role-card">
        <SettingsToggleRow
          id={`toggle-font-${role}`}
          label={enabledLabel}
          description={enabledHint}
          checked={enabled}
          disabled={disabled || roleFonts.length === 0}
          onChange={(checked) => setFontRoleEnabled(role, checked)}
        />
        <FontSizeControl role={role} disabled={disabled} />
      </div>

      <div
        className={`appearance-font-drop${dragOver ? " is-drag-over" : ""}${disabled ? " is-disabled" : ""}`}
        onDragOver={onDragOver}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <div className="appearance-font-drop-icon">{FONT_ICON}</div>
        <div className="appearance-font-drop-copy">
          <div className="appearance-font-drop-title">
            {activeEntry && enabled ? (
              <>
                Using <strong style={{ fontFamily: activeEntry.stack }}>{activeEntry.name}</strong>
              </>
            ) : (
              <>Drop a Google Font here</>
            )}
          </div>
          <div className="appearance-font-drop-hint">
            Paste a fonts.google.com link, or type a family name and press Enter
          </div>
          <div className="appearance-font-drop-row">
            <input
              type="text"
              className="settings-input appearance-font-drop-input"
              placeholder="Roboto, JetBrains Mono, https://fonts.google.com/…"
              value={draft}
              disabled={disabled}
              onChange={(e) => {
                setDraft(e.target.value);
                if (error) setError("");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  applyInput(draft);
                }
              }}
            />
            <button
              type="button"
              className="modal-btn modal-btn-primary appearance-font-apply-btn"
              disabled={disabled || !draft.trim()}
              onClick={() => applyInput(draft)}
            >
              Add
            </button>
          </div>
          {error ? <div className="appearance-font-drop-error">{error}</div> : null}
        </div>
      </div>

      {roleFonts.length > 0 ? (
        <div className="appearance-font-tabs" role="tablist" aria-label={`${title} picker`}>
          {roleFonts.map((entry) => {
            const isActive = entry.id === activeId && enabled;
            return (
              <button
                key={entry.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                className={`appearance-font-tab${isActive ? " is-active" : ""}`}
                style={{ fontFamily: entry.stack }}
                disabled={disabled}
                onClick={() => selectFont(role, entry.id)}
              >
                <span className="appearance-font-tab-label">{entry.name}</span>
                {!disabled ? (
                  <span
                    className="appearance-font-tab-remove"
                    role="button"
                    tabIndex={-1}
                    aria-label={`Remove ${entry.name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleRemove(entry.id, entry.name);
                    }}
                  >
                    ×
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

export function FontLibraryContent() {
  return (
    <>
      <FontRoleSection
        role="ui"
        title="UI font"
        hint="Sidebar, search, chat, headers, and general interface text."
        enabledLabel="Use custom UI font"
        enabledHint="When off, the default Inter stack is used everywhere in the UI."
      />
    </>
  );
}

export function FontLibraryPanel() {
  return (
    <section className="general-tab-section appearance-font-section">
      <GeneralSectionHeader
        icon={FONT_ICON}
        title="Fonts"
        description="Add Google Fonts here, then assign them per region in the sections below. Base size applies even when the default font is on."
      />
      <FontLibraryContent />
    </section>
  );
}
