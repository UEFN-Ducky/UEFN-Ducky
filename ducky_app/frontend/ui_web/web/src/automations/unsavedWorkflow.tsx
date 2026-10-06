import { useEffect, useState, useSyncExternalStore } from "react";
import { UnsavedChangesModal } from "../components/UnsavedChangesModal";
import { getApi } from "../hooks/usePanelApi";
import type { AutomationDto } from "../types/panel";

/** The open workflow's edits that are not saved yet, kept on this PC: switching to another
 *  tab, a pop-out window closing or a restart brings them back instead of losing them. */
const KEY = "ducky.workflows.unsaved.v1";
/** Dragging changes the draft every frame: it is written down a moment after it rests. */
const WRITE_DELAY_MS = 300;

/** What the open editor does on Save and Don't save, so its canvas follows. `id` is the
 *  workflow it has open; Save throws with the reason when it fails. */
export type WorkflowEditorHandle = { id: () => string; save: () => Promise<void>; discard: () => void };
/** Asks fields that hold typing back for a moment (the code editor, a details header being
 *  edited) to put it into the workflow now, before a save. */
const FLUSH_EVENT = "ducky:workflow-flush-edits";
type Prompt = { name: string; saving: boolean; error: string; resolve: (go: boolean) => void };

let pending: AutomationDto | null = null;
let editor: WorkflowEditorHandle | null = null;
let prompt: Prompt | null = null;
let writeTimer = 0;
let version = 0;
const hosts: symbol[] = [];
const listeners = new Set<() => void>();
const emit = () => { version += 1; listeners.forEach((listener) => listener()); };
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };

function read(): AutomationDto | null {
  try {
    const doc = JSON.parse(window.localStorage.getItem(KEY) || "null");
    return doc && typeof doc === "object" && typeof doc.id === "string" && doc.id && doc.graph ? doc as AutomationDto : null;
  } catch {
    return null;
  }
}

function write() {
  window.clearTimeout(writeTimer);
  writeTimer = 0;
  try {
    if (pending) window.localStorage.setItem(KEY, JSON.stringify(pending));
    else window.localStorage.removeItem(KEY);
  } catch { /* private mode or full: kept in memory only */ }
}

if (typeof window !== "undefined") {
  pending = read();
  // A pop-out window changed it.
  window.addEventListener("storage", (event) => { if (event.key === KEY && !writeTimer) pending = read(); });
  window.addEventListener("pagehide", () => { if (writeTimer) write(); });
}

export function unsavedWorkflow(): AutomationDto | null {
  return pending;
}

/** What is kept on this PC now (another window may have changed it). */
export function reloadUnsavedWorkflow(): AutomationDto | null {
  if (!writeTimer) pending = read();
  return pending;
}

export function setUnsavedWorkflow(doc: AutomationDto | null) {
  if (doc === pending) return;
  pending = doc;
  if (!doc) write();
  else if (!writeTimer) writeTimer = window.setTimeout(write, WRITE_DELAY_MS);
}

export function flushUnsavedWorkflow() {
  if (writeTimer) write();
}

export function flushWorkflowEdits() {
  window.dispatchEvent(new Event(FLUSH_EVENT));
}

export function onFlushWorkflowEdits(flush: () => void): () => void {
  window.addEventListener(FLUSH_EVENT, flush);
  return () => window.removeEventListener(FLUSH_EVENT, flush);
}

/** The editor has this workflow open (else Save sends what is kept here). */
const editing = (doc: AutomationDto | null) => !!editor && !!doc && editor.id() === doc.id;

export function registerWorkflowEditor(handle: WorkflowEditorHandle): () => void {
  editor = handle;
  return () => { if (editor === handle) editor = null; };
}

/** Before leaving unsaved edits (another workflow, closing the tab): asks Save, Don't save
 *  or Cancel. Resolves true when it is fine to go on. */
export function guardUnsavedWorkflow(): Promise<boolean> {
  if (!pending || !hosts.length) return Promise.resolve(true);
  prompt?.resolve(false);
  return new Promise((resolve) => {
    prompt = { name: pending?.name || "Untitled", saving: false, error: "", resolve };
    emit();
  });
}

/** Save what is kept when the editor isn't open (its tab is in the background). */
async function saveKept() {
  const doc = pending;
  if (!doc) return;
  const { folder: _folder, ...body } = doc;
  const res = await getApi()?.save_workflow?.(body);
  if (!res?.workflow) throw new Error(res?.error ? `Could not save changes. ${res.error}` : "Could not save changes.");
  if (pending === doc) setUnsavedWorkflow(null);
  if (res.workflow.owner?.kind === "team") await getApi()?.workflow_sync?.(true, res.workflow.owner.id, true);
}

function close(open: Prompt, go: boolean) {
  if (prompt !== open) return;
  prompt = null;
  emit();
  open.resolve(go);
}

async function saveFromPrompt() {
  const open = prompt;
  if (!open || open.saving) return;
  prompt = { ...open, saving: true, error: "" };
  emit();
  const saving = prompt;
  try {
    if (editing(pending)) await editor!.save();
    else await saveKept();
  } catch (error) {
    if (prompt !== saving) return;
    prompt = { ...saving, saving: false, error: error instanceof Error && error.message ? error.message : "Could not save changes." };
    emit();
    return;
  }
  close(saving, true);
}

function discardFromPrompt() {
  const open = prompt;
  if (!open || open.saving) return;
  if (editing(pending)) editor!.discard();
  setUnsavedWorkflow(null);
  close(open, true);
}

/** Shows the Save changes? dialog. Mounted by the Workflows editor and by the window around
 *  it (for closing its tab while it is in the background); only the newest one shows it. */
export function UnsavedWorkflowPrompt() {
  const [me] = useState(() => Symbol("unsaved-workflow-prompt"));
  useEffect(() => {
    hosts.push(me);
    emit();
    return () => {
      hosts.splice(hosts.indexOf(me), 1);
      if (!hosts.length && prompt) close(prompt, false);
      emit();
    };
  }, [me]);
  useSyncExternalStore(subscribe, () => version);
  const open = prompt;
  if (!open || hosts[hosts.length - 1] !== me) return null;
  return <UnsavedChangesModal
    saving={open.saving}
    message={<>
      <strong>{open.name}</strong> has unsaved changes. Save them before leaving?
      {open.error ? <><br /><span className="aw-field-error" role="alert">{open.error}</span></> : null}
    </>}
    onSave={() => void saveFromPrompt()}
    onDiscard={discardFromPrompt}
    onCancel={() => close(open, false)} />;
}
