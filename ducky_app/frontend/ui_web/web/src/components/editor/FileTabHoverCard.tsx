import { useSyncExternalStore, type ReactNode } from "react";

import { Icons } from "../../icons/Icons";
import {
  getFileTranslateStatus,
  getFileTranslateStatusSnapshot,
  subscribeFileTranslateStatus,
} from "../../navigation/fileTranslateStatus";
import { openVerseTranslatedTab } from "../../navigation/openVerseTranslatedTab";
import {
  autoTranslateAllFilesFromPrefs,
  canVisualTranslateFile,
  isAutoTranslateFile,
  toggleAutoTranslateFile,
} from "../../navigation/tabTranslatePrefs";
import { EDITOR_TAB_HOVER_CARD_FILE_EST_HEIGHT } from "../../hooks/editorTabHoverCardPosition";
import type { EditorTabHoverCardPlacement } from "../../hooks/useEditorTabHoverCard";
import { usePluginUiPrefs } from "../../hooks/usePluginUiPrefs";
import {
  pluginContributesSettingsTab,
  usePluginContributions,
} from "../../hooks/usePluginContributions";
import type { EditorTab } from "../../types/panel";
import { FileTypeIcon } from "../../verse-editor/components/FileTypeIcon";
import { isEnglishLang } from "../../views/settings/translationLanguages";
import { EditorTabHoverCardShell } from "./EditorTabHoverCardShell";

interface FileTabHoverCardProps {
  tab: EditorTab;
  isDirty?: boolean;
  diagnosticErrors?: number;
  diagnosticWarnings?: number;
  disabled?: boolean;
  placement?: EditorTabHoverCardPlacement;
  /** Override; default = any openable text file when a UI language is active. */
  showVisualTranslate?: boolean;
  children: ReactNode;
}

export function FileTabHoverCard({
  tab,
  isDirty = false,
  diagnosticErrors = 0,
  diagnosticWarnings = 0,
  disabled = false,
  placement = "below",
  showVisualTranslate,
  children,
}: FileTabHoverCardProps) {
  const path = tab.path?.replace(/\\/g, "/") ?? "";
  const hasDiagnostics = diagnosticErrors > 0 || diagnosticWarnings > 0;
  const pluginContrib = usePluginContributions();
  const { prefs, setPref } = usePluginUiPrefs("translation");
  const languagesOn = pluginContributesSettingsTab(pluginContrib, "Languages");
  const lang =
    typeof prefs.language === "string" && prefs.language.trim()
      ? prefs.language.trim()
      : "en";
  const langReady = languagesOn && !isEnglishLang(lang);
  const offerTranslate =
    langReady &&
    !!path &&
    (showVisualTranslate ?? canVisualTranslateFile(path));
  const autoOn = path ? isAutoTranslateFile(path, prefs) : false;
  const globalAutoFiles = autoTranslateAllFilesFromPrefs(prefs);

  useSyncExternalStore(subscribeFileTranslateStatus, getFileTranslateStatusSnapshot, () => 0);
  const tx = path && langReady ? getFileTranslateStatus(path, lang) : { phase: "idle" as const, label: "" };
  const translating = tx.phase === "translating";
  const cached = tx.phase === "cached";

  return (
    <EditorTabHoverCardShell
      disabled={disabled}
      placement={placement}
      cardHeight={offerTranslate ? 180 : EDITOR_TAB_HOVER_CARD_FILE_EST_HEIGHT}
      card={
        <>
          <div className="editor-tab-hover-card-header">
            <div className="editor-tab-hover-card-icon">
              <FileTypeIcon
                path={path}
                size={44}
                diagnosticErrors={diagnosticErrors}
                diagnosticWarnings={diagnosticWarnings}
              />
            </div>
            <div className="editor-tab-hover-card-titles">
              <div className="editor-tab-hover-card-name">{tab.name}</div>
              {path ? (
                <div className="editor-tab-hover-card-subtitle" title={path}>
                  {path}
                </div>
              ) : null}
            </div>
          </div>
          {isDirty ? (
            <div className="editor-tab-hover-card-status editor-tab-hover-card-status--dirty">
              Unsaved changes
            </div>
          ) : null}
          {translating ? (
            <div className="editor-tab-hover-card-status editor-tab-hover-card-status--running">
              <span className="sidebar-agent-spinner" aria-hidden="true" />
              {tx.label || "Translating…"}
            </div>
          ) : cached ? (
            <div className="editor-tab-hover-card-status editor-tab-hover-card-status--alert">
              {tx.label || "Cached"} — open Translate for instant view
            </div>
          ) : null}
          {hasDiagnostics ? (
            <div className="editor-tab-hover-card-meta">
              {diagnosticErrors > 0 ? (
                <span className="editor-tab-hover-card-diag editor-tab-hover-card-diag--error">
                  {diagnosticErrors} error{diagnosticErrors === 1 ? "" : "s"}
                </span>
              ) : null}
              {diagnosticErrors > 0 && diagnosticWarnings > 0 ? (
                <span className="editor-tab-hover-card-sep" aria-hidden="true">
                  ·
                </span>
              ) : null}
              {diagnosticWarnings > 0 ? (
                <span className="editor-tab-hover-card-diag editor-tab-hover-card-diag--warning">
                  {diagnosticWarnings} warning{diagnosticWarnings === 1 ? "" : "s"}
                </span>
              ) : null}
            </div>
          ) : null}
          {offerTranslate ? (
            <div className="editor-tab-hover-card-actions">
              <button
                type="button"
                className={`editor-tab-hover-card-action-btn${cached ? " is-active" : ""}${
                  translating ? " is-busy" : ""
                }`}
                disabled={translating}
                title={
                  translating
                    ? "Translation in progress…"
                    : cached
                      ? "Open cached visual translation (entire file)"
                      : "Translate entire file (all words) — result is cached"
                }
                onClick={(e) => {
                  e.stopPropagation();
                  openVerseTranslatedTab(path);
                }}
              >
                <Icons.Globe />
                <span>
                  {translating ? "Translating…" : cached ? "Open cached" : "Translate"}
                </span>
              </button>
              <button
                type="button"
                className={`editor-tab-hover-card-action-btn${autoOn ? " is-active" : ""}`}
                aria-pressed={autoOn}
                disabled={translating}
                title={
                  globalAutoFiles
                    ? autoOn
                      ? "Global auto is on — click to disable auto for this file only"
                      : "This file is opted out of global auto — click to allow again"
                    : autoOn
                      ? "Auto on for this file — opens translated view when you open it"
                      : "Auto translate this file only when opened"
                }
                onClick={(e) => {
                  e.stopPropagation();
                  const nowOn = toggleAutoTranslateFile(path, prefs, setPref);
                  if (nowOn) openVerseTranslatedTab(path);
                }}
              >
                <Icons.Globe />
                <span>
                  {globalAutoFiles
                    ? autoOn
                      ? "Auto on"
                      : "Auto off"
                    : autoOn
                      ? "Auto on"
                      : "Auto translate"}
                </span>
              </button>
            </div>
          ) : null}
        </>
      }
    >
      {children}
    </EditorTabHoverCardShell>
  );
}
