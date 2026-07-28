import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { usePluginUiPrefs } from "../hooks/usePluginUiPrefs";
import { checkLanguageName } from "../views/settings/languageNames";
import {
  isEnglishLang,
  normalizeLanguageCode,
  parseCustomLanguages,
  serializeCustomLanguages,
} from "../views/settings/translationLanguages";

// ponytail: Discord-style pick + add in the header; model/cache stay in Settings → Languages.
const PLUGIN_ID = "translation";
const MENU_WIDTH = 260;
const MENU_GAP = 6;

function computeMenuPosition(trigger: HTMLElement): { top: number; left: number } {
  const rect = trigger.getBoundingClientRect();
  let left = rect.right - MENU_WIDTH;
  if (left < 8) left = 8;
  if (left + MENU_WIDTH > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - MENU_WIDTH - 8);
  }
  return { top: rect.bottom + MENU_GAP, left };
}

type LanguageHeaderDropdownProps = {
  icon: ReactNode;
  title: string;
};

export function LanguageHeaderDropdown({ icon, title }: LanguageHeaderDropdownProps) {
  const { prefs, setPref, setPrefs } = usePluginUiPrefs(PLUGIN_ID);
  const { confirm, alert } = useConfirmModal();
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const language = typeof prefs.language === "string" ? prefs.language.trim() : "en";
  const languages = useMemo(() => parseCustomLanguages(prefs.languages), [prefs.languages]);
  const translated = !isEnglishLang(language);

  const selectLanguage = useCallback(
    (code: string) => {
      const next = code.trim() || "en";
      setPref("language", next);
      setOpen(false);
      setAdding(false);
      setDraft("");
    },
    [setPref],
  );

  const commitLanguage = useCallback(
    (name: string) => {
      const code = normalizeLanguageCode(name);
      if (!code) return;
      if (isEnglishLang(code)) {
        selectLanguage("en");
        return;
      }
      const nextList = languages.some((c) => c.toLowerCase() === code.toLowerCase())
        ? languages
        : [...languages, code];
      const applyNow = isEnglishLang(language) || language.toLowerCase() === code.toLowerCase();
      if (applyNow) {
        setPrefs({
          languages: serializeCustomLanguages(nextList),
          language: code,
        });
        setOpen(false);
      } else {
        setPrefs({ languages: serializeCustomLanguages(nextList) });
      }
      setDraft("");
      setAdding(false);
    },
    [language, languages, selectLanguage, setPrefs],
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

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) {
      setMenuPos((pos) => (pos === null ? pos : null));
      return;
    }
    const update = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      setMenuPos(computeMenuPosition(trigger));
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, adding]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setOpen(false);
      setAdding(false);
      setDraft("");
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (adding) {
          setAdding(false);
          setDraft("");
          return;
        }
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [adding, open]);

  useEffect(() => {
    if (!open || !adding) return;
    inputRef.current?.focus();
  }, [adding, open]);

  const menu =
    open && menuPos ? (
      <div
        ref={menuRef}
        className="terminal-header-menu terminal-header-menu--portaled no-drag"
        style={{ top: menuPos.top, left: menuPos.left, width: MENU_WIDTH }}
        data-no-translate
      >
        <div className="terminal-header-list">
          <div className={`terminal-header-item${isEnglishLang(language) ? " is-active" : ""}`}>
            <button
              type="button"
              className="terminal-header-item-main"
              onClick={() => selectLanguage("en")}
            >
              <span className="terminal-header-item-name">English (off)</span>
              <span className="terminal-header-item-meta">
                <span className="terminal-header-item-status">default</span>
              </span>
            </button>
          </div>
          {languages.map((code) => {
            const active = language.toLowerCase() === code.toLowerCase();
            return (
              <div key={code} className={`terminal-header-item${active ? " is-active" : ""}`}>
                <button
                  type="button"
                  className="terminal-header-item-main"
                  onClick={() => selectLanguage(code)}
                >
                  <span className="terminal-header-item-name">{code}</span>
                  <span className="terminal-header-item-meta">
                    <span className="terminal-header-item-status">
                      {active ? "active" : "click to apply"}
                    </span>
                  </span>
                </button>
              </div>
            );
          })}
        </div>
        {adding ? (
          <div className="language-header-add-row">
            <input
              ref={inputRef}
              type="text"
              className="settings-input language-header-add-input"
              placeholder="Spanish, Français, ja…"
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
              className="settings-btn general-tab-btn-primary language-header-add-commit"
              onClick={() => void addLanguage()}
              disabled={!draft.trim()}
            >
              Add
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="terminal-header-new-btn"
            onClick={() => setAdding(true)}
          >
            <span>Add language</span>
          </button>
        )}
      </div>
    ) : null;

  return (
    <div className="terminal-header-root">
      <button
        ref={triggerRef}
        type="button"
        className={`icon-btn no-drag plugin-header-btn terminal-header-trigger${open || translated ? " is-active" : ""}`}
        title={title}
        aria-label={title}
        aria-pressed={translated || undefined}
        onClick={() => {
          setOpen((v) => !v);
          setAdding(false);
          setDraft("");
        }}
      >
        {icon}
      </button>
      {menu ? createPortal(menu, document.body) : null}
    </div>
  );
}
