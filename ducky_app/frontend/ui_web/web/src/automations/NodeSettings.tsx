import { useEffect, useId, useState } from "react";
import { ChoiceDropdown } from "../components/ChoiceDropdown";
import { getApi } from "../hooks/usePanelApi";
import type { AutomationBackendDto, AutomationBackendSetupDto, AutomationFieldDto, AutomationGraphNodeDto, AutomationNodeDto, AutomationSummaryDto } from "../types/panel";
import { openPanelRoute } from "../navigation/openPanelRoute";
import { playShowMe, type ShowMeRequest } from "../showme/ShowMeService";
import { AgentField } from "./AgentField";
import { CallSettings, NamedValueList } from "./FunctionSettings";
import { ProjectField } from "./ProjectField";
import { ToolSettings } from "./ToolSettings";
import { DuckyModelPicker } from "../components/ducky/DuckyModelPicker";
import { Icons } from "../icons/Icons";
import { ExpressionField, FilePicker, NamesField } from "./PinFields";
import { nodePins } from "./pins";

export function hasNodeSettings(node: AutomationGraphNodeDto, meta?: AutomationNodeDto) {
  if (node.type === "code.js") return codeSettingsSpec(node).length > 0 || codeBuiltins(node).length > 0;
  return node.type === "tool.call" || !!meta?.config_fields?.length;
}

/** The settings a custom code node's code declares (kept from its last good check). */
function codeSettingsSpec(node: AutomationGraphNodeDto): AutomationFieldDto[] {
  return Array.isArray(node.config.settings_spec) ? (node.config.settings_spec as AutomationFieldDto[]).filter((field) => !!field && typeof field.id === "string" && !!field.id) : [];
}

function codeBuiltins(node: AutomationGraphNodeDto): string[] {
  const uses = node.config.uses as { builtins?: unknown } | undefined;
  return Array.isArray(uses?.builtins) ? uses.builtins.map(String) : [];
}

/** A custom code node: the settings its code declares (values kept in config.settings, the
 *  declared default shown until one is set) and its own Spend switch for paid built-ins. */
function CodeSettings({ node, onChange }: { node: AutomationGraphNodeDto; onChange: (node: AutomationGraphNodeDto) => void }) {
  const values = (node.config.settings && typeof node.config.settings === "object" ? node.config.settings : {}) as Record<string, unknown>;
  const builtins = codeBuiltins(node);
  const spend = node.config.spend === true;
  return <div className="aw-insp-form">
    {codeSettingsSpec(node).map((field) => <ConfigField key={field.id} field={field}
      node={{ ...node, config: { ...(field.default !== undefined ? { [field.id]: field.default } : {}), ...values } }}
      onChange={(edited) => onChange({ ...node, config: { ...node.config, settings: { ...values, [field.id]: edited.config[field.id] } } })} />)}
    {builtins.length ? <div className="aw-field aw-spend">
      <button type="button" role="switch" aria-checked={spend} aria-label="Spend credits" className={`aw-spend-toggle${spend ? " is-on" : ""}`}
        onClick={() => onChange({ ...node, config: { ...node.config, spend: !spend } })}>
        <span>Spend credits</span><span className="aw-switch" aria-hidden="true"><span /></span>
      </button>
      <small className="aw-field-hint">Its code runs {builtins.join(", ")}. {spend ? "Paid ones spend when the workflow runs." : "Off: paid ones spend only when you press play."}</small>
    </div> : null}
  </div>;
}

/** Run workflow nodes list the other workflows; `onOpen` jumps to the one it runs. */
type WorkflowChoices = { workflows?: AutomationSummaryDto[]; currentId?: string; onOpen?: (id: string) => void };

export function NodeSettings({ node, meta, onChange, workflows = [], currentId, onOpen }: { node: AutomationGraphNodeDto; meta?: AutomationNodeDto; onChange: (node: AutomationGraphNodeDto) => void } & WorkflowChoices) {
  if (node.type === "code.js") return <CodeSettings node={node} onChange={onChange} />;
  return <div className="aw-insp-form">
    {node.type === "tool.call" ? <ToolSettings node={node} onChange={onChange} />
      : node.type === "workflow.call" ? <CallSettings node={node} workflows={workflows} currentId={currentId} onChange={onChange} onOpen={onOpen} />
      : (meta?.config_fields || []).map((field) => <NodeField key={field.id} field={field} node={node} pluginId={meta?.plugin_id} backends={meta?.backends}
        backendKey={meta?.config_fields?.find((row) => row.type === "backend")?.id} onChange={onChange} />)}
  </div>;
}

/** backendKey: the setting that holds the picked backend ("backend"; a plugin node may name its own). */
type Backends = { backends?: AutomationBackendDto[]; backendKey?: string };

function NodeField({ field, node, pluginId, backends, backendKey, onChange }: { field: AutomationFieldDto; node: AutomationGraphNodeDto; pluginId?: string; onChange: (node: AutomationGraphNodeDto) => void } & Backends) {
  if (field.type === "params") return <NamedValueList node={node} field={field.id} valueKey="default" valueLabel="Default" valuePlaceholder="used by Test" addLabel="Add input" onChange={onChange} />;
  if (field.type === "returns") return <NamedValueList node={node} field={field.id} valueKey="value" valueLabel="Value" valuePlaceholder="same-name field, text or {{field}}" addLabel="Add return value" onChange={onChange} />;
  if (field.type === "backend") return <BackendField field={field} node={node} backends={backends || []} onChange={onChange} />;
  if (field.id === "spend" && backends?.length) return <SpendField node={node} backends={backends} backendKey={backendKey} onChange={onChange} />;
  return <ConfigField field={field} node={node} pluginId={pluginId} onChange={onChange} />;
}

/** The backend picked for this run; none picked = the first that can run here (as the runner does). */
function pickedBackend(node: AutomationGraphNodeDto, backends: AutomationBackendDto[], key = "backend") {
  return backends.find((row) => row.id === String(node.config[key] || "")) || backends.find((row) => row.available) || backends[0];
}

function costOf(row: AutomationBackendDto) {
  return row.cost || `~${row.credits} credits`;
}

/** Image gateways expose their own image settings. Unavailable gateways explain setup.
 *  A plugin node's Backend field picks from another node's list (node_type, Text to Image by default). */
function BackendField({ field, node, backends, onChange }: { field: AutomationFieldDto; node: AutomationGraphNodeDto; backends: AutomationBackendDto[]; onChange: (node: AutomationGraphNodeDto) => void }) {
  const id = useId();
  const key = field.id || "backend";
  const label = field.label || "Backend";
  const images = (field.node_type || node.type) === "image.generate";
  const picked = pickedBackend(node, backends, key);
  const shown = images ? backends : backends.filter((row) => row.available || row.id === picked?.id);
  const setConfig = (patch: Record<string, unknown>) => onChange({ ...node, config: { ...node.config, ...patch } });
  return <div className="aw-field">
    <label className="aw-field-label" htmlFor={id}>{label}</label>
    <ChoiceDropdown id={id} aria-label={label} size="compact" value={picked?.id || ""}
      options={shown.map((row) => ({ value: row.id, label: row.label, disabled: !row.available,
        hint: row.available ? `${costOf(row)} · ${row.plugin}` : `${row.plugin} · ${row.reason || "not set up"}` }))}
      onChange={(value) => setConfig({ [key]: value })} />
    {picked && !picked.available ? <small className="aw-field-error" role="status">{picked.reason || `Needs the ${picked.plugin} plugin.`}</small> : null}
    {picked && !picked.available && picked.setup ? <BackendSetup setup={picked.setup} plugin={picked.plugin} /> : null}
    {!picked && images ? <small className="aw-field-error" role="status">Turn on an image-capable gateway or image plugin in the Store.</small> : null}
    {images && node.config[key] === "agent" ? <small className="aw-field-error" role="status">Choose a direct image backend to replace the saved agent backend.</small> : null}
    {picked?.config_fields?.map((field) => {
      const configs = (node.config.gateway_config || {}) as Record<string, Record<string, unknown>>;
      const config = configs[picked.id] || {};
      return <ConfigField key={`${picked.id}:${field.id}`} field={field}
        node={{ ...node, config: { model: picked.model, ...config } }}
        onChange={(edited) => setConfig({ gateway_config: { ...configs, [picked.id]: edited.config } })} />;
    })}
  </div>;
}

/** The Show me for a backend that isn't set up: the key field (and Save) for a gateway,
 *  the Store page's install / turn-on buttons for a plugin. */
export function backendSetupTour(setup: AutomationBackendSetupDto, plugin: string): ShowMeRequest {
  if (setup.kind === "key") {
    const first = {
      navigate: setup.route, item_id: setup.item, target: "settings.llms.provider.key",
      title: `Add your ${plugin} API key`,
      body: `Paste your ${plugin} API key here. Pictures made with ${plugin} are billed to your own ${plugin} account — never Ducky credits or a wallet.`,
    };
    return { ...first, steps: [first, {
      target: "settings.llms.provider.save", title: "Save it",
      body: `Save the key. Back in your workflow, ${plugin} is ready to pick in Text to Image.`,
    }] };
  }
  const verb = setup.kind === "install" ? "Install" : "Turn on";
  return {
    navigate: setup.route, item_id: setup.item, target: "settings.store.detail.actions",
    title: `${verb} ${plugin}`,
    body: `${verb} ${plugin} here. Then it shows up as a backend in Text to Image.`,
  };
}

/** A backend that can't run yet: a button that goes where it's fixed, and a Show me that explains it. */
function BackendSetup({ setup, plugin }: { setup: AutomationBackendSetupDto; plugin: string }) {
  return <div className="aw-backend-setup">
    <button type="button" className="is-primary" onClick={() => openPanelRoute(setup.route, setup.item)}>{setup.label}</button>
    <button type="button" className="aw-link" onClick={() => void playShowMe(backendSetupTour(setup, plugin))}>Show me</button>
  </div>;
}

/** What one run costs: the person's own API key (a gateway), the plugin's own words, or about N credits. */
function spendHint(row: AutomationBackendDto) {
  if (row.own_key) return `${row.label} is billed to your ${row.plugin} API key each run.`;
  if (row.cost && row.cost !== `~${row.credits} credits`) return `${row.label} costs ${row.cost} each run (${row.plugin}).`;
  return `About ${row.credits} credits each run on ${row.label} (${row.plugin}).`;
}

/** Paid steps only spend credits with this on; it says about how many each run. */
function SpendField({ node, backends, backendKey, onChange }: { node: AutomationGraphNodeDto; backends: AutomationBackendDto[]; backendKey?: string; onChange: (node: AutomationGraphNodeDto) => void }) {
  const on = node.config.spend === true;
  const picked = pickedBackend(node, backends, backendKey);
  return <div className="aw-field aw-spend">
    <button type="button" role="switch" aria-checked={on} aria-label="Spend credits" className={`aw-spend-toggle${on ? " is-on" : ""}`}
      onClick={() => onChange({ ...node, config: { ...node.config, spend: on ? undefined : true } })}>
      <span>Spend credits</span><span className="aw-switch" aria-hidden="true"><span /></span>
    </button>
    <small className="aw-field-hint">{picked ? spendHint(picked) : ""} {on ? "It runs when the workflow does." : "Off: the run stops here and says so, nothing is spent."}</small>
  </div>;
}

/** Save file: a folder on this PC, typed or picked. */
function FolderField({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (value: string) => void }) {
  const [error, setError] = useState("");
  const pick = async () => {
    setError("");
    const res = await getApi()?.pick_workflow_folder?.();
    if (!res) { setError("The folder picker isn't available here."); return; }
    if (res.ok === false) { setError(res.error || "Could not open the folder picker."); return; }
    if (res.folder) onChange(res.folder);
  };
  return <div className="aw-folder-field">
    <input id={id} aria-label={label} value={value} placeholder="A folder on this PC" spellCheck={false} onChange={(event) => onChange(event.target.value)} />
    <button type="button" className="aw-file-pick" aria-label={`Choose ${label}`} onClick={() => void pick()}><Icons.Folder /> Choose</button>
    {error ? <small className="aw-field-error" role="status">{error}</small> : null}
  </div>;
}

function ConfigField({ field, node, pluginId, onChange }: { field: AutomationFieldDto; node: AutomationGraphNodeDto; pluginId?: string; onChange: (node: AutomationGraphNodeDto) => void }) {
  const id = useId();
  const raw = node.config[field.id];
  const value = String(raw ?? "");
  const label = field.label || field.id;
  const set = (next: unknown) => onChange({ ...node, config: { ...node.config, [field.id]: next } });
  const [models, setModels] = useState<Array<{ id: string; name?: string }>>([]);
  const provider = field.provider || (pluginId === "google" ? "gemini" : pluginId) || "";
  useEffect(() => {
    if (field.type !== "model" || !provider) return;
    let cancelled = false;
    void getApi()?.get_models(provider)?.then((rows) => { if (!cancelled && Array.isArray(rows)) setModels(rows); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [field.type, provider]);

  let input;
  if (field.type === "expression") {
    const names = nodePins(node, undefined).inputs.map((pin) => pin.id);
    input = <ExpressionField id={id} label={label} value={value} names={names} onChange={set} />;
  } else if (field.type === "names") {
    const fallback = nodePins({ ...node, config: { ...node.config, names: [] } }, undefined).inputs.map((pin) => pin.id);
    input = <NamesField id={id} label={label} value={raw} fallback={fallback} onChange={set} />;
  } else if (field.type === "file" || field.type === "files") {
    input = <FilePicker id={id} label={label} value={raw} accept={field.accept || "any"} multiple={field.type === "files"} onChange={set} />;
  } else if (field.type === "model" && !provider) {
    // Every gateway and agent you have, like the chat composer's picker (saved as "backend:model").
    input = <div className="aw-model-field"><DuckyModelPicker model={value} onChange={(model) => set(model || undefined)} label="" hint="" placeholder="The app's default model" menuPlacement="bottom" labeled /></div>;
  } else if (field.type === "folder") input = <FolderField id={id} label={label} value={value} onChange={(next) => set(next || undefined)} />;
  else if (field.type === "project") input = <ProjectField id={id} label={label} value={value} onChange={set} />;
  else if (field.type === "ducky") input = <AgentField id={id} label={label} value={String(raw ?? node.config.profile_id ?? "")} onChange={set} />;
  else if (["boolean", "bool", "checkbox"].includes(field.type || "")) input = <ChoiceDropdown id={id} aria-label={label} value={value} options={[{ value: "", label: "Default" }, { value: "true", label: "Yes" }, { value: "false", label: "No" }]} onChange={(next) => set(next === "" ? undefined : next === "true")} size="compact" />;
  else if (["select", "model", "multiselect"].includes(field.type || "")) {
    const choices = field.type === "model" ? models.map((model) => ({ value: model.id, label: model.name || model.id })) : (field.options || []).map((option) => ({ value: option.id, label: option.label || option.id }));
    if (field.type === "multiselect") {
      const values = Array.isArray(raw) ? raw.map(String) : [];
      for (const saved of values) if (!choices.some((choice) => choice.value === saved)) choices.push({ value: saved, label: saved });
      input = <ChoiceDropdown id={id} aria-label={label} mode="checkbox" values={values} options={choices} onChange={set} searchable size="compact" />;
    }
    else {
      const options = [{ value: "", label: "Default" }, ...choices];
      if (value && !options.some((option) => option.value === value)) options.push({ value, label: value });
      input = <ChoiceDropdown id={id} aria-label={label} value={value} options={options} onChange={set} searchable={options.length > 8} size="compact" />;
    }
  } else if (field.type === "textarea") input = <textarea id={id} aria-label={label} rows={3} value={value} onChange={(event) => set(event.target.value)} />;
  else input = <input id={id} aria-label={label} type={field.type === "number" ? "number" : "text"} value={value} onChange={(event) => set(field.type === "number" && event.target.value !== "" ? Number(event.target.value) : event.target.value)} />;
  return <div className="aw-field" data-aw-field={field.id}><label className="aw-field-label" htmlFor={id}>{label}</label>{input}</div>;
}
