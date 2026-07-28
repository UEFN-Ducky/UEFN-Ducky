import type { ReactNode } from "react";

import { DuckyAvatar } from "../ducky/DuckyAvatars";
import { useDuckyCatalogOptional } from "../ducky/DuckyCatalogContext";
import { getCachedChatComposer } from "../../hooks/chatComposerCache";
import type { EditorTabHoverCardPlacement } from "../../hooks/useEditorTabHoverCard";
import { usePluginUiPrefs } from "../../hooks/usePluginUiPrefs";
import {
  pluginContributesSettingsTab,
  usePluginContributions,
} from "../../hooks/usePluginContributions";
import { Icons } from "../../icons/Icons";
import { requestOpenDuckyEditor } from "../../navigation/openDuckyEditor";
import {
  openTranslatedChat,
  requestChatTranslateWalk,
} from "../../navigation/openTranslatedChat";
import {
  autoTranslateAllChatsFromPrefs,
  isAutoTranslateChat,
  toggleAutoTranslateChat,
} from "../../navigation/tabTranslatePrefs";
import type { AgentMode, ChatTab } from "../../types/panel";
import { fmtCompactTokens } from "../../utils/contextFormat";
import { isEnglishLang } from "../../views/settings/translationLanguages";
import { ElapsedTimer } from "../ElapsedTimer";
import { EditorTabHoverCardShell } from "./EditorTabHoverCardShell";

const MODE_LABELS: Record<AgentMode, string> = {
  ask: "Ask",
  plan: "Plan",
  agent: "Agent",
};

/** Sidebar / hover title: the role we created — never the avatar skin (Wizard/Artist). */
export function resolveChatHoverTitle(
  chat: Pick<ChatTab, "name" | "duckyName" | "duckyStyle">,
  styleLabel?: string,
): string {
  return (
    chat.name?.trim() ||
    chat.duckyName?.trim() ||
    styleLabel?.trim() ||
    "Ducky"
  );
}

interface ChatTabHoverCardProps {
  chat: ChatTab;
  isRunning?: boolean;
  hasCompletionAlert?: boolean;
  disabled?: boolean;
  placement?: EditorTabHoverCardPlacement;
  children: ReactNode;
}

export function ChatTabHoverCard({
  chat,
  isRunning = false,
  hasCompletionAlert = false,
  disabled = false,
  placement = "below",
  children,
}: ChatTabHoverCardProps) {
  const catalog = useDuckyCatalogOptional();
  const composer = getCachedChatComposer(chat.id);
  const pluginContrib = usePluginContributions();
  const { prefs, setPref } = usePluginUiPrefs("translation");
  const languagesOn = pluginContributesSettingsTab(pluginContrib, "Languages");
  const lang =
    typeof prefs.language === "string" && prefs.language.trim()
      ? prefs.language.trim()
      : "en";
  // Hide Translate entirely on English — never bounce to Settings.
  const langReady = languagesOn && !isEnglishLang(lang);
  const autoOn = isAutoTranslateChat(chat.id, prefs);
  const globalAutoChats = autoTranslateAllChatsFromPrefs(prefs);
  const duckyName = resolveChatHoverTitle(chat, catalog?.labelFor(chat.duckyStyle));
  const modeLabel = composer ? MODE_LABELS[composer.agentMode] ?? composer.agentMode : null;
  const modelLabel =
    composer?.selectedModelDisplayName?.trim() ||
    composer?.selectedModel?.trim() ||
    null;
  const personality = chat.duckyPersonality?.trim();
  const contextTokens = Math.max(0, Number(chat.contextTokens) || 0);
  // Group members (parent = hub) hide mode/model in the hover strip.
  const hideComposerMeta = Boolean(chat.parentConvId);
  const showModelMeta = !hideComposerMeta && Boolean(modeLabel || modelLabel);

  const enableAndWalk = () => {
    if (!autoOn) toggleAutoTranslateChat(chat.id, prefs, setPref);
    openTranslatedChat(chat);
    window.setTimeout(() => requestChatTranslateWalk(), 50);
  };

  return (
    <EditorTabHoverCardShell
      disabled={disabled}
      placement={placement}
      card={
        <>
          <div className="editor-tab-hover-card-header">
            <button
              type="button"
              className="editor-tab-hover-card-avatar-btn"
              title={`Open ${duckyName} in Settings → Duckies`}
              aria-label={`Open ${duckyName} in Settings → Duckies`}
              onClick={(e) => {
                e.stopPropagation();
                requestOpenDuckyEditor({
                  id: chat.id,
                  name: chat.name,
                  duckyStyle: chat.duckyStyle,
                  duckyName: chat.duckyName,
                  profileId: chat.profileId,
                  duckyPersonality: chat.duckyPersonality,
                  ttsVoice: chat.ttsVoice,
                  ttsSpeed: chat.ttsSpeed,
                  thinkingEffort: chat.thinkingEffort,
                });
              }}
            >
              <DuckyAvatar styleId={chat.duckyStyle} size={44} title={duckyName} />
            </button>
            <div className="editor-tab-hover-card-titles">
              <div className="editor-tab-hover-card-name">{duckyName}</div>
            </div>
          </div>
          {showModelMeta ? (
            <div className="editor-tab-hover-card-meta">
              {modeLabel ? (
                <span className="editor-tab-hover-card-mode" data-mode={composer?.agentMode}>
                  {modeLabel}
                </span>
              ) : null}
              {modeLabel && modelLabel ? (
                <span className="editor-tab-hover-card-sep" aria-hidden="true">
                  ·
                </span>
              ) : null}
              {modelLabel ? (
                <span className="editor-tab-hover-card-model">{modelLabel}</span>
              ) : null}
            </div>
          ) : null}
          {personality ? (
            <div className="editor-tab-hover-card-personality">{personality}</div>
          ) : null}
          {contextTokens > 0 ? (
            <div className="editor-tab-hover-card-folder-total">
              <span>Context</span>
              <span className="editor-tab-hover-card-folder-total-value">
                {fmtCompactTokens(contextTokens)} tokens
              </span>
            </div>
          ) : null}
          {chat.filePath ? (
            <div className="editor-tab-hover-card-path" title={chat.filePath}>
              Linked file: {chat.filePath.replace(/\\/g, "/").split("/").pop()}
            </div>
          ) : null}
          {isRunning ? (
            <div className="editor-tab-hover-card-status editor-tab-hover-card-status--running">
              <span className="sidebar-agent-spinner" aria-hidden="true" />
              <span>Agent working</span>
              <ElapsedTimer chatId={chat.id} when="live" className="elapsed-timer--hover" />
            </div>
          ) : hasCompletionAlert ? (
            <div className="editor-tab-hover-card-status editor-tab-hover-card-status--alert">
              Response ready
              <ElapsedTimer chatId={chat.id} when="idle" idlePrefix="Took" className="elapsed-timer--hover" />
            </div>
          ) : (
            <ElapsedTimer
              chatId={chat.id}
              when="idle"
              idlePrefix="Last turn"
              className="editor-tab-hover-card-status editor-tab-hover-card-status--idle-timer elapsed-timer--hover"
            />
          )}
          {langReady ? (
            <div className="editor-tab-hover-card-actions">
              <button
                type="button"
                className={`editor-tab-hover-card-action-btn${autoOn ? " is-active" : ""}`}
                title="Open this ducky and translate chat messages (including what you asked)"
                onClick={(e) => {
                  e.stopPropagation();
                  enableAndWalk();
                }}
              >
                <Icons.Globe />
                <span>Translate</span>
              </button>
              <button
                type="button"
                className={`editor-tab-hover-card-action-btn${autoOn ? " is-active" : ""}`}
                aria-pressed={autoOn}
                title={
                  globalAutoChats
                    ? autoOn
                      ? "Global auto is on — click to disable auto for this chat only"
                      : "This chat is opted out of global auto — click to allow again"
                    : autoOn
                      ? "Auto on — chat messages stay translated for this ducky"
                      : "Keep translating this ducky’s chat when you open it"
                }
                onClick={(e) => {
                  e.stopPropagation();
                  const nowOn = toggleAutoTranslateChat(chat.id, prefs, setPref);
                  if (nowOn) {
                    openTranslatedChat(chat);
                    window.setTimeout(() => requestChatTranslateWalk(), 50);
                  }
                }}
              >
                <Icons.Globe />
                <span>
                  {globalAutoChats
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

export function resolveEditorChatTab(
  chatId: string,
  tab: { name: string; duckyStyle?: string; isGroup?: boolean },
  allChats: ChatTab[],
): ChatTab {
  const found = allChats.find((c) => c.id === chatId);
  if (found) return { ...found, isGroup: found.isGroup || tab.isGroup };
  return {
    id: chatId,
    name: tab.name,
    duckyStyle: tab.duckyStyle,
    isGroup: Boolean(tab.isGroup),
  };
}
