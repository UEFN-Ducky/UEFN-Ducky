import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type Ref,
  type SetStateAction,
} from "react";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { Icons } from "../../icons/Icons";
import { targetRef } from "../../ui-targets/registry";
import { ChoiceDropdown } from "../ChoiceDropdown";
import { SkillCheckboxList } from "../SkillCheckboxList";
import type { AgentProfileEditorCatalogDto, McpPluginDto, SkillPackDto } from "../../types/panel";
import { fmtCompactTokens } from "../../utils/contextFormat";
import { estimatePersonalityTokens, formatDuckyPersonalityBlock } from "../../utils/duckyPersonality";
import { DuckyAvatar } from "./DuckyAvatars";
import { DuckyMemorySection } from "./DuckyMemorySection";
import { DuckyProfileStats } from "./DuckyProfileStats";
import { useDuckyCatalog } from "./DuckyCatalogContext";
import type { DuckyProfileFormState } from "./duckyProfileForm";
import { modelShowsThinkingEffort } from "./duckyProfileForm";
import { DuckyModelPicker } from "./DuckyModelPicker";
import type { DuckyEditTarget } from "./duckyProfileTypes";
import { EffortSelector } from "../EffortSelector";
import { useTtsVoiceOptions } from "../../voice/pluginVoices";
import { SpeedDropdown } from "../../voice/SpeedDropdown";

function toggleableSubskillIds(pack: SkillPackDto): string[] {
  return pack.subskills.filter((s) => !s.parent_id && !s.always_on).map((s) => s.id);
}

const DUCKY_EDITOR_HERO_SIZE = 120;
const DUCKY_DROPDOWN_PICKER_SIZE = 56;

type PromptExpandKey = "personality" | "whenToUse";

export type DuckyProfileSectionTab = "profile" | "skills" | "mcps" | "memory";

const SECTION_TABS: { id: DuckyProfileSectionTab; label: string }[] = [
  { id: "profile", label: "Profile" },
  { id: "skills", label: "Skills" },
  { id: "mcps", label: "MCPs" },
  { id: "memory", label: "Memory" },
];

export function DuckyProfileSectionTabs({
  value,
  onChange,
  className,
}: {
  value: DuckyProfileSectionTab;
  onChange: (tab: DuckyProfileSectionTab) => void;
  className?: string;
}) {
  return (
    <nav
      className={`ducky-profile-tabs-bar${className ? ` ${className}` : ""}`}
      aria-label="Ducky sections"
    >
      {SECTION_TABS.map((tab) => (
        <button
          key={tab.id}
          ref={targetRef(`settings.duckies.section.${tab.id}`, {
            kind: "tab",
            label: tab.label,
            route: "settings.duckies",
          })}
          type="button"
          className={`ducky-profile-tab${value === tab.id ? " is-active" : ""}`}
          aria-current={value === tab.id ? "page" : undefined}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}

interface DuckyProfileEditorFormProps {
  form: DuckyProfileFormState;
  setForm: Dispatch<SetStateAction<DuckyProfileFormState>>;
  catalog: AgentProfileEditorCatalogDto | null;
  editChat?: DuckyEditTarget | null;
  onDeleteChat?: (chat: DuckyEditTarget) => void;
  deleteActionLabel?: string;
  /** When set, show usage stats for this saved profile (not for new drafts). */
  profileId?: string;
  /** Saved ducky name used to match chats / ledger rows. */
  statsDuckyName?: string;
  /** Controlled section tab (parent owns the tab bar). */
  sectionTab?: DuckyProfileSectionTab;
  onSectionTabChange?: (tab: DuckyProfileSectionTab) => void;
  /** Hide the in-form tab bar when the parent renders it (e.g. editor-tab header). */
  hideTabsBar?: boolean;
}

function autoResizeTextarea(el: HTMLTextAreaElement) {
  el.style.height = "";
  el.style.height = `${el.scrollHeight}px`;
}

function PromptExpandCard({
  id,
  title,
  icon,
  iconClassName,
  value,
  placeholder,
  expanded,
  onExpand,
  onChange,
  textareaRef,
  footer,
}: {
  id: PromptExpandKey;
  title: string;
  icon: ReactNode;
  iconClassName?: string;
  value: string;
  placeholder: string;
  expanded: boolean;
  onExpand: () => void;
  onChange: (next: string) => void;
  textareaRef: Ref<HTMLTextAreaElement>;
  footer?: ReactNode;
}) {
  return (
    <div
      className={`ducky-editor-prompt-card${expanded ? " is-expanded" : ""}`}
      data-prompt-expand={id}
    >
      <div className="ducky-editor-prompt-card-head">
        <span className={`ducky-editor-prompt-card-icon${iconClassName ? ` ${iconClassName}` : ""}`} aria-hidden>
          {icon}
        </span>
        <span className="ducky-editor-prompt-card-title">{title}</span>
      </div>
      <div className="ducky-editor-prompt-card-slot">
        <div
          className={`ducky-editor-prompt-card-panel${expanded ? " is-expanded" : ""}`}
          role={expanded ? undefined : "button"}
          tabIndex={expanded ? undefined : 0}
          aria-expanded={expanded}
          aria-label={expanded ? undefined : `Edit ${title}`}
          onClick={expanded ? undefined : onExpand}
          onKeyDown={
            expanded
              ? undefined
              : (e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onExpand();
                  }
                }
          }
        >
          {expanded ? (
            <textarea
              ref={textareaRef}
              className="ducky-editor-ghost-input selectable-text"
              value={value}
              onChange={(e) => {
                onChange(e.target.value);
                autoResizeTextarea(e.target);
              }}
              placeholder={placeholder}
              rows={4}
            />
          ) : (
            <span className={`ducky-editor-prompt-preview${value.trim() ? "" : " is-placeholder"}`}>
              {value.trim() || placeholder}
            </span>
          )}
        </div>
      </div>
      {footer}
    </div>
  );
}

export function DuckyProfileEditorForm({
  form,
  setForm,
  catalog,
  editChat,
  onDeleteChat,
  deleteActionLabel = "Archive",
  profileId,
  statsDuckyName,
  sectionTab: sectionTabProp,
  onSectionTabChange,
  hideTabsBar = false,
}: DuckyProfileEditorFormProps) {
  const { allStyles, defaultStyle, uploadPng, deleteCustom } = useDuckyCatalog();
  const { alert, confirm } = useConfirmModal();
  const [uploading, setUploading] = useState(false);
  const [avatarPickerOpen, setAvatarPickerOpen] = useState(false);
  const [sectionTabLocal, setSectionTabLocal] = useState<DuckyProfileSectionTab>("profile");
  const sectionTab = sectionTabProp ?? sectionTabLocal;
  const setSectionTab = onSectionTabChange ?? setSectionTabLocal;
  const [expandedPrompt, setExpandedPrompt] = useState<PromptExpandKey | null>(null);
  const voiceOptions = useTtsVoiceOptions();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const heroRef = useRef<HTMLDivElement>(null);
  const personalityRef = useRef<HTMLTextAreaElement>(null);
  const whenToUseRef = useRef<HTMLTextAreaElement>(null);

  const personalityTokens = useMemo(() => {
    const block = formatDuckyPersonalityBlock(form.name, form.personality);
    return estimatePersonalityTokens(block);
  }, [form.name, form.personality]);

  const packs: SkillPackDto[] = catalog?.packs ?? [];
  const tools: McpPluginDto[] = catalog?.tools ?? [];

  const enabledPacks = useMemo(
    () => packs.filter((p) => !form.disabledPacks.includes(p.id)).map((p) => p.id),
    [form.disabledPacks, packs],
  );

  const enabledSubskills = useMemo(() => {
    const out: Record<string, string[]> = { ...form.enabledSubskills };
    for (const pack of packs) {
      if (form.disabledPacks.includes(pack.id)) continue;
      if (out[pack.id]) continue;
      out[pack.id] = toggleableSubskillIds(pack);
    }
    return out;
  }, [form.disabledPacks, form.enabledSubskills, packs]);

  const handlePackToggle = (packId: string, checked: boolean) => {
    setForm((prev) => {
      const pack = packs.find((p) => p.id === packId);
      const nextDisabled = checked
        ? prev.disabledPacks.filter((id) => id !== packId)
        : prev.disabledPacks.includes(packId)
          ? prev.disabledPacks
          : [...prev.disabledPacks, packId];
      const nextSubs = { ...prev.enabledSubskills };
      if (!checked) {
        delete nextSubs[packId];
      } else if (pack) {
        nextSubs[packId] = toggleableSubskillIds(pack);
      }
      return { ...prev, disabledPacks: nextDisabled, enabledSubskills: nextSubs };
    });
  };

  const handleSubskillToggle = (packId: string, subskillId: string, checked: boolean) => {
    setForm((prev) => {
      const pack = packs.find((p) => p.id === packId);
      const fallback = pack ? toggleableSubskillIds(pack) : [];
      const current = prev.enabledSubskills[packId] ?? fallback;
      const next = checked
        ? current.includes(subskillId)
          ? current
          : [...current, subskillId]
        : current.filter((id) => id !== subskillId);
      const nextSubs = { ...prev.enabledSubskills, [packId]: next };
      const nextDisabled =
        next.length === 0
          ? prev.disabledPacks.includes(packId)
            ? prev.disabledPacks
            : [...prev.disabledPacks, packId]
          : prev.disabledPacks.filter((id) => id !== packId);
      if (next.length === 0) delete nextSubs[packId];
      return { ...prev, disabledPacks: nextDisabled, enabledSubskills: nextSubs };
    });
  };

  useEffect(() => {
    if (!avatarPickerOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      const node = heroRef.current;
      if (node && e.target instanceof Node && node.contains(e.target)) return;
      setAvatarPickerOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [avatarPickerOpen]);

  useEffect(() => {
    if (!expandedPrompt) return;
    const onPointerDown = (e: PointerEvent) => {
      if (!(e.target instanceof Node)) return;
      const card = document.querySelector(`[data-prompt-expand="${expandedPrompt}"]`);
      if (card?.contains(e.target)) return;
      setExpandedPrompt(null);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpandedPrompt(null);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [expandedPrompt]);

  useEffect(() => {
    if (expandedPrompt !== "personality") return;
    const el = personalityRef.current;
    if (!el) return;
    autoResizeTextarea(el);
    el.focus({ preventScroll: true });
  }, [expandedPrompt]);

  useEffect(() => {
    if (expandedPrompt === "personality" && personalityRef.current) {
      autoResizeTextarea(personalityRef.current);
    }
  }, [expandedPrompt, form.personality]);

  useEffect(() => {
    if (expandedPrompt !== "whenToUse") return;
    const el = whenToUseRef.current;
    if (!el) return;
    autoResizeTextarea(el);
    el.focus({ preventScroll: true });
  }, [expandedPrompt]);

  useEffect(() => {
    if (expandedPrompt === "whenToUse" && whenToUseRef.current) {
      autoResizeTextarea(whenToUseRef.current);
    }
  }, [expandedPrompt, form.whenToUse]);

  const toggleTool = (toolId: string, checked: boolean) => {
    setForm((prev) => {
      const rest = prev.disabledToolIds.filter((id) => id !== toolId);
      return { ...prev, disabledToolIds: checked ? rest : [...rest, toolId] };
    });
  };

  const handleUpload = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    try {
      const style = await uploadPng(file);
      setForm((prev) => ({ ...prev, duckyStyle: style.id }));
      setAvatarPickerOpen(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Upload failed";
      await alert({ title: "Upload failed", message });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDeleteCustom = async (styleId: string, label: string) => {
    if (!(await confirm({ message: `Delete custom ducky "${label}"?`, confirmLabel: "Delete", danger: true }))) return;
    const ok = await deleteCustom(styleId);
    if (!ok) {
      await alert({ title: "Delete failed", message: "Could not delete ducky." });
      return;
    }
    if (form.duckyStyle === styleId) {
      setForm((prev) => ({ ...prev, duckyStyle: defaultStyle }));
    }
  };

  return (
    <div className="ducky-editor-form ducky-editor-form--profile">
      {hideTabsBar ? null : (
        <DuckyProfileSectionTabs
          className="ducky-profile-tabs-bar--top"
          value={sectionTab}
          onChange={setSectionTab}
        />
      )}

      {sectionTab === "profile" ? (
        <>
          <section className="ducky-editor-identity" ref={heroRef}>
            <div className="ducky-editor-hero-avatar-wrap">
              <button
                type="button"
                className={`ducky-editor-hero-avatar${avatarPickerOpen ? " is-open" : ""}`}
                onClick={() => setAvatarPickerOpen((open) => !open)}
                aria-expanded={avatarPickerOpen}
                aria-label="Choose ducky avatar"
                title="Choose avatar"
              >
                <DuckyAvatar styleId={form.duckyStyle} size={DUCKY_EDITOR_HERO_SIZE} />
                <span className="ducky-editor-hero-camera" aria-hidden>
                  Change
                </span>
              </button>
              {onDeleteChat && editChat ? (
                <button
                  type="button"
                  className="ducky-editor-hero-delete"
                  aria-label={`${deleteActionLabel} ${editChat.name}`}
                  title={`${deleteActionLabel} ducky`}
                  onClick={() => onDeleteChat(editChat)}
                >
                  ×
                </button>
              ) : null}

              {avatarPickerOpen ? (
                <div className="ducky-editor-avatar-dropdown">
                  <div className="ducky-picker-grid ducky-picker-grid--dropdown">
                    {allStyles.map((style) => (
                      <div
                        key={style.id}
                        className={`ducky-picker-cell ${form.duckyStyle === style.id ? "is-selected" : ""}`}
                      >
                        {style.kind === "custom" ? (
                          <button
                            type="button"
                            className="ducky-picker-delete"
                            aria-label={`Delete ${style.label}`}
                            title="Delete custom ducky"
                            onClick={(e) => {
                              e.stopPropagation();
                              void handleDeleteCustom(style.id, style.label);
                            }}
                          >
                            ×
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="ducky-picker-tile ducky-picker-tile--dropdown"
                          onClick={() => {
                            setForm((prev) => ({ ...prev, duckyStyle: style.id }));
                            setAvatarPickerOpen(false);
                          }}
                          aria-pressed={form.duckyStyle === style.id}
                          title={style.label}
                        >
                          <DuckyAvatar styleId={style.id} size={DUCKY_DROPDOWN_PICKER_SIZE} />
                          <span className="ducky-picker-label">{style.label}</span>
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      className="ducky-picker-tile ducky-picker-tile--upload ducky-picker-tile--dropdown"
                      onClick={() => fileInputRef.current?.click()}
                      disabled={uploading}
                      title="Upload custom PNG"
                    >
                      <span className="ducky-picker-upload-icon ducky-picker-upload-icon--dropdown" aria-hidden>
                        +
                      </span>
                      <span className="ducky-picker-label">{uploading ? "Uploading…" : "Add custom"}</span>
                    </button>
                  </div>
                </div>
              ) : null}
            </div>

            <div className="ducky-editor-identity-main">
              <input
                type="text"
                className="ducky-editor-name-ghost"
                value={form.name}
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="Ducky name"
                aria-label="Ducky name"
              />
              <div className="ducky-editor-model-row">
                <DuckyModelPicker
                  variant="chips"
                  model={form.model}
                  onChange={(model) => setForm((prev) => ({ ...prev, model }))}
                  hint=""
                  leadingIcon={<Icons.Brain />}
                />
                {modelShowsThinkingEffort(form.model) ? (
                  <EffortSelector
                    convId={editChat?.id || ""}
                    provider="anthropic"
                    value={form.thinkingEffort || "off"}
                    onChange={(thinkingEffort) => setForm((prev) => ({ ...prev, thinkingEffort }))}
                  />
                ) : null}
              </div>
              <div className="ducky-favorite-models ducky-favorite-models--chips">
                <div className={`ducky-favorite-models-chip${form.ttsVoice.trim() ? " is-selected" : ""}`}>
                  <span className="ducky-favorite-models-chip-rank">Voice</span>
                  <span className="ducky-favorite-models-chip-icon" aria-hidden>
                    <Icons.Mic />
                  </span>
                  <ChoiceDropdown
                    size="compact"
                    aria-label="Ducky voice"
                    mode="radio"
                    value={form.ttsVoice}
                    options={[
                      { value: "", label: "AI Voice default" },
                      ...voiceOptions.map((v) => ({ value: v.id, label: v.label })),
                    ]}
                    onChange={(next) => setForm((prev) => ({ ...prev, ttsVoice: next }))}
                  />
                </div>
                <div className={`ducky-favorite-models-chip${form.ttsSpeed ? " is-selected" : ""}`}>
                  <span className="ducky-favorite-models-chip-rank">Speed</span>
                  <SpeedDropdown
                    size="compact"
                    aria-label="Ducky talking speed"
                    value={form.ttsSpeed || 0}
                    extraOptions={[{ value: "0", label: "AI Voice default" }]}
                    onChange={(next) => setForm((prev) => ({ ...prev, ttsSpeed: next || 0 }))}
                  />
                </div>
              </div>
            </div>
          </section>

          {profileId && (statsDuckyName || form.name).trim() ? (
            <DuckyProfileStats
              profileId={profileId}
              duckyName={(statsDuckyName || form.name).trim()}
            />
          ) : null}

          <section className="ducky-editor-prompt-grid">
            <PromptExpandCard
              id="personality"
              title="Personality & Tone"
              icon={<Icons.Sparkles />}
              value={form.personality}
              placeholder="How should this ducky behave?"
              expanded={expandedPrompt === "personality"}
              onExpand={() => setExpandedPrompt("personality")}
              onChange={(personality) => setForm((prev) => ({ ...prev, personality }))}
              textareaRef={personalityRef}
              footer={
                form.personality.trim() ? (
                  <span className="ducky-editor-personality-hint">
                    Adds ~{fmtCompactTokens(personalityTokens)} to every message context
                  </span>
                ) : null
              }
            />
            <PromptExpandCard
              id="whenToUse"
              title="When to call"
              icon={<Icons.Split />}
              iconClassName="is-split"
              value={form.whenToUse}
              placeholder="When should other duckies delegate to this one?"
              expanded={expandedPrompt === "whenToUse"}
              onExpand={() => setExpandedPrompt("whenToUse")}
              onChange={(whenToUse) => setForm((prev) => ({ ...prev, whenToUse }))}
              textareaRef={whenToUseRef}
            />
          </section>
        </>
      ) : null}

      {sectionTab === "skills" ? (
        packs.length === 0 ? (
          <div className="skill-checkbox-empty">No skill packs available.</div>
        ) : (
          <div className="skill-checkbox-list skill-checkbox-list--cards">
            <p className="ducky-profile-tab-hint">
              Toggle packs off to deny them. Expand a pack to pick which reference files this ducky
              may load — full text still loads on demand.
            </p>
            <SkillCheckboxList
              packs={packs}
              enabledPacks={enabledPacks}
              enabledSubskills={enabledSubskills}
              onPackToggle={handlePackToggle}
              onSubskillToggle={handleSubskillToggle}
              layout="accordion"
              appearance="cards"
              defaultCollapsed
            />
          </div>
        )
      ) : null}

      {sectionTab === "memory" ? (
        <DuckyMemorySection
          duckyName={
            editChat?.duckyName?.trim() ||
            form.name.trim() ||
            editChat?.name?.trim() ||
            ""
          }
        />
      ) : null}

      {sectionTab === "mcps" ? (
        tools.length === 0 ? (
          <div className="skill-checkbox-empty">No tools available.</div>
        ) : (
          <div className="skill-checkbox-list skill-checkbox-list--cards">
            <p className="ducky-profile-tab-hint">
              All tools &amp; MCPs are available by default. Toggle off to deny a group. Schemas load
              only when needed.
            </p>
            {tools.map((tool: McpPluginDto) => {
              const on = !form.disabledToolIds.includes(tool.id);
              return (
                <label
                  key={tool.id}
                  className={`ducky-skill-card ducky-skill-card--solo${on ? " is-enabled" : ""}`}
                >
                  <span className="ducky-skill-card-row">
                    <span className="general-tab-switch general-tab-switch--compact">
                      <input
                        className="general-tab-switch-input"
                        type="checkbox"
                        checked={on}
                        onChange={(e) => toggleTool(tool.id, e.target.checked)}
                      />
                      <span className="general-tab-switch-track" aria-hidden />
                    </span>
                    <span className="ducky-skill-card-copy">
                      <span className="ducky-skill-card-title">{tool.label}</span>
                      <span className="ducky-skill-card-desc">{tool.description || tool.id}</span>
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        )
      ) : null}

      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,.png"
        className="ducky-editor-file-input"
        onChange={(e) => void handleUpload(e.target.files?.[0])}
      />
    </div>
  );
}
