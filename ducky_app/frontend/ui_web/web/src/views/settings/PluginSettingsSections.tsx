import { useCallback, useEffect, useState } from "react";

import { ChoiceDropdown } from "../../components/ChoiceDropdown";
import { DuckyModelPicker } from "../../components/ducky/DuckyModelPicker";
import { getApi } from "../../hooks/usePanelApi";
import { Icons } from "../../icons/Icons";
import {
  pluginSettingsSectionsForTab,
  usePluginContributions,
  type PluginSettingsProperty,
  type PluginSettingsSection,
} from "../../hooks/usePluginContributions";
import { usePluginUiPrefs } from "../../hooks/usePluginUiPrefs";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { PluginWalkthroughReplayButton } from "./PluginWalkthroughReplayButton";
import { SettingsToggleRow } from "./SettingsToggleRow";

/** A property with no explicit type is a boolean toggle (back-compat). */
function isBoolProp(p: PluginSettingsProperty): boolean {
  return (p.type || "boolean") === "boolean";
}

function isSecretProp(p: PluginSettingsProperty): boolean {
  const t = (p.type || "").toLowerCase();
  return t === "secret" || t === "password";
}

function isPrefFieldProp(p: PluginSettingsProperty): boolean {
  const t = (p.type || "").toLowerCase();
  return t === "select" || t === "string" || t === "text";
}

function isActionProp(p: PluginSettingsProperty): boolean {
  return (p.type || "").toLowerCase() === "action";
}

function isModelProp(p: PluginSettingsProperty): boolean {
  return (p.type || "").toLowerCase() === "model";
}

/**
 * The chat composer's own model picker (every gateway, search, effort). Saves the qualified
 * `backend:model` id to the plugin's prefs; empty (the × button) is the plugin's own choice,
 * named by `placeholder` (e.g. "Auto").
 */
function ModelField({
  pluginId,
  prop,
  compact,
}: {
  pluginId: string;
  prop: PluginSettingsProperty;
  compact?: boolean;
}) {
  const { prefs, setPref } = usePluginUiPrefs(pluginId);
  const cur = prefs[prop.id];
  const value = typeof cur === "string" ? cur : typeof prop.default === "string" ? prop.default : "";
  // `logins`: the chat's whole dropdown (Claude Code, Codex… and the Faster / Smarter slider, kept
  // in `<id>_effort`), for a plugin that hands the pick to a chat. Otherwise API models only.
  const effortKey = `${prop.id}_effort`;
  const effort = typeof prefs[effortKey] === "string" ? (prefs[effortKey] as string) : "off";
  const loginProps = prop.logins
    ? { thinkingEffort: effort, onEffortChange: (next: string) => setPref(effortKey, next) }
    : { apiOnly: true };
  return (
    <div className="voice-settings-row" data-plugin-model-field={prop.id}>
      <label className="voice-settings-label">{prop.label || prop.id}</label>
      {!compact && prop.description ? <p className="general-tab-section-desc">{prop.description}</p> : null}
      <DuckyModelPicker
        model={value}
        onChange={(next) => setPref(prop.id, next)}
        label=""
        hint=""
        placeholder={prop.placeholder || "Default model"}
        menuPlacement="bottom"
        {...loginProps}
        hideProviders={prop.hide_providers}
        labeled
      />
    </div>
  );
}

/**
 * Encrypted secret (e.g. an API key). The value is never read back — we only learn
 * whether one is set. Writes go through set_uefn_plugin_secret, which enforces that
 * the key is declared in the plugin manifest's secret_keys.
 */
function SecretField({
  pluginId,
  prop,
  compact,
}: {
  pluginId: string;
  prop: PluginSettingsProperty;
  compact?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testStatus, setTestStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const inputId = `plugin-${pluginId}-${prop.id}`;
  const testable = Boolean(prop.testable);

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api?.get_uefn_plugin_secret_status) return;
    try {
      const res = await api.get_uefn_plugin_secret_status(pluginId, [prop.id]);
      setSaved(Boolean(res?.ok && res.status && res.status[prop.id]));
    } catch {
      /* ignore offline / old builds */
    }
  }, [pluginId, prop.id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = useCallback(
    async (value: string) => {
      const api = getApi();
      if (!api?.set_uefn_plugin_secret) return;
      setBusy(true);
      try {
        const res = await api.set_uefn_plugin_secret(pluginId, prop.id, value);
        if (res?.ok) {
          setSaved(Boolean(res.set));
          setDraft("");
          setTestStatus(null);
        }
      } finally {
        setBusy(false);
      }
    },
    [pluginId, prop.id],
  );

  const test = useCallback(async () => {
    const api = getApi();
    if (!api?.test_uefn_plugin_secret) {
      setTestStatus({ ok: false, text: "Update the app to test API keys here" });
      return;
    }
    setTesting(true);
    setTestStatus({ ok: true, text: "Testing…" });
    try {
      const res = await api.test_uefn_plugin_secret(pluginId, prop.id, draft.trim());
      setTestStatus({
        ok: Boolean(res?.ok),
        text: String(res?.detail || (res?.ok ? "OK" : "Test failed")),
      });
    } catch (e) {
      setTestStatus({
        ok: false,
        text: e instanceof Error ? e.message : "Test failed",
      });
    } finally {
      setTesting(false);
    }
  }, [pluginId, prop.id, draft]);

  return (
    <div className="voice-settings-row">
      <label className="voice-settings-label" htmlFor={inputId}>
        {prop.label || prop.id}
      </label>
      {!compact && prop.description ? (
        <p className="general-tab-section-desc">{prop.description}</p>
      ) : null}
      <div className="plugin-secret-field">
        <input
          id={inputId}
          className="settings-input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={draft}
          placeholder={saved ? "••••••••••••••••" : prop.placeholder || "Paste key"}
          onChange={(e) => {
            setDraft(e.target.value);
            setTestStatus(null);
          }}
        />
        <button
          type="button"
          className={`settings-btn llms-provider-btn${saved && !draft.trim() ? " is-saved" : ""}`}
          // Actionable only with a typed draft, so the green "Saved" state is a
          // passive indicator — removing a key is the dedicated Clear button's job.
          disabled={busy || testing || !draft.trim()}
          onClick={() => void save(draft.trim())}
        >
          {busy ? (
            "Saving…"
          ) : saved && !draft.trim() ? (
            <>
              <Icons.Check />
              Saved
            </>
          ) : (
            "Save"
          )}
        </button>
        {testable ? (
          <button
            type="button"
            className={`settings-btn llms-provider-btn${
              testStatus?.ok && testStatus.text !== "Testing…" ? " is-saved" : ""
            }`}
            disabled={busy || testing || (!draft.trim() && !saved)}
            title="Call the vendor API to verify this key"
            onClick={() => void test()}
          >
            {testing ? "Testing…" : testStatus?.ok && testStatus.text !== "Testing…" ? "OK" : "Test"}
          </button>
        ) : null}
        {saved ? (
          <button
            type="button"
            className="settings-btn llms-provider-btn"
            disabled={busy || testing}
            title="Remove the saved key"
            onClick={() => void save("")}
          >
            Clear
          </button>
        ) : null}
      </div>
      {testable && testStatus && testStatus.text !== "Testing…" ? (
        <p
          className={`plugin-secret-test-detail${testStatus.ok ? " is-ok" : " is-error"}`}
          role="status"
        >
          {testStatus.text}
        </p>
      ) : null}
    </div>
  );
}

type ActionResult = { ok?: boolean; connected?: boolean; detail?: string; error?: string };
type ActionStatus = { tone: "" | "is-ok" | "is-error"; text: string };

function actionStatus(res: ActionResult): ActionStatus | null {
  const text = String(res.detail || res.error || "");
  if (!text) return null;
  return { tone: res.ok === false ? "is-error" : res.connected ? "is-ok" : "", text };
}

/**
 * Button backed by plugin panel RPCs (e.g. "Sign in with Notion"). The status line
 * comes from the RPC's own `{ ok, connected, detail }` result; once connected the
 * button swaps to `connected_label` / `connected_rpc`.
 */
function ActionField({
  pluginId,
  prop,
  compact,
}: {
  pluginId: string;
  prop: PluginSettingsProperty;
  compact?: boolean;
}) {
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState<ActionStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const call = useCallback(
    async (method: string) => {
      const api = getApi();
      if (!api?.plugin_call) {
        setStatus({ tone: "is-error", text: "Update the app to use this button" });
        return;
      }
      try {
        const res = (await api.plugin_call(pluginId, method, {})) as ActionResult;
        setConnected(Boolean(res.connected));
        setStatus(actionStatus(res));
      } catch (e) {
        setStatus({ tone: "is-error", text: e instanceof Error ? e.message : "Failed" });
      }
    },
    [pluginId],
  );

  useEffect(() => {
    if (prop.status_rpc) void call(prop.status_rpc);
  }, [call, prop.status_rpc]);

  const swap = connected && Boolean(prop.connected_rpc);
  const method = swap ? prop.connected_rpc : prop.rpc;
  const label = swap ? prop.connected_label || "Disconnect" : prop.label || prop.id;

  return (
    <div className="voice-settings-row">
      {!compact && prop.description ? (
        <p className="general-tab-section-desc">{prop.description}</p>
      ) : null}
      <div className="plugin-secret-field">
        <button
          type="button"
          className={`settings-btn llms-provider-btn${connected ? " is-saved" : ""}`}
          disabled={busy || !method}
          onClick={async () => {
            if (!method) return;
            setBusy(true);
            try {
              await call(method);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Waiting…" : label}
        </button>
      </div>
      {status ? (
        <p className={`plugin-secret-test-detail${status.tone ? ` ${status.tone}` : ""}`} role="status">
          {status.text}
        </p>
      ) : null}
    </div>
  );
}

/** Non-secret string / select property, persisted to plugin UI prefs. */
function PrefField({
  pluginId,
  prop,
  compact,
}: {
  pluginId: string;
  prop: PluginSettingsProperty;
  compact?: boolean;
}) {
  const { prefs, setPref } = usePluginUiPrefs(pluginId);
  const inputId = `plugin-${pluginId}-${prop.id}`;
  const cur = prefs[prop.id];
  const fallback = typeof prop.default === "string" ? prop.default : "";
  const value = typeof cur === "string" ? cur : fallback;
  const desc =
    !compact && prop.description ? (
      <p className="general-tab-section-desc">{prop.description}</p>
    ) : null;

  if ((prop.type || "").toLowerCase() === "select") {
    const options = (prop.options || [])
      .filter((o) => o && typeof o.value === "string")
      .map((o) => ({ value: o.value, label: o.label || o.value }));
    return (
      <div className="voice-settings-row">
        <label className="voice-settings-label">{prop.label || prop.id}</label>
        {desc}
        <ChoiceDropdown
          aria-label={prop.label || prop.id}
          mode="radio"
          value={value}
          options={options}
          onChange={(next) => setPref(prop.id, next)}
        />
      </div>
    );
  }

  return (
    <div className="voice-settings-row">
      <label className="voice-settings-label" htmlFor={inputId}>
        {prop.label || prop.id}
      </label>
      {desc}
      <input
        id={inputId}
        className="settings-input"
        type="text"
        value={value}
        placeholder={prop.placeholder || ""}
        onChange={(e) => setPref(prop.id, e.target.value)}
      />
    </div>
  );
}

const ACCORDION_CHEVRON = (
  <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" fill="none" strokeWidth="2">
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

function sectionHasFields(section: PluginSettingsSection): boolean {
  const pluginId = (section.plugin_id || "").trim().toLowerCase();
  if (!pluginId) return false;
  return (
    section.properties.some(isBoolProp) ||
    section.properties.some(isActionProp) ||
    section.properties.some(isSecretProp) ||
    section.properties.some(isPrefFieldProp) ||
    section.properties.some(isModelProp)
  );
}

function SectionFields({
  section,
  compact,
}: {
  section: PluginSettingsSection;
  compact?: boolean;
}) {
  const pluginId = (section.plugin_id || "").trim().toLowerCase();
  const { getBool, setPref } = usePluginUiPrefs(pluginId || "_");
  const actionProps = section.properties.filter(isActionProp);
  const boolProps = section.properties.filter(isBoolProp);
  const secretProps = section.properties.filter(isSecretProp);
  const prefFieldProps = section.properties.filter(isPrefFieldProp);
  const modelProps = section.properties.filter(isModelProp);

  return (
    <>
      {actionProps.map((prop) => (
        <ActionField key={prop.id} pluginId={pluginId} prop={prop} compact={compact} />
      ))}
      {boolProps.length ? (
        <div className="general-tab-toggle-card">
          {boolProps.map((prop) => {
            const defaultOn = prop.default !== false;
            const checked = getBool(prop.id, defaultOn);
            return (
              <SettingsToggleRow
                key={prop.id}
                id={`plugin-${pluginId}-${section.id}-${prop.id}`}
                label={prop.label || prop.id}
                description={compact ? undefined : prop.description || ""}
                checked={checked}
                onChange={(next) => setPref(prop.id, next)}
              />
            );
          })}
        </div>
      ) : null}
      {secretProps.map((prop) => (
        <SecretField key={prop.id} pluginId={pluginId} prop={prop} compact={compact} />
      ))}
      {prefFieldProps.map((prop) => (
        <PrefField key={prop.id} pluginId={pluginId} prop={prop} compact={compact} />
      ))}
      {modelProps.map((prop) => (
        <ModelField key={prop.id} pluginId={pluginId} prop={prop} compact={compact} />
      ))}
    </>
  );
}

function SectionBlock({
  section,
  compact,
  showWalkthroughReplay,
}: {
  section: PluginSettingsSection;
  compact?: boolean;
  /** First section for this plugin tab — replay sits beside the title. */
  showWalkthroughReplay?: boolean;
}) {
  if (!sectionHasFields(section)) return null;
  const pluginId = (section.plugin_id || "").trim().toLowerCase();

  return (
    <section className="general-tab-section" key={`${pluginId}:${section.id}`}>
      <GeneralSectionHeader
        icon={<Icons.PanelLeft />}
        title={section.title || section.id}
        description={compact ? undefined : section.description}
        trailing={
          showWalkthroughReplay && pluginId ? (
            <PluginWalkthroughReplayButton pluginId={pluginId} label={section.title || pluginId} />
          ) : null
        }
      />
      <SectionFields section={section} compact={compact} />
    </section>
  );
}

function AccordionSection({
  section,
  compact,
}: {
  section: PluginSettingsSection;
  compact?: boolean;
}) {
  if (!sectionHasFields(section)) return null;
  const pluginId = (section.plugin_id || "").trim().toLowerCase();
  const title = section.title || section.id;

  return (
    <details className="appearance-details" key={`${pluginId}:${section.id}`}>
      <summary>
        <div>
          <div className="appearance-tab-details-title">{title}</div>
          {!compact && section.description ? (
            <div className="appearance-tab-details-subtitle">{section.description}</div>
          ) : null}
        </div>
        <div className="appearance-details-summary-actions">
          <div className="appearance-tab-details-chevron">{ACCORDION_CHEVRON}</div>
        </div>
      </summary>
      <div className="appearance-details-content">
        <SectionFields section={section} compact={compact} />
      </div>
    </details>
  );
}

/** Renders declarative `settings.sections` for a Settings tab id and/or plugin id. */
export function PluginSettingsSections({
  tabId,
  pluginId,
  compact,
  accordion,
}: {
  tabId?: string;
  /** Prefer this when the host embeds a builtin form — tab ids can rename in Store plugins. */
  pluginId?: string;
  /** Titles only — hide section/property blurbs (LLMs provider detail). */
  compact?: boolean;
  /** Collapse each section into an Appearance-style `<details>` accordion. */
  accordion?: boolean;
}) {
  const contrib = usePluginContributions();
  const byTab = tabId ? pluginSettingsSectionsForTab(contrib, tabId) : [];
  const pid = (pluginId || "").trim().toLowerCase();
  const sections = pid
    ? contrib.settings_sections
        .filter((s) => (s.plugin_id || "").trim().toLowerCase() === pid)
        .sort((a, b) => {
          const ao = typeof a.order === "number" ? a.order : 100;
          const bo = typeof b.order === "number" ? b.order : 100;
          if (ao !== bo) return ao - bo;
          return String(a.id).localeCompare(String(b.id));
        })
    : byTab;
  if (!sections.length) return null;
  const firstWithFields = sections.findIndex((s) => sectionHasFields(s));
  return (
    <>
      {sections.map((section, i) =>
        accordion || section.collapsed ? (
          <AccordionSection
            key={`${section.plugin_id}:${section.id}`}
            section={section}
            compact={compact}
          />
        ) : (
          <SectionBlock
            key={`${section.plugin_id}:${section.id}`}
            section={section}
            compact={compact}
            showWalkthroughReplay={!compact && i === firstWithFields}
          />
        ),
      )}
    </>
  );
}
