import { useCallback, useMemo, useState } from "react";
import { DuckyModelPicker } from "../../components/ducky/DuckyModelPicker";
import { Icons } from "../../icons/Icons";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getApi } from "../../hooks/usePanelApi";
import { usePluginUiPrefs } from "../../hooks/usePluginUiPrefs";
import { verseLangSlug } from "../../navigation/verseFileTranslate";
import { checkLanguageName } from "./languageNames";
import {
  isEnglishLang,
  normalizeLanguageCode,
  parseCustomLanguages,
  serializeCustomLanguages,
} from "./translationLanguages";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { PluginWalkthroughReplayButton } from "./PluginWalkthroughReplayButton";
import { useUiTarget } from "../../ui-targets/registry";
import "./languages-tab.css";

const PLUGIN_ID = "translation";

/** Host React Settings form for the Translation plugin (theme + model picker + language checks). */
export function LanguagesTab() {
  const { prefs, setPref, setPrefs } = usePluginUiPrefs(PLUGIN_ID);
  const { confirm, alert } = useConfirmModal();
  const modelTargetRef = useUiTarget("settings.languages.model", {
    kind: "dropdown",
    label: "Translation model",
    route: "settings.languages",
  });
  const langListRef = useUiTarget("settings.languages.list", {
    kind: "settings_field",
    label: "Your languages",
    route: "settings.languages",
  });
  const langAddRef = useUiTarget("settings.languages.add", {
    kind: "input",
    label: "Add language",
    route: "settings.languages",
  });
  const langStartRef = useUiTarget("settings.languages.start", {
    kind: "settings_field",
    label: "Start translation",
    route: "settings.languages",
  });
  const language = typeof prefs.language === "string" ? prefs.language.trim() : "en";
  const model = typeof prefs.model === "string" ? prefs.model.trim() : "";
  const languages = useMemo(() => parseCustomLanguages(prefs.languages), [prefs.languages]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const selectLanguage = useCallback(
    (code: string) => {
      const next = code.trim() || "en";
      setPref("language", next);
      setNote(
        isEnglishLang(next)
          ? "UI language: English (off)"
          : `Selected ${next} — press Start`,
      );
    },
    [setPref],
  );

  const startTranslate = useCallback(() => {
    if (isEnglishLang(language)) {
      setNote("Pick a language first, then press Start.");
      return;
    }
    setPref("translateStart", String(Date.now()));
    setNote(`Starting ${language}…`);
  }, [language, setPref]);

  const commitLanguage = useCallback(
    (name: string) => {
      const code = normalizeLanguageCode(name);
      if (!code) return;
      if (isEnglishLang(code)) {
        selectLanguage("en");
        setDraft("");
        return;
      }
      const nextList = languages.some((c) => c.toLowerCase() === code.toLowerCase())
        ? languages
        : [...languages, code];
      setPrefs({ languages: serializeCustomLanguages(nextList) });
      setNote(`Added ${code} — select it, then press Start`);
      setDraft("");
    },
    [languages, selectLanguage, setPrefs],
  );

  const addLanguage = useCallback(async () => {
    const raw = draft.trim();
    if (!raw) return;
    const check = checkLanguageName(raw);
    if (check.kind === "unknown") {
      await alert({
        title: "Unknown language",
        message: raw
          ? `“${raw}” doesn’t look like a real language name. Try something like Spanish, Français, Bulgarian, or ja.`
          : "Enter a language name first.",
      });
      return;
    }
    if (check.kind === "suggest") {
      const ok = await confirm({
        title: "Did you mean…?",
        message: `“${check.input}” looks like a typo for “${check.suggestion}”. Use ${check.suggestion}?`,
        confirmLabel: `Use ${check.suggestion}`,
        cancelLabel: "Cancel",
      });
      if (!ok) return;
      commitLanguage(check.suggestion);
      return;
    }
    commitLanguage(check.name);
  }, [alert, commitLanguage, confirm, draft]);

  const removeLanguage = useCallback(
    (code: string) => {
      const next = languages.filter((c) => c.toLowerCase() !== code.toLowerCase());
      const removingActive = language.toLowerCase() === code.toLowerCase();
      // Atomic: dropping the active language must snap back to English default.
      if (removingActive) {
        setPrefs({
          languages: serializeCustomLanguages(next),
          language: "en",
        });
        setNote("UI language: English");
      } else {
        setPref("languages", serializeCustomLanguages(next));
      }
    },
    [language, languages, setPref, setPrefs],
  );

  const clearLanguageCache = useCallback(async () => {
    const api = getApi();
    if (!api?.plugin_cache_clear) {
      setNote("Cache clear unavailable — rebuild the app.");
      return;
    }
    if (isEnglishLang(language)) {
      setNote("Nothing to clear for English.");
      return;
    }
    setBusy(true);
    try {
      // Chrome catalog + Verse full-file (vf_) + Verse chunks (vc_) for this language.
      await api.plugin_cache_clear(PLUGIN_ID, language);
      const slug = verseLangSlug(language);
      await api.plugin_cache_clear(PLUGIN_ID, `vf_${slug}_*`);
      await api.plugin_cache_clear(PLUGIN_ID, `vc_${slug}_*`);
      setNote(`Cleared chrome + Verse caches for ${language}.`);
      setPref("language", language);
    } finally {
      setBusy(false);
    }
  }, [language, setPref]);

  const clearAllCaches = useCallback(async () => {
    const api = getApi();
    if (!api?.plugin_cache_clear) {
      setNote("Cache clear unavailable — rebuild the app.");
      return;
    }
    const ok = await confirm({
      title: "Clear all translation caches?",
      message:
        "Deletes every language’s chrome catalog and Verse visual translations. Prefs (active language, model) stay. Next language pick re-translates from scratch.",
      confirmLabel: "Clear all",
      cancelLabel: "Cancel",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.plugin_cache_clear(PLUGIN_ID, "");
      setNote("Cleared all translation caches.");
      if (!isEnglishLang(language)) setPref("language", language);
    } finally {
      setBusy(false);
    }
  }, [confirm, language, setPref]);

  return (
    // Do NOT put data-no-translate on this root — it blocked the whole Languages
    // settings page from ever translating. Keep only literal ids / paths opted out.
    <div className="general-tab-shell">
      <div className="llms-tab-intro">
        <h2 className="general-tab-page-title">
          <span>Languages</span>
          <PluginWalkthroughReplayButton pluginId="translation" label="Languages" />
        </h2>
        <p className="general-tab-section-desc">
          {
            "Add a language, pick an API model (Anthropic / OpenAI / Gemini / Ollama — not Claude Code, Cursor, or Codex), then press Start. Opening a gateway in the list is not enough — click a model under it. Auto-translate checkboxes are only for Verse files and chats, not this UI run."
          }
        </p>
      </div>

      <section className="general-tab-section" data-no-translate>
        <GeneralSectionHeader
          icon={<Icons.Globe />}
          title="Live progress"
          description="UI translate runs in the background. Minimize or hide the floating panel anytime — the bar and phrase list always stay here."
        />
        <div id="uefn-translation-progress-host" className="translation-progress-host" />
      </section>

      <section ref={modelTargetRef} className="general-tab-section">
        <GeneralSectionHeader
          icon={<Icons.Brain />}
          title="Translation model"
          description="Pick a model under Anthropic, OpenAI, Gemini, or Ollama. Empty / Default Model / Claude Code / Cursor / Codex cannot batch-translate UI."
        />
        <DuckyModelPicker
          model={model}
          onChange={(next) => setPref("model", next)}
          label="Model"
          placeholder="Default model (Settings → LLMs)"
          hint="Click a model under the gateway — highlighting Anthropic without picking a model still leaves Claude Code / Default selected."
          allowClear
          requireTools={false}
          menuPlacement="bottom"
        />
        <label
          className="translation-opt-toggle"
          style={{ display: "flex", gap: 10, alignItems: "flex-start", marginTop: 14 }}
        >
          <input
            type="checkbox"
            checked={prefs.translatePlans === true}
            onChange={(e) => setPref("translatePlans", e.target.checked)}
          />
          <span>
            <strong style={{ display: "block", fontWeight: 600 }}>Translate Plans</strong>
            <span className="general-tab-section-note" style={{ margin: 0 }}>
              {
                "When on, open Plan tabs show a translated view of the plan (title, overview, body). Off by default — plans stay English until you enable this."
              }
            </span>
          </span>
        </label>
        <label
          className="translation-opt-toggle"
          style={{ display: "flex", gap: 10, alignItems: "flex-start", marginTop: 14 }}
        >
          <input
            type="checkbox"
            checked={prefs.autoTranslateAllFiles === true}
            onChange={(e) => setPref("autoTranslateAllFiles", e.target.checked)}
          />
          <span>
            <strong style={{ display: "block", fontWeight: 600 }}>Auto-translate all Verse files</strong>
            <span className="general-tab-section-note" style={{ margin: 0 }}>
              {
                "When on, opening any .verse file also opens a visual translation. Turn Auto off on a file’s hover card to exclude just that file."
              }
            </span>
          </span>
        </label>
        <label
          className="translation-opt-toggle"
          style={{ display: "flex", gap: 10, alignItems: "flex-start", marginTop: 14 }}
        >
          <input
            type="checkbox"
            checked={prefs.autoTranslateAllChats === true}
            onChange={(e) => setPref("autoTranslateAllChats", e.target.checked)}
          />
          <span>
            <strong style={{ display: "block", fontWeight: 600 }}>Auto-translate all chats</strong>
            <span className="general-tab-section-note" style={{ margin: 0 }}>
              {
                "When on, chat messages translate for every ducky. Turn Auto off on a chat’s hover card to exclude just that chat."
              }
            </span>
          </span>
        </label>
      </section>

      <section ref={langListRef} className="general-tab-section">
        <GeneralSectionHeader
          icon={<Icons.Globe />}
          title="Your languages"
          description="Add grows the list only. Click a language to select it, then press Start. English (off) stops translation immediately."
        />

        <ul className="translation-lang-list">
          <li>
            <button
              type="button"
              className={`translation-lang-item${isEnglishLang(language) ? " is-active" : ""}`}
              onClick={() => selectLanguage("en")}
            >
              <span className="translation-lang-item-label">English (off)</span>
              <span className="translation-lang-item-meta">default</span>
            </button>
          </li>
          {languages.map((code) => {
            const active = language.toLowerCase() === code.toLowerCase();
            return (
              <li key={code} className="translation-lang-row">
                <button
                  type="button"
                  className={`translation-lang-item${active ? " is-active" : ""}`}
                  onClick={() => selectLanguage(code)}
                >
                  <span className="translation-lang-item-label" data-no-translate>
                    {code}
                  </span>
                  <span className="translation-lang-item-meta">
                    {active ? "selected" : "click to select"}
                  </span>
                </button>
                <button
                  type="button"
                  className="icon-btn translation-lang-remove"
                  title={`Remove ${code}`}
                  aria-label={`Remove ${code}`}
                  onClick={() => removeLanguage(code)}
                >
                  <Icons.Close />
                </button>
              </li>
            );
          })}
        </ul>

        <div ref={langStartRef} className="translation-lang-actions">
          <button
            type="button"
            className="settings-btn general-tab-btn-primary"
            onClick={startTranslate}
            disabled={isEnglishLang(language)}
          >
            Start
          </button>
          <span className="general-tab-section-note" style={{ margin: 0 }}>
            {isEnglishLang(language)
              ? "Select a language first."
              : `Start ${language} with the model above.`}
          </span>
        </div>

        <div ref={langAddRef} className="translation-lang-add">
          <input
            type="text"
            className="settings-input translation-lang-input"
            placeholder="Add a language (e.g. Spanish, Français, ja)"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void addLanguage();
              }
            }}
            aria-label="Add language"
          />
          <button
            type="button"
            className="settings-btn general-tab-btn-primary"
            onClick={() => void addLanguage()}
            disabled={!draft.trim()}
          >
            Add
          </button>
        </div>

        {note ? <p className="general-tab-section-note">{note}</p> : null}
      </section>

      <section className="general-tab-section">
        <GeneralSectionHeader
          icon={<Icons.Trash />}
          title="Cache"
          description="Chrome catalogs are Language.json; Verse full-file vf_* + chunk vc_* under %LOCALAPPDATA%/UEFN-Ducky/uefn_plugin_cache/translation/. Prefs (language list, model) survive clears."
        />
        <div className="general-tab-btn-row" style={{ marginTop: 4, gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            className="settings-btn"
            disabled={busy || isEnglishLang(language)}
            onClick={() => void clearLanguageCache()}
          >
            {busy ? "Clearing…" : "Clear this language"}
          </button>
          <button
            type="button"
            className="settings-btn"
            disabled={busy}
            onClick={() => void clearAllCaches()}
          >
            Clear all translation caches
          </button>
        </div>
      </section>
    </div>
  );
}
