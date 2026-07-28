import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Icons } from "../../icons/Icons";
import { runBridgeJob } from "../../hooks/bridgeJobAsync";
import { getApi } from "../../hooks/usePanelApi";
import { requestOpenSettings } from "../../navigation/openSettingsTab";
import type {
  TesterDeviceNode,
  TesterDevicesDto,
  TesterListTestsDto,
  TesterResultsDto,
  TesterSimulateDto,
} from "../../types/panel";

type DeviceRow = TesterDeviceNode & { source: "live" | "workspace"; unwired?: boolean };

function groupDevices(rows: DeviceRow[]): Record<string, DeviceRow[]> {
  const groups: Record<string, DeviceRow[]> = {};
  for (const row of rows) {
    const key = row.kind || row.class || "other";
    (groups[key] ??= []).push(row);
  }
  return groups;
}

export function useTesterDockPanel(enabled: boolean) {
  const [devices, setDevices] = useState<TesterDevicesDto | null>(null);
  const [tests, setTests] = useState<TesterListTestsDto | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [simulating, setSimulating] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sim, setSim] = useState<TesterSimulateDto | null>(null);
  const [results, setResults] = useState<TesterResultsDto | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [createNote, setCreateNote] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    setError(null);
    try {
      const [dev, tst] = await Promise.all([
        runBridgeJob<TesterDevicesDto>("tester_list_devices", [], 30_000),
        getApi()?.tester_list_tests?.() ?? Promise.resolve({ ok: true, tests: [] }),
      ]);
      setDevices(dev);
      setTests(tst);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    if (enabled) void refresh();
  }, [enabled, refresh]);

  const rows = useMemo(() => {
    const out: DeviceRow[] = [];
    const unwiredSet = new Set(
      (devices?.audit?.unwired || []).map((u) => String(u.device || "")),
    );
    for (const n of devices?.live?.nodes || []) {
      out.push({
        ...n,
        source: "live",
        unwired: unwiredSet.has(String(n.id)) || unwiredSet.has(String(n.label)),
      });
    }
    const liveLabels = new Set(out.map((r) => (r.label || "").toLowerCase()));
    for (const n of devices?.workspace?.nodes || []) {
      if (liveLabels.has((n.label || "").toLowerCase())) continue;
      out.push({ ...n, source: "workspace" });
    }
    return out;
  }, [devices]);

  const groups = useMemo(() => groupDevices(rows), [rows]);

  const selected = rows.find((r) => r.id === selectedId) || null;

  const snapshotJson = useCallback(() => {
    // Prefer cached panel data — never re-probe the listener on every Sim click.
    return JSON.stringify({
      listener_online: !!devices?.listener_online,
      live: devices?.live || null,
      workspace: devices?.workspace || { nodes: [], edges: [] },
    });
  }, [devices]);

  const onSimulate = useCallback(
    async (device: DeviceRow) => {
      setSelectedId(device.id);
      setSim(null);
      setSimulating(true);
      try {
        const result = await runBridgeJob<TesterSimulateDto>(
          "tester_simulate",
          [device.label || device.id, "InteractedWithEvent", snapshotJson()],
          15_000,
        );
        setSim(result);
      } catch (err) {
        setSim({ ok: false, error: err instanceof Error ? err.message : String(err) });
      } finally {
        setSimulating(false);
      }
    },
    [snapshotJson],
  );

  const onRunAll = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      await getApi()?.tester_scaffold?.(false);
      await runBridgeJob("tester_run_tests", [false], 180_000);
      const res = await runBridgeJob<TesterResultsDto>("tester_results", [500, 0], 60_000);
      setResults(res);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }, [refresh]);

  const onCreateTest = useCallback(async () => {
    const label = selected?.label || "";
    setCreateNote(null);
    setCreating(true);
    try {
      const created = await runBridgeJob<{
        ok: boolean;
        conversation?: { id: string; title: string; ducky_style?: string };
        prompt?: string;
        error?: string;
      }>("tester_create_chat", [label], 60_000);
      if (created.ok && created.conversation?.id) {
        window.dispatchEvent(
          new CustomEvent("ducky:tester-open-chat", {
            detail: {
              id: created.conversation.id,
              name: created.conversation.title || "Tester",
              duckyStyle: created.conversation.ducky_style,
              draft: created.prompt || "",
            },
          }),
        );
        setCreateNote(`Opened Tester chat “${created.conversation.title || "Tester"}”`);
      } else {
        setCreateNote(created.error || "Could not create Tester chat");
      }
    } catch (err) {
      setCreateNote(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }, [selected]);

  const busy = running || simulating;
  const busyTitle = running ? "Tests running…" : simulating ? "Simulating…" : "Working…";

  const actions = useMemo(
    () => (
      <>
        <button
          type="button"
          className="icon-btn"
          title="Refresh devices & tests"
          onClick={() => void refresh()}
          disabled={loading}
        >
          <Icons.Refresh />
        </button>
        <button
          type="button"
          className="icon-btn"
          title="Run all tests (compile + collect results)"
          onClick={() => void onRunAll()}
          disabled={running}
        >
          <Icons.Sparkles />
        </button>
      </>
    ),
    [loading, onRunAll, refresh, running],
  );

  const auditSummary = devices?.audit?.summary;
  const online = !!devices?.listener_online;
  const liveCount = (devices?.live?.nodes || []).length;
  const openListenerHelp = useCallback(() => {
    requestOpenSettings("General");
    queueMicrotask(() =>
      window.dispatchEvent(
        new CustomEvent("ducky:settings-section", {
          detail: { tab: "General", section: "general" },
        }),
      ),
    );
  }, []);

  const children = (
    <div className="tester-dock">
      <div className="tester-dock-status">
        <span className={`tester-dock-pill ${online ? "is-online" : "is-offline"}`}>
          {online ? "Listener online" : "Listener offline"}
        </span>
        {auditSummary ? (
          <span className="tester-dock-pill muted">
            {auditSummary.errors ?? 0} err · {auditSummary.warnings ?? 0} warn
          </span>
        ) : null}
        {loading ? <span className="tester-dock-muted">Loading…</span> : null}
        {running ? <span className="tester-dock-muted">Running…</span> : null}
        {simulating ? <span className="tester-dock-muted">Simulating…</span> : null}
        {creating ? <span className="tester-dock-muted">Opening Tester…</span> : null}
      </div>

      {!online ? (
        <div className="tester-dock-callout" role="status">
          <p>
            Sim needs UEFN connected. Open your project in UEFN, then connect the listener —
            until then Sim only stubs source classes (no wiring chain).
          </p>
          <button type="button" className="tester-dock-link" onClick={openListenerHelp}>
            How to connect
          </button>
        </div>
      ) : liveCount === 0 ? (
        <div className="tester-dock-callout" role="status">
          <p>
            Listener is online, but no devices are placed in the level. Place and wire devices in
            UEFN, then refresh — Sim walks level wiring, not just Verse class names.
          </p>
        </div>
      ) : null}

      {error ? <p className="tester-dock-error">{error}</p> : null}
      {createNote ? <p className="tester-dock-note">{createNote}</p> : null}

      <section className="tester-dock-section">
        <div className="tester-dock-section-head">
          <strong>Devices</strong>
          <span className="tester-dock-section-head-trail">
            {busy ? (
              <span
                className="sidebar-agent-spinner sidebar-section-header-busy"
                title={busyTitle}
                aria-label={busyTitle}
              />
            ) : null}
            <span className="tester-dock-muted">{rows.length}</span>
          </span>
        </div>
        {rows.length === 0 ? (
          <p className="verse-outline-empty">
            {online ? "No devices found in the level" : "No Verse creative_device classes found — open a project"}
          </p>
        ) : (
          <div className="tester-dock-tree">
            {Object.entries(groups).map(([group, items]) => {
              const open = expandedGroups[group] !== false;
              const groupBusy =
                simulating && !!selectedId && items.some((d) => d.id === selectedId);
              return (
                <div key={group} className="tester-dock-group">
                  <button
                    type="button"
                    className={`tester-dock-group-btn${groupBusy ? " is-busy" : ""}`}
                    onClick={() =>
                      setExpandedGroups((prev) => ({ ...prev, [group]: !open }))
                    }
                  >
                    <span className={`codicon ${open ? "codicon-chevron-down" : "codicon-chevron-right"}`} />
                    <span data-no-translate>{group}</span>
                    {groupBusy ? (
                      <span
                        className="sidebar-agent-spinner sidebar-section-header-busy"
                        title="Simulating…"
                        aria-label="Simulating"
                      />
                    ) : null}
                    <span className="tester-dock-muted">{items.length}</span>
                  </button>
                  {open
                    ? items.map((device) => (
                        <div
                          key={device.id}
                          className={`tester-dock-row${selectedId === device.id ? " is-selected" : ""}`}
                        >
                          <button
                            type="button"
                            className="tester-dock-row-main"
                            onClick={() => setSelectedId(device.id)}
                            title={device.path || device.verse_source || device.id}
                          >
                            <span className="tester-dock-name" data-no-translate>
                              {device.label}
                            </span>
                            {device.unwired ? (
                              <span className="tester-dock-badge is-warn">unwired</span>
                            ) : null}
                            {device.source === "workspace" ? (
                              <span className="tester-dock-badge">source</span>
                            ) : null}
                          </button>
                          <button
                            type="button"
                            className="tester-dock-sim-btn"
                            title={
                              !online || device.source === "workspace"
                                ? "Simulate InteractedWithEvent (needs listener + placed device for wiring)"
                                : "Simulate InteractedWithEvent"
                            }
                            onClick={() => void onSimulate(device)}
                            disabled={simulating}
                          >
                            Sim
                          </button>
                        </div>
                      ))
                    : null}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {sim ? (
        <section className="tester-dock-section">
          <div className="tester-dock-section-head">
            <strong>Simulation</strong>
            <span className="tester-dock-muted">{sim.device || ""}</span>
          </div>
          {!sim.ok ? (
            <p className="tester-dock-error">{sim.error || "Simulation failed"}</p>
          ) : (
            <>
              {sim.note ? (
                <div className="tester-dock-callout is-sim-note" role="status">
                  <p>{sim.note}</p>
                  {!online ? (
                    <button type="button" className="tester-dock-link" onClick={openListenerHelp}>
                      How to connect
                    </button>
                  ) : null}
                </div>
              ) : null}
              <ol className="tester-dock-trace">
                {(sim.trace || []).map((step, i) => (
                  <li key={i} className={step.skipped ? "is-skip" : ""}>
                    <span data-no-translate>{step.device}</span>
                    <span className="tester-dock-muted"> ← {step.incoming}</span>
                    {(step.effects || []).length > 0 ? (
                      <span className="tester-dock-badge is-ok">
                        {(step.effects || []).map((e) => e.kind).join(", ")}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ol>
            </>
          )}
        </section>
      ) : null}

      <section className="tester-dock-section">
        <div className="tester-dock-section-head">
          <strong>Tests</strong>
          <span className="tester-dock-section-head-trail">
            {running ? (
              <span
                className="sidebar-agent-spinner sidebar-section-header-busy"
                title="Tests running…"
                aria-label="Tests running"
              />
            ) : null}
            <button
              type="button"
              className="tester-dock-link"
              onClick={() => void onCreateTest()}
              disabled={creating}
            >
              {creating ? "Opening…" : "Create test"}
            </button>
          </span>
        </div>
        {(tests?.tests || []).length === 0 ? (
          <p className="verse-outline-empty">
            No harness cases yet — click Create test or Run (sparkle) to scaffold
          </p>
        ) : (
          <ul className="tester-dock-tests">
            {(tests?.tests || []).map((t) => (
              <li key={t.id}>
                <span className="tester-dock-name" data-no-translate>
                  {t.name}
                </span>
                <span className="tester-dock-badge">{t.kind === "simulation" ? "sim" : "verse"}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {results ? (
        <section className="tester-dock-section">
          <div className="tester-dock-section-head">
            <strong>Results</strong>
            <span className={`tester-dock-pill ${results.ok ? "is-online" : "is-fail"}`}>
              {results.passed ?? 0} pass · {results.failed ?? 0} fail
            </span>
          </div>
          <ul className="tester-dock-results">
            {(results.results || [])
              .filter((r) => r.name !== "summary")
              .map((r, i) => (
                <li key={`${r.name}-${i}`} className={`is-${(r.status || "").toLowerCase()}`}>
                  <span className="tester-dock-badge">{r.status}</span>
                  <span data-no-translate>{r.name}</span>
                  {r.detail ? <span className="tester-dock-muted"> — {r.detail}</span> : null}
                </li>
              ))}
          </ul>
        </section>
      ) : null}
    </div>
  );

  return { actions: actions as ReactNode, children, busy, busyTitle };
}
