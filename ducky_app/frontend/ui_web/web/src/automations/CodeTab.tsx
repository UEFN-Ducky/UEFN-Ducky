import { useEffect, useMemo, useRef, useState } from "react";
import { getApi } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";
import type {
  AutomationGraphDto,
  AutomationGraphEdgeDto,
  AutomationGraphNodeDto,
  AutomationNodeDto,
  AutomationRunStepDto,
  AutomationSummaryDto,
  CodeCheckDto,
  CodeProblemDto,
  CodeTestResultDto,
  WorkflowCodeApiDto,
  WorkflowNodeCodeDto,
} from "../types/panel";
import { CodeDiff, CodeEditor, type CodeMarker } from "../verse-editor/components/CodeEditor";
import {
  basedOn, codeOf, convertNode, declarationKey, isCodeNode, MAX_CODE_BYTES, pinDiff, pinDiffEmpty, pinDiffText, revertNode, wiresDropped, wireText, withCheck,
} from "./codeNode";
import { nodeLabel } from "./NodeVisuals";
import { PinValueEditor } from "./PinFields";
import { cleanType, nodePins, PIN_TYPE_LABELS, type NodePins } from "./pins";

/** How long typing rests before the code reaches the draft and is checked. */
const EDIT_DELAY_MS = 300;

export type CodeTabProps = {
  workflowId: string;
  node: AutomationGraphNodeDto;
  graph: AutomationGraphDto;
  byType: Map<string, AutomationNodeDto>;
  workflows: AutomationSummaryDto[];
  /** The node's pins now (what the canvas draws). */
  pins: NodePins;
  /** Read-only here (someone else's team workflow, no rights). */
  readOnly: boolean;
  /** Locked itself or by a group around it. */
  locked: boolean;
  /** A team's workflow: custom code runs in Local workflows only for now. */
  team: boolean;
  /** This node's newest step in the last run (its inputs, log, code error). */
  lastStep?: AutomationRunStepDto;
  /** Show this line (a failed step clicked in the run log). */
  reveal?: { line: number; nonce: number } | null;
  wide: boolean;
  onWide: (wide: boolean) => void;
  /** Code typed in: into the draft, one undo step per focus. */
  onNodeChange: (node: AutomationGraphNodeDto, label?: string) => void;
  /** Edit as custom code / Revert to built-in: into the draft and saved. */
  onNodeReplace: (node: AutomationGraphNodeDto, label: string) => void;
  /** Typing in the editor starts ("begin") and ends ("end") one undo step. */
  onCodeSession: (phase: "begin" | "end", nodeId: string) => void;
  onRunNode?: (id: string) => void;
  runningNode?: string;
};

const apiCache = new Map<string, Promise<WorkflowCodeApiDto | null>>();

/** The `ducky` types (and typed args for the tools the code names), fetched once per set of tools. */
function loadCodeApi(tools: string[]): Promise<WorkflowCodeApiDto | null> {
  const key = [...tools].sort().join(",");
  let found = apiCache.get(key);
  if (!found) {
    found = Promise.resolve(getApi()?.workflow_code_api?.(tools.length ? tools : null)).then((res) => res && res.ok !== false ? res : null).catch(() => null);
    found.then((res) => { if (!res) apiCache.delete(key); }).catch(() => undefined);
    apiCache.set(key, found);
  }
  return found;
}

function bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

function problemText(problem: { line?: number; message: string }): string {
  return problem.line ? `Line ${problem.line}: ${problem.message}` : problem.message;
}

/** The problems chip: how many, and each one jumps to its line. */
function ProblemsChip({ problems, onPick }: { problems: CodeMarker[]; onPick: (line: number) => void }) {
  const [open, setOpen] = useState(false);
  const shown = problems.filter((problem) => problem.severity !== "info");
  const errors = shown.filter((problem) => problem.severity === "error").length;
  useEffect(() => { if (!shown.length) setOpen(false); }, [shown.length]);
  if (!shown.length) return <span className="aw-code-chip is-ok" role="status"><Icons.Check /> No problems</span>;
  return <span className="aw-code-chip-wrap">
    <button type="button" className={`aw-code-chip ${errors ? "is-error" : "is-warning"}`} aria-expanded={open} onClick={() => setOpen((value) => !value)}>
      <Icons.AlertTriangle /> {shown.length} problem{shown.length === 1 ? "" : "s"}
    </button>
    {open ? <ul className="aw-code-problems" aria-label="Problems">
      {shown.map((problem, index) => <li key={`${problem.line}-${index}`}>
        <button type="button" className={`is-${problem.severity}`} onClick={() => onPick(problem.line)}>{problemText(problem)}</button>
      </li>)}
    </ul> : null}
  </span>;
}

/** An inline confirm under the buttons (it stays in the details, nothing pops over the canvas). */
function ConfirmBox({ label, text, wires, confirmLabel, onConfirm, onCancel }: { label: string; text: string; wires: string[]; confirmLabel: string; onConfirm: () => void; onCancel: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.querySelector<HTMLButtonElement>(".is-primary")?.focus({ preventScroll: true }); }, []);
  return <div ref={ref} className="aw-code-confirm" role="dialog" aria-label={label} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); onCancel(); } }}>
    <p>{text}</p>
    {wires.length ? <>
      <p className="aw-code-confirm-warn">{wires.length === 1 ? "This data wire will be disconnected:" : `These ${wires.length} data wires will be disconnected:`}</p>
      <ul className="aw-code-wires">{wires.map((wire) => <li key={wire}>{wire}</li>)}</ul>
    </> : null}
    <div className="aw-code-confirm-actions">
      <button type="button" className="is-primary" onClick={onConfirm}>{confirmLabel}</button>
      <button type="button" onClick={onCancel}>Cancel</button>
    </div>
  </div>;
}

/** A node's Code tab: the JavaScript it runs. A built-in shows its generated code (read-only)
 *  and can become custom code in this workflow; a custom code node is edited here. */
export function CodeTab(props: CodeTabProps) {
  const { workflowId, node } = props;
  const [info, setInfo] = useState<WorkflowNodeCodeDto | null>(null);
  const [infoState, setInfoState] = useState<"loading" | "ready" | "failed">("loading");
  const [infoError, setInfoError] = useState("");
  const custom = isCodeNode(node);
  // A built-in's code follows its settings; a custom node's server view changes when it is saved.
  const fetchKey = custom ? `${node.id}|code|${String(node.config.code_sha || "")}` : `${node.id}|${node.type}|${JSON.stringify(node.config)}`;
  useEffect(() => {
    if (!workflowId) { setInfo(null); setInfoState("failed"); setInfoError("Save the workflow first to see its code."); return; }
    let cancelled = false;
    let timer = 0;
    setInfoState((state) => (custom && state === "ready" ? state : "loading"));
    const load = (tries: number) => {
      void Promise.resolve(getApi()?.get_workflow_node_code?.(workflowId, node.id)).then((res) => {
        if (cancelled) return;
        if (!res || res.ok === false) { setInfoState("failed"); setInfoError(res?.error || "Its code isn't available here."); return; }
        setInfo(res);
        setInfoState("ready");
        setInfoError("");
        // Just converted: the save may not have landed yet, so ask again shortly.
        if (custom && res.kind !== "custom" && tries < 3) timer = window.setTimeout(() => load(tries + 1), 1000);
      }).catch(() => { if (!cancelled) { setInfoState("failed"); setInfoError("Its code isn't available here."); } });
    };
    timer = window.setTimeout(() => load(0), custom ? 0 : 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [workflowId, fetchKey]);
  // The built-in's generated code at the moment it was converted, until the server has the custom node.
  const builtinCode = useRef("");
  if (!custom && info?.kind === "builtin") builtinCode.current = info.code;
  return custom
    ? <CustomCode {...props} info={info?.kind === "custom" ? info : null} builtinCode={builtinCode.current} onApproved={() => setInfo((current) => current ? { ...current, approved: true } : current)} />
    : <BuiltinCode {...props} info={info} state={infoState} error={infoError} />;
}

function BuiltinCode({ node, graph, byType, readOnly, locked, team, info, state, error, onNodeReplace, workflowId }: CodeTabProps & { info: WorkflowNodeCodeDto | null; state: "loading" | "ready" | "failed"; error: string }) {
  const meta = byType.get(node.type);
  const name = meta?.label || node.type;
  const [confirming, setConfirming] = useState(false);
  const nameOf = (id: string) => { const item = graph.nodes.find((row) => row.id === id); return item ? nodeLabel(item, byType.get(item.type)) : id; };
  const blocked = readOnly ? "This workflow is read-only here. Duplicate it to change a Local copy."
    : locked ? "This node is locked. Unlock it to edit its code."
    : team ? "Custom code runs in Local workflows for now."
    : !info ? (state === "loading" ? "Loading its code…" : error || "Its code isn't available here.")
    : info.kind === "flow" || !info.convertible ? info.reason || "This step steers the workflow, so it can't become custom code."
    : "";
  useEffect(() => { if (blocked) setConfirming(false); }, [blocked]);
  const dropped: AutomationGraphEdgeDto[] = info ? wiresDropped(graph, node.id, info.pins) : [];
  return <div className="aw-code-tab">
    <p className="aw-code-strip"><span className="aw-code-badge" aria-hidden="true">&lt;/&gt;</span>
      <span><strong>Built-in · {name}</strong> - the code this step runs with its current settings</span></p>
    {info?.kind === "flow" ? <p className="aw-field-hint">This step steers the workflow, so its code only explains what it does.</p> : null}
    {info?.reason && info.convertible && info.kind !== "flow" ? <p className="aw-field-hint aw-code-note">{info.reason}</p> : null}
    {info ? <CodeEditor value={info.code} readOnly path={`${workflowId}/${node.id}/builtin`} label={`Code of ${name} (read-only)`} className="aw-code-editor" />
      : <p className="aw-field-hint" role="status">{state === "loading" ? "Loading its code…" : error}</p>}
    <div className="aw-code-actions">
      <button type="button" disabled={!!blocked} title={blocked || `Make this ${name} a Custom code node in this workflow only`} aria-expanded={confirming} onClick={() => setConfirming(true)}>
        <Icons.Pencil /> Edit as custom code
      </button>
    </div>
    {blocked ? <p className="aw-field-hint aw-code-why">{blocked}</p> : null}
    {confirming && info ? <ConfirmBox label="Edit as custom code" confirmLabel="Convert"
      text="This node becomes Custom code in this workflow only. The built-in stays the same everywhere else."
      wires={dropped.map((edge) => wireText(edge, nameOf))}
      onCancel={() => setConfirming(false)}
      onConfirm={() => { setConfirming(false); onNodeReplace(convertNode(node, info), "Edit as custom code"); }} /> : null}
  </div>;
}

type CustomProps = CodeTabProps & { info: WorkflowNodeCodeDto | null; builtinCode: string; onApproved: () => void };

function CustomCode(props: CustomProps) {
  const { workflowId, node, graph, byType, workflows, pins, readOnly, locked, lastStep, info, onNodeChange, onCodeSession } = props;
  const frozen = readOnly || locked;
  const from = basedOn(node);
  const fromName = from ? byType.get(from.type)?.label || from.type : "";
  const nameOf = (id: string) => { const item = graph.nodes.find((row) => row.id === id); return item ? nodeLabel(item, byType.get(item.type)) : id; };

  // The text in the editor; it reaches the draft a moment after typing rests.
  const [code, setCode] = useState(() => codeOf(node));
  const pushed = useRef(code);
  const pending = useRef<string | null>(null);
  const timer = useRef(0);
  const nodeRef = useRef(node);
  nodeRef.current = node;
  const session = useRef(false);
  const [edited, setEdited] = useState(false);
  const [check, setCheck] = useState<CodeCheckDto | null>(null);
  const checkSeq = useRef(0);
  const [serviceProblems, setServiceProblems] = useState<CodeMarker[]>([]);
  const [reveal, setReveal] = useState<{ line: number; nonce: number } | null>(props.reveal || null);
  useEffect(() => { if (props.reveal) setReveal(props.reveal); }, [props.reveal?.nonce]);
  const show = (line: number) => setReveal((current) => ({ line, nonce: (current?.nonce || 0) + 1 }));

  const runCheck = (text: string) => {
    const seq = ++checkSeq.current;
    void Promise.resolve(getApi()?.check_workflow_node_code?.(text)).then((res) => {
      if (!res || seq !== checkSeq.current) return;
      setCheck(res);
      const current = nodeRef.current;
      if (codeOf(current) !== text) return;
      const next = withCheck(current, res);
      // Pins, settings and tools change on the canvas only after a check passes.
      if (declarationKey(next) !== declarationKey(current)) onNodeChange(next, "Edit code");
    }).catch(() => undefined);
  };

  const flush = () => {
    window.clearTimeout(timer.current);
    const text = pending.current;
    if (text === null) return;
    pending.current = null;
    const current = nodeRef.current;
    if (codeOf(current) !== text) {
      pushed.current = text;
      onNodeChange({ ...current, config: { ...current.config, code: text } }, "Edit code");
    }
    runCheck(text);
  };
  const flushRef = useRef(flush);
  flushRef.current = flush;

  const edit = (text: string) => {
    if (frozen) return;
    setCode(text);
    setEdited(true);
    pending.current = text;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => flushRef.current(), EDIT_DELAY_MS);
  };

  // A change from outside (undo, a save, an agent): the editor follows unless it is the text sent from here.
  const incoming = codeOf(node);
  useEffect(() => {
    if (incoming === pushed.current) return;
    window.clearTimeout(timer.current);
    pending.current = null;
    pushed.current = incoming;
    setCode(incoming);
  }, [incoming]);

  // Check the code once when it opens (problems and the pins preview).
  useEffect(() => { runCheck(codeOf(node)); }, [node.id]);

  const begin = () => {
    if (session.current) return;
    session.current = true;
    onCodeSession("begin", node.id);
  };
  const end = () => {
    flushRef.current();
    if (!session.current) return;
    session.current = false;
    onCodeSession("end", node.id);
  };
  const endRef = useRef(end);
  endRef.current = end;
  useEffect(() => () => endRef.current(), []);

  // The last run's error is drawn until the code changes here.
  const runError = !edited && lastStep?.code_error ? lastStep.code_error : null;
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<CodeTestResultDto | null>(null);
  const [testError, setTestError] = useState("");
  const checkProblems: CodeProblemDto[] = check ? check.problems || [] : Array.isArray(node.config.problems) ? node.config.problems as CodeProblemDto[] : [];
  const tooBig = bytes(code) > MAX_CODE_BYTES;
  const markers = useMemo<CodeMarker[]>(() => [
    ...checkProblems.map((problem) => ({ line: problem.line, col: problem.col, message: problem.message, severity: problem.severity })),
    ...(runError?.line ? [{ line: runError.line, col: runError.col, message: `Last run: ${runError.message}`, severity: "error" as const }] : []),
    ...(result?.error?.line ? [{ line: result.error.line, col: result.error.col, message: `Test: ${result.error.message}`, severity: "error" as const }] : []),
  ], [check, node.config.problems, runError?.line, runError?.message, result]);
  const chipProblems = [...markers, ...serviceProblems, ...(tooBig ? [{ line: 1, message: `The code is over ${MAX_CODE_BYTES / 1024} KB, so it can't be saved.`, severity: "error" as const }] : [])];

  // Pins: what the code declares now against what the saved node has.
  const baseline = useRef<NodePins>(info?.pins || pins);
  if (info?.pins) baseline.current = info.pins;
  const diff = pinDiff(baseline.current, pins);
  const dropping = wiresDropped(graph, node.id, pins);
  const diffLines = pinDiffText(diff, dropping.length);

  const libsKey = JSON.stringify((node.config.uses as { tools?: unknown } | undefined)?.tools || []);
  const [libs, setLibs] = useState<string[] | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    const tools = (JSON.parse(libsKey) as unknown[]).map(String);
    void loadCodeApi(tools).then((res) => { if (!cancelled && res) setLibs([res.dts, res.tools_dts || ""].filter(Boolean)); });
    return () => { cancelled = true; };
  }, [libsKey]);

  const [comparing, setComparing] = useState(false);
  const basedOnCode = info?.based_on_code || props.builtinCode;
  const [confirmRevert, setConfirmRevert] = useState(false);
  const reverted = from ? revertNode(node) : null;
  const revertDrops = reverted ? wiresDropped(graph, node.id, nodePins(reverted, byType.get(reverted.type), workflows)) : [];

  const [reviewError, setReviewError] = useState("");
  const needsReview = !!info && info.approved === false && info.code === code;
  const approve = async () => {
    if (!info) return;
    setReviewError("");
    const res = await Promise.resolve(getApi()?.approve_workflow_node_code?.(workflowId, node.id, info.code_sha)).catch(() => null);
    if (res && res.ok !== false) props.onApproved();
    else setReviewError(res?.error || "Could not mark it reviewed.");
  };

  // Test: the code in the editor (saved or not), with inputs from its last run.
  const [testInputs, setTestInputs] = useState<Record<string, unknown> | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const inputs = testInputs ?? info?.last_inputs ?? lastStep?.inputs ?? {};
  const runTest = async () => {
    flush();
    setTesting(true);
    setTestError("");
    try {
      const sent = Object.fromEntries(pins.inputs.filter((pin) => inputs[pin.id] !== undefined).map((pin) => [pin.id, inputs[pin.id]]));
      const settings = node.config.settings && typeof node.config.settings === "object" ? node.config.settings as Record<string, unknown> : {};
      const res = await getApi()?.test_workflow_node?.(workflowId, node.id, code, sent, settings, dryRun);
      if (!res) { setTestError("Testing isn't available here."); return; }
      setResult(res);
      if (res.error?.line) show(res.error.line);
    } catch (error) {
      setTestError(error instanceof Error ? error.message : "The test could not run.");
    } finally {
      setTesting(false);
    }
  };

  return <div className="aw-code-tab">
    <div className="aw-code-strip is-custom">
      <span className="aw-code-badge" aria-hidden="true">&lt;/&gt;</span>
      <span><strong>Custom code</strong>{from ? ` · made from ${fromName}` : ""}</span>
      <ProblemsChip problems={chipProblems} onPick={show} />
    </div>
    {props.team ? <p className="aw-field-error aw-code-why" role="note">Custom code runs in Local workflows for now: a team workflow can't save or run it. Copy this workflow to Local to use it.</p> : null}
    {needsReview ? <div className="aw-code-review" role="status">
      <p>An agent changed this code, so it won't run on its own yet. Read it, then press Review, or run it once yourself.</p>
      <button type="button" className="is-primary" title="You read this code: it may run on its own on this PC" onClick={() => void approve()}><Icons.Check /> Review</button>
      {reviewError ? <small className="aw-field-error" role="alert">{reviewError}</small> : null}
    </div> : null}
    {comparing && from ? <CodeDiff original={basedOnCode} modified={code} path={`${workflowId}/${node.id}`} />
      : <CodeEditor value={code} onChange={edit} readOnly={frozen} path={`${workflowId}/${node.id}`} label="Code" className="aw-code-editor"
        markers={markers} libs={libs} reveal={reveal} onFocus={begin} onBlur={end} onServiceMarkers={setServiceProblems} />}
    {runError ? <button type="button" className="aw-code-error-line" onClick={() => runError.line && show(runError.line)}>Last run failed: {problemText(runError)}</button> : null}
    {!pinDiffEmpty(diff) || dropping.length ? <div className="aw-code-pins" aria-live="polite">
      <strong>Pins after this change</strong>
      <ul>{diffLines.map((line) => <li key={line} className={line.startsWith("+") ? "is-added" : line.startsWith("−") || line.includes("disconnected") ? "is-removed" : undefined}>{line}</li>)}</ul>
    </div> : null}
    <div className="aw-code-actions">
      {props.onRunNode ? <button type="button" disabled={!!props.runningNode || !workflowId} title="Save, then run this node now (what feeds it reuses the last run)"
        onClick={() => { flush(); props.onRunNode?.(node.id); }}>{props.runningNode === node.id ? <Icons.Spinner /> : <Icons.Play />} Run</button> : null}
      {from ? <button type="button" aria-pressed={comparing} disabled={!basedOnCode} title={basedOnCode ? `What changed from the built-in ${fromName}` : "Save first to compare"} onClick={() => setComparing((value) => !value)}>
        <Icons.Diff /> Compare with built-in</button> : null}
      {from && !frozen ? <button type="button" aria-expanded={confirmRevert} onClick={() => setConfirmRevert(true)}><Icons.Undo /> Revert to built-in</button> : null}
      <button type="button" aria-pressed={props.wide} title={props.wide ? "Narrow the details panel again" : "Widen the details panel while you code"} onClick={() => props.onWide(!props.wide)}>
        {props.wide ? <Icons.Minimize /> : <Icons.Maximize />} {props.wide ? "Pop in" : "Pop out"}</button>
    </div>
    {confirmRevert && reverted ? <ConfirmBox label="Revert to built-in" confirmLabel="Revert"
      text={`It goes back to the built-in ${fromName} with the settings it had when it was converted. This code is dropped (Ctrl+Z brings it back).`}
      wires={revertDrops.map((edge) => wireText(edge, nameOf))}
      onCancel={() => setConfirmRevert(false)}
      onConfirm={() => { setConfirmRevert(false); window.clearTimeout(timer.current); pending.current = null; props.onNodeReplace(reverted, "Revert to built-in"); }} /> : null}
    <details className="aw-insp-section aw-fold aw-code-test">
      <summary className="aw-pins-title">Test</summary>
      <p className="aw-field-hint">Runs the code in the editor once, saved or not, and shows what it gave back. Nothing is saved.</p>
      {pins.inputs.length ? <fieldset className="aw-insp-section aw-code-test-inputs" disabled={testing}>
        <legend className="aw-pins-title">Inputs</legend>
        {pins.inputs.map((pin) => <div key={pin.id} className="aw-field aw-pin-field">
          <span className="aw-pin-field-head"><span className={`aw-pin-dot aw-pin-type--${cleanType(pin.type)}`} aria-hidden="true" />{pin.label}<small>{PIN_TYPE_LABELS[cleanType(pin.type)]}</small></span>
          <PinValueEditor pin={pin} value={inputs[pin.id]} onChange={(value) => {
            const next = { ...inputs };
            if (value === undefined) delete next[pin.id]; else next[pin.id] = value;
            setTestInputs(next);
          }} />
        </div>)}
      </fieldset> : null}
      <button type="button" role="switch" aria-checked={dryRun} aria-label="Dry run" className={`aw-spend-toggle${dryRun ? " is-on" : ""}`} onClick={() => setDryRun((value) => !value)}>
        <span>Dry run</span><span className="aw-switch" aria-hidden="true"><span /></span>
      </button>
      <small className="aw-field-hint">{dryRun ? "Tools and built-ins are not called: each call is listed instead." : "Tools and built-ins really run, and paid ones may spend."}</small>
      <div className="aw-code-actions">
        <button type="button" className="is-primary" disabled={testing || !workflowId} title={workflowId ? "Run the code in the editor once without saving it" : "Save the workflow first"} onClick={() => void runTest()}>
          {testing ? <Icons.Spinner /> : <Icons.Play />} Run unsaved code</button>
      </div>
      {testError ? <p className="aw-field-error" role="alert">{testError}</p> : null}
      {result ? <TestResult result={result} onLine={show} /> : null}
    </details>
  </div>;
}

function TestResult({ result, onLine }: { result: CodeTestResultDto; onLine: (line: number) => void }) {
  const outputs = result.outputs && Object.keys(result.outputs).length ? result.outputs : null;
  return <div className={`aw-code-result is-${result.ok ? "ok" : "error"}`} role="status" aria-label="Test result">
    <p className="aw-code-result-head">{result.ok ? <Icons.Check /> : <Icons.ErrorCircle />} {result.ok ? "Finished" : "Failed"}{typeof result.ms === "number" ? ` in ${result.ms} ms` : ""}</p>
    {result.error ? <button type="button" className="aw-code-error-line" disabled={!result.error.line} onClick={() => result.error?.line && onLine(result.error.line)}>{problemText(result.error)}</button> : null}
    {outputs ? <><strong>Outputs</strong><pre className="aw-pin-out-text">{JSON.stringify(outputs, null, 2)}</pre></> : null}
    {result.tool_calls?.length ? <><strong>Tool calls</strong>
      <ul className="aw-code-calls">{result.tool_calls.map((call, index) => <li key={index}>
        <code>{call.name}</code>{call.dry_run ? <small> dry run, not made</small> : null}
        {call.args !== undefined ? <pre className="aw-pin-out-text">{JSON.stringify(call.args, null, 2)}</pre> : null}
      </li>)}</ul></> : null}
    {result.log ? <><strong>Log</strong><pre className="aw-pin-out-text">{result.log}</pre></> : null}
  </div>;
}
