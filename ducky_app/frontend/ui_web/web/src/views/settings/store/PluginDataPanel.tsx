import { useCallback, useEffect, useState } from "react";
import { getApi } from "../../../hooks/usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "../../../hooks/usePanelPushBus";
import { Icons } from "../../../icons/Icons";
import { FolderShareDialog } from "../../../plugin-ui/FolderShareDialog";
import { PluginScopeDrawer } from "../../../plugin-ui/PluginScopeDrawer";
import {
  bindingText,
  folderRefusal,
  formatBytes,
  hasFolders,
  kindTitle,
  sharingText,
  syncText,
} from "../../../plugin-ui/scopeBarText";
import type {
  PluginDataTotals,
  PluginFolderShareUnit,
  PluginScopeChoice,
  PluginScopeFirstShare,
  PluginScopeStatus,
} from "../../../types/panel";

/** "12 docs · 3 files · 4.2 MB", or that nothing is stored. */
export function totalsText(t: PluginDataTotals | undefined): string {
  const docs = t?.docs ?? 0;
  const files = t?.files ?? 0;
  if (!docs && !files) return "Nothing stored";
  const bytes = (t?.docsBytes ?? 0) + (t?.filesBytes ?? 0);
  return `${docs} doc${docs === 1 ? "" : "s"} · ${files} file${files === 1 ? "" : "s"} · ${formatBytes(bytes)}`;
}

function readOnlyText(status: PluginScopeStatus, kind: string): string {
  if (status.state === "locked") return "Waiting for your account's data key. Read-only until you're back online.";
  if (status.state === "waiting") return `Waiting for ${kind} data. Read-only until it arrives.`;
  if (status.scope?.readOnly) return status.pausedText || `${status.scope.label} is paused. Read-only.`;
  return "";
}

type Confirm = { kind: "switch"; choice: PluginScopeChoice } | { kind: "copy" };

/** A switch into a team with none of the plugin's folder files yet: which Local units go in. */
type FirstShare = { choice: PluginScopeChoice; team: string; units: PluginFolderShareUnit[]; note: string };

/**
 * Plugins page → a plugin → Data: where its data lives (Local or one shared space, picked
 * per plugin), how much there is, and a one-way copy of the Local data into that space.
 * A shared copy also shows the scope bar's drawer (people, locks, changes, history, mode).
 * Every space keeps its own copy; nothing moves between spaces. Renders nothing while the
 * account has no shared spaces to offer.
 */
export function PluginDataPanel({ pluginId, name }: { pluginId: string; name: string }) {
  const [status, setStatus] = useState<PluginScopeStatus | null>(null);
  const [choices, setChoices] = useState<PluginScopeChoice[] | null>(null);
  const [emptyNote, setEmptyNote] = useState("");
  const [picking, setPicking] = useState(false);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [share, setShare] = useState<FirstShare | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await getApi()?.plugin_scope_status?.(pluginId);
      if (next) setStatus(next);
    } catch {
      /* keep what is shown */
    }
  }, [pluginId]);

  useEffect(() => {
    void refresh();
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type === "plugin_scope_changed" && (!event.plugins?.length || event.plugins.includes(pluginId))) void refresh();
      else if (event.type === "duckyos_account_changed") void refresh();
    });
  }, [refresh, pluginId]);

  if (!status?.visible || !status.scope) return null;
  const scope = status.scope;
  const shared = scope.kind === "shared";
  const refusal = folderRefusal(status);
  if (!shared && status.shareable === false) {
    // Nothing of this plugin can go to a space. When its folders can't be shared from
    // this PC, say why; otherwise there is nothing to pick: no Data section.
    if (!refusal) return null;
    return (
      <div className="ds-panel ds-data">
        <h3 className="ds-panel-title">Data</h3>
        <p className="ds-data-where">
          <span className="ds-data-pill ds-data-pill--local"><Icons.Monitor />Local</span>
          <span className="ds-data-help">{refusal}</span>
        </p>
      </div>
    );
  }
  const kind = (status.kindLabel || "space").toLowerCase();
  const where = shared ? `${kindTitle(kind)} · ${scope.label}` : "Local";
  const locked = readOnlyText(status, kind);
  const localCount = (status.local?.docs ?? 0) + (status.local?.files ?? 0);

  const openPicker = async () => {
    setPicking((open) => !open);
    setConfirm(null);
    if (choices) return;
    const res = await getApi()?.plugin_scope_choices?.();
    setChoices(res?.choices ?? []);
    setEmptyNote(res?.emptyNote ?? "");
  };

  const run = async (work: () => Promise<string>) => {
    setBusy(true);
    try {
      setMessage(await work());
    } finally {
      setBusy(false);
      setConfirm(null);
      await refresh();
    }
  };

  /** A team with none of the plugin's folder files yet asks which Local units go in first
   * (`needs: "first_share"`); the answer comes back here with `firstShare`. */
  const switchTo = (choice: PluginScopeChoice, firstShare?: PluginScopeFirstShare) =>
    run(async () => {
      const set = getApi()?.plugin_scope_set;
      // A plugin that keeps its own folders gets the team's files before it switches.
      if (choice.kind === "shared" && hasFolders(status)) {
        setMessage(firstShare?.units?.length ? sharingText(choice.label) : bindingText(choice.label));
      }
      const res = firstShare ? await set?.(pluginId, choice.id, firstShare) : await set?.(pluginId, choice.id);
      setPicking(false);
      if (res?.needs === "first_share") {
        setShare({ choice, team: res.team || choice.label, units: res.units ?? [], note: res.note || "" });
        return "";
      }
      if (res?.ok === false) return res.error || "Could not switch.";
      return choice.kind === "shared" ? `${name} now shows ${choice.label}'s copy.` : `${name} now shows your Local copy.`;
    });
  const answerShare = (firstShare: PluginScopeFirstShare) => {
    if (!share) return;
    setShare(null);
    void switchTo(share.choice, firstShare);
  };

  const copyIn = () =>
    run(async () => {
      const res = await getApi()?.plugin_data_copy_to_space?.(pluginId, scope.spaceId);
      if (!res || res.ok === false) return res?.error || "Could not copy.";
      const kept = res.kept ? ` ${res.kept} ${res.kept === 1 ? "item" : "items"} ${scope.label} already had stayed as they were.` : "";
      const big = res.tooBig?.length ? ` ${res.tooBig.length} too big to share stayed Local.` : "";
      return `Copied ${res.copied ?? 0} ${res.copied === 1 ? "item" : "items"} into ${scope.label}.${kept}${big}`;
    });

  return (
    <div className="ds-panel ds-data">
      <h3 className="ds-panel-title">Data</h3>
      <p className="ds-data-where">
        <span className={`ds-data-pill ds-data-pill--${shared ? "shared" : "local"}`}>
          {shared ? <Icons.Users /> : <Icons.Monitor />}
          {where}
        </span>
        <span className="ds-data-help">
          {shared ? "Every member sees and changes the same copy." : "Only on this PC, for your account."}
        </span>
      </p>
      <dl className="ds-data-totals">
        {shared ? (
          <>
            <dt>{scope.label}</dt>
            <dd>
              {totalsText(status.current)}
              <span className="ds-data-sync">{syncText(status, Date.now())}</span>
            </dd>
          </>
        ) : null}
        <dt>Local</dt>
        <dd>{totalsText(status.local)}{shared ? <span className="ds-data-sync">Separate copy on this PC</span> : null}</dd>
      </dl>
      {locked ? <p className="ds-data-note" role="note"><Icons.Lock /> {locked}</p> : null}

      <div className="ds-data-actions">
        {status.canChange ? (
          <button type="button" className="ds-btn ds-data-btn" aria-expanded={picking} onClick={() => void openPicker()} disabled={busy}>
            Keep data in…
          </button>
        ) : null}
        {shared && !locked ? (
          <button type="button" className="ds-btn ds-data-btn" disabled={busy || !localCount} title={localCount ? undefined : "Your Local copy is empty"}
            onClick={() => { setPicking(false); setConfirm({ kind: "copy" }); }}>
            Copy Local data into {scope.label}
          </button>
        ) : null}
        {shared && status.webPage ? (
          <button type="button" className="ds-btn ds-data-btn" onClick={() => void getApi()?.plugin_data_open_web?.(pluginId)}>
            <Icons.Globe /> View on the web
          </button>
        ) : null}
      </div>

      {picking ? (
        <div className="ds-data-choices" role="radiogroup" aria-label={`Keep ${name}'s data in`}>
          {choices === null ? <p className="ds-data-help">Loading your {kind}s…</p> : null}
          {/* A folder that can't be shared from this PC: no team to offer, and why. */}
          {refusal && !shared ? <p className="ds-data-help">{refusal}</p> : null}
          {(choices ?? []).filter((choice) => !(refusal && !shared && choice.kind === "shared")).map((choice) => {
            const current = choice.kind === "shared" ? scope.spaceId === choice.id : !shared;
            return (
              <button key={choice.id} type="button" role="radio" aria-checked={current} className="ds-data-choice"
                disabled={busy} onClick={() => { if (!current) setConfirm({ kind: "switch", choice }); }}>
                {choice.kind === "shared" ? <Icons.Users /> : <Icons.Monitor />}
                <span>{choice.kind === "shared" ? `${kindTitle(kind)} · ${choice.label}` : "Local"}</span>
                {choice.members ? <small>{choice.members} members</small> : null}
              </button>
            );
          })}
          {choices && choices.length === 1 && emptyNote ? <p className="ds-data-help">{emptyNote}</p> : null}
        </div>
      ) : null}

      {confirm ? (
        <div className="ds-data-confirm" role="alertdialog" aria-label="Confirm">
          <p>
            {confirm.kind === "copy"
              ? `Copy your Local ${name} data into ${scope.label}? Items ${scope.label} already has are kept. Nothing goes to any other ${kind}.`
              : confirm.choice.kind === "shared"
                ? `Switch everything to ${confirm.choice.label}? ${name}, every other plugin, Workflows and General storage show ${confirm.choice.label}'s copy. Your Local copies stay on this PC, separate. Nothing is copied.`
                : `Switch everything to Local? ${name}, every other plugin, Workflows and General storage show their Local copy. ${scope.label}'s copy stays with the ${kind}.`}
          </p>
          <button type="button" className="ds-btn ds-data-btn ds-data-btn--primary" disabled={busy}
            onClick={() => void (confirm.kind === "copy" ? copyIn() : switchTo(confirm.choice))}>
            {confirm.kind === "copy" ? "Copy" : "Switch"}
          </button>
          <button type="button" className="ds-btn ds-data-btn" disabled={busy} onClick={() => setConfirm(null)}>Cancel</button>
        </div>
      ) : null}

      {message ? <p className="ds-data-help" role="status">{message}</p> : null}
      {share ? (
        <FolderShareDialog
          plugin={name}
          team={share.team}
          units={share.units}
          note={share.note}
          busy={busy}
          onShare={(units) => answerShare({ units })}
          onStartEmpty={() => answerShare({ start_empty: true })}
          onCancel={() => setShare(null)}
        />
      ) : null}
      {shared ? <PluginScopeDrawer pluginId={pluginId} status={status} onChanged={() => refresh()} /> : null}
      <p className="ds-data-help">Each {kind} keeps its own copy. Data never moves between {kind}s.</p>
    </div>
  );
}
