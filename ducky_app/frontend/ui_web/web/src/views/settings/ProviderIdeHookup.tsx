import { useCallback, useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { Icons } from "../../icons/Icons";
import { TruncatedText } from "../../components/TruncatedText";
import { usePluginContributions } from "../../hooks/usePluginContributions";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { targetRef } from "../../ui-targets/registry";

type IdeStatus = { text: string; ok: boolean };

/** When register() never ran, plugin.json ide.hookups may be missing — still show Apply. */
const FALLBACK_BY_PLUGIN: Record<string, { kind: string; label: string }> = {
  cursor: { kind: "cursor", label: "Cursor" },
  anthropic: { kind: "claude", label: "Claude" },
  google: { kind: "antigravity", label: "Antigravity" },
};

function LaptopCodeIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <path d="M8 21h8" />
      <path d="M12 17v4" />
      <path d="M7 8l2 2 3-3 3 3 2-2" />
    </svg>
  );
}

/** IDE MCP + skills Apply for one Store gateway (inside that provider’s LLMs detail). */
export function ProviderIdeHookup({ pluginId }: { pluginId: string }) {
  const contrib = usePluginContributions();
  const pid = pluginId.trim().toLowerCase();
  const hookup =
    contrib.ide_hookups.find((h) => (h.plugin_id || "").trim().toLowerCase() === pid) ||
    (FALLBACK_BY_PLUGIN[pid]
      ? { plugin_id: pid, ...FALLBACK_BY_PLUGIN[pid] }
      : null);

  const [status, setStatus] = useState<IdeStatus | null>(null);
  const [applying, setApplying] = useState(false);
  const [testing, setTesting] = useState(false);
  const [verifying, setVerifying] = useState(true);

  const kind = hookup?.kind || "";
  const label = hookup?.label || kind;

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api?.get_ide_statuses || !kind) return;
    const rows = await api.get_ide_statuses();
    const row = rows[kind];
    if (row) setStatus({ text: String(row.detail || ""), ok: !!row.ok });
  }, [kind]);

  useEffect(() => {
    if (!kind) {
      setVerifying(false);
      return;
    }
    return onApiReady((api) => {
      if (!api.get_ide_statuses) {
        setVerifying(false);
        return;
      }
      setVerifying(true);
      void api
        .get_ide_statuses()
        .then((rows) => {
          const row = rows[kind];
          if (row) setStatus({ text: String(row.detail || ""), ok: !!row.ok });
        })
        .finally(() => setVerifying(false));
    });
  }, [kind]);

  if (!hookup || !kind) return null;

  const showTestOk = !!status?.ok && status.text === "OK" && !testing;

  return (
    <section
      className="general-tab-section"
      ref={targetRef("settings.llms.provider.ide", {
        kind: "settings_field",
        label: "IDE / MCP",
        route: "settings.llms",
      })}
    >
      <GeneralSectionHeader
        icon={<LaptopCodeIcon />}
        title="IDE / MCP"
        description={`${label} — apply UEFN MCP + Ducky skills into this IDE (global, every project).`}
      />
      <div className="llms-provider-card">
        <div className="llms-provider-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
          <div className="llms-provider-row-main" style={{ width: "100%" }}>
            <div className={`llms-provider-label${status?.ok ? " is-saved" : ""}`}>
              {status?.ok ? <Icons.Check /> : <span className="llms-provider-dot" title="Not applied" />}
              <span>{label}</span>
            </div>
            <div style={{ flex: 1 }} />
            <button
              ref={targetRef("settings.llms.provider.ide.apply", {
                kind: "button",
                label: "Apply",
                route: "settings.llms",
              })}
              type="button"
              className="settings-btn llms-provider-btn"
              disabled={applying || testing || verifying}
              onClick={async () => {
                const api = getApi();
                if (!api?.apply_ide) return;
                setApplying(true);
                try {
                  await api.apply_ide(kind);
                  await refresh();
                } catch (e) {
                  setStatus({
                    text: e instanceof Error ? e.message : "Apply failed",
                    ok: false,
                  });
                } finally {
                  setApplying(false);
                }
              }}
            >
              {applying ? "Applying…" : verifying ? "Checking…" : status?.ok ? "Re-apply" : "Apply"}
            </button>
            <button
              type="button"
              className={`settings-btn llms-provider-btn${showTestOk ? " is-saved" : ""}`}
              disabled={testing || applying || verifying}
              onClick={async () => {
                const api = getApi();
                if (!api?.test_ide) return;
                setTesting(true);
                try {
                  const res = await api.test_ide(kind);
                  setStatus({ text: res.ok ? "OK" : res.detail, ok: !!res.ok });
                } catch (e) {
                  setStatus({
                    text: e instanceof Error ? e.message : "Test failed",
                    ok: false,
                  });
                } finally {
                  setTesting(false);
                }
              }}
            >
              {testing ? "Testing…" : showTestOk ? "OK" : "Test"}
            </button>
          </div>
          {status && !status.ok ? (
            <TruncatedText title={status.text} className="llms-provider-status-text is-fail">
              {status.text}
            </TruncatedText>
          ) : null}
          {status?.ok && status.text !== "OK" ? (
            <TruncatedText title={status.text} className="llms-provider-status-text is-ok">
              {status.text}
            </TruncatedText>
          ) : null}
          <p className="general-tab-section-desc" style={{ margin: 0 }}>
            Apply also installs Ducky skills into this IDE globally.
          </p>
        </div>
      </div>
    </section>
  );
}
