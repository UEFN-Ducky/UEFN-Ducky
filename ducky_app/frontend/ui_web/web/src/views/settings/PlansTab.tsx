import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AddPlanNodeDialog } from "../../components/AddPlanNodeDialog";
import { AppNotice } from "../../components/AppNotice";
import { MdBlockEditor } from "../../components/md-block-editor";
import { PlanDetailSplit } from "../../components/PlanDetailSplit";
import { PlanTodoCard } from "../../components/PlanTodoCard";
import { MarkdownContent } from "../../components/rich-content/MarkdownContent";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { onApiReady } from "../../hooks/onApiReady";
import { useTimedMessage } from "../../hooks/useTimedMessage";
import { getApi } from "../../hooks/usePanelApi";
import { Icons } from "../../icons/Icons";
import type { ChatPlan, PlanListItem, PlanNode, PlanProgress, PlanTemplateListItem } from "../../types/panel";
import {
  isPlanFinished,
  isPlanPaused,
  isPlanStarted,
  isPlanStructureLocked,
  nodeKind,
} from "../../utils/planLock";
import {
  findNodePath,
  planAtFocus,
  progressForNodes,
  scrollPlanToNode,
} from "../../utils/planOutlineNav";
import { openPlanFromCatalog } from "../../utils/reusePlan";
import {
  AgentCatalogCreateModal,
  CatalogDetailHead,
  CatalogListRow,
  CatalogSlideShell,
  CatalogSourceBadge,
  type CatalogBreadcrumb,
} from "../../components/catalog-slide";
import type { SettingsNavLocation } from "../../navigation/settingsHistory";
import {
  useApplySettingsDrill,
  useRecordSettingsLocation,
  useSettingsHistoryBack,
} from "../../navigation/useSettingsHistory";
import { targetRef } from "../../ui-targets/registry";

export type PlansSectionTab = "templates" | "working";

/** Flatten outline nodes with computed labels (1, 1.1, …). */
export function flattenOutline(
  nodes: PlanNode[] | undefined,
  prefix = "",
): Array<{ label: string; node: PlanNode; depth: number }> {
  const out: Array<{ label: string; node: PlanNode; depth: number }> = [];
  (nodes || []).forEach((node, i) => {
    const label = prefix ? `${prefix}.${i + 1}` : `${i + 1}`;
    const depth = prefix ? prefix.split(".").length : 0;
    out.push({ label, node, depth });
    if (node.children?.length) {
      out.push(...flattenOutline(node.children, label));
    }
  });
  return out;
}

function formatUpdated(ts: number): string {
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function progressLabel(p: PlanListItem["progress"] | undefined, status?: string): string {
  if ((status || "").toLowerCase() === "finished") return "Finished";
  const total = p?.total ?? 0;
  if (!total) return "No steps";
  return `${p?.completed ?? 0}/${total} done`;
}

function rowKey(p: PlanListItem): string {
  return `${p.project_root}::${p.chat_id}`;
}

interface PlansTabProps {
  sectionTab?: PlansSectionTab;
}

export function PlansTab({ sectionTab = "working" }: PlansTabProps) {
  if (sectionTab === "templates") {
    return <TemplatesPanel />;
  }
  return <WorkingPlansPanel />;
}

const DETAIL_SLIDE_MS = 280;

function WorkingPlansPanel() {
  const { confirm } = useConfirmModal();
  const [plans, setPlans] = useState<PlanListItem[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);
  const [fullPlan, setFullPlan] = useState<ChatPlan | null>(null);
  const [planProgress, setPlanProgress] = useState<PlanProgress | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const detailScrollRef = useRef<HTMLDivElement>(null);
  const [statusMsg, setStatusMsg] = useTimedMessage();
  const [addTarget, setAddTarget] = useState<{
    item: PlanListItem;
    parentId: string;
  } | null>(null);
  const [editDraft, setEditDraft] = useState<{
    /** null = root plan; set = focused subplan node id */
    nodeId: string | null;
    title: string;
    overview: string;
    body_markdown: string;
  } | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const pendingPlanBody = useRef("");

  const load = useCallback(async () => {
    const api = getApi();
    if (!api?.list_plans) return;
    setLoading(true);
    try {
      const listed = await api.list_plans();
      setPlans(listed.plans ?? []);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Failed to load plans");
    } finally {
      setLoading(false);
    }
  }, [setStatusMsg]);

  useEffect(() => onApiReady(() => void load()), [load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return plans;
    return plans.filter((p) => {
      const hay = [p.title, p.overview, p.chat_title, p.chat_id].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [plans, query]);

  const selected = useMemo(
    () => (selectedKey ? plans.find((p) => rowKey(p) === selectedKey) ?? null : null),
    [plans, selectedKey],
  );
  const detailOpen = selected !== null;
  const [detailRendered, setDetailRendered] = useState(detailOpen);
  useEffect(() => {
    if (detailOpen) {
      setDetailRendered(true);
      return;
    }
    const timer = window.setTimeout(() => setDetailRendered(false), DETAIL_SLIDE_MS);
    return () => window.clearTimeout(timer);
  }, [detailOpen]);

  const reloadFullPlan = useCallback(
    async (item: PlanListItem) => {
      const api = getApi();
      if (!api?.get_plan) return;
      const res = await api.get_plan(item.chat_id, item.project_root);
      setFullPlan(res.plan ?? null);
      setPlanProgress(res.progress ?? null);
      // Drop focus if the focused node was deleted.
      setFocusNodeId((fid) => {
        if (!fid || !res.plan) return null;
        return findNodePath(res.plan.nodes, fid) ? fid : null;
      });
    },
    [],
  );

  useEffect(() => {
    if (!selected) {
      setFullPlan(null);
      setPlanProgress(null);
      setFocusNodeId(null);
      setHighlightNodeId(null);
      return;
    }
    let cancelled = false;
    setPlanLoading(true);
    void reloadFullPlan(selected)
      .catch((err: unknown) => {
        if (!cancelled) {
          setStatusMsg(err instanceof Error ? err.message : "Failed to load plan");
        }
      })
      .finally(() => {
        if (!cancelled) setPlanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected, reloadFullPlan, setStatusMsg]);

  const focusPath = useMemo(
    () => (fullPlan && focusNodeId ? findNodePath(fullPlan.nodes, focusNodeId) : null),
    [fullPlan, focusNodeId],
  );

  const viewPlan = useMemo(() => {
    if (!fullPlan) return null;
    return planAtFocus(fullPlan, focusNodeId);
  }, [fullPlan, focusNodeId]);

  const viewProgress = useMemo(() => {
    if (!viewPlan) return null;
    if (!focusNodeId) return planProgress;
    return progressForNodes(viewPlan.nodes);
  }, [viewPlan, focusNodeId, planProgress]);

  const openDetail = (item: PlanListItem) => {
    setSelectedKey(rowKey(item));
    setFocusNodeId(null);
    setHighlightNodeId(null);
    setStatusMsg("");
  };

  const closeDetail = () => {
    setSelectedKey(null);
    setFocusNodeId(null);
    setHighlightNodeId(null);
    setEditDraft(null);
    setAddTarget(null);
  };

  const plansNavLoc = useMemo<SettingsNavLocation>(() => {
    if (!selectedKey) {
      return {
        kind: "settings",
        tab: "Plans",
        sectionTab: "working",
        name: "Plans · Project Plans",
      };
    }
    const title = plans.find((p) => rowKey(p) === selectedKey)?.title || selectedKey;
    return {
      kind: "settings",
      tab: "Plans",
      sectionTab: "working",
      drill: { type: "plans", planKey: selectedKey },
      name: title,
    };
  }, [selectedKey, plans]);
  useRecordSettingsLocation(plansNavLoc);

  const applyPlansDrill = useCallback(
    (loc: SettingsNavLocation) => {
      if (loc.sectionTab && loc.sectionTab !== "working") return;
      const planKey = loc.drill?.type === "plans" ? loc.drill.planKey : null;
      if (!planKey) {
        closeDetail();
        return;
      }
      if (!plans.some((p) => rowKey(p) === planKey)) {
        closeDetail();
        return;
      }
      setSelectedKey(planKey);
      setFocusNodeId(null);
      setHighlightNodeId(null);
    },
    [plans],
  );
  useApplySettingsDrill("Plans", applyPlansDrill);

  const historyCloseDetail = useSettingsHistoryBack(closeDetail);

  const handleSelectOutlineNode = (node: PlanNode) => {
    const isSub = nodeKind(node) === "subplan" || (node.children || []).length > 0;
    if (isSub) {
      setFocusNodeId(node.id);
      setHighlightNodeId(null);
      return;
    }
    setHighlightNodeId(node.id);
    requestAnimationFrame(() => {
      scrollPlanToNode(detailScrollRef.current, node.id, node.content);
    });
  };

  const planLocked = isPlanStructureLocked(fullPlan, planProgress);
  const planPaused = isPlanPaused(fullPlan);
  const planFinished = isPlanFinished(fullPlan);
  const showPlayPause = Boolean(fullPlan) && !planFinished && (planPaused || isPlanStarted(fullPlan, planProgress));

  const setPlanPlayback = async (status: "paused" | "open") => {
    if (!selected || !fullPlan) return;
    const api = getApi();
    if (!api?.update_plan) {
      setStatusMsg("Play/Pause unavailable — restart the app");
      return;
    }
    setBusyId(`${rowKey(selected)}::playback`);
    setStatusMsg("");
    try {
      const res = await api.update_plan(
        selected.chat_id,
        undefined,
        "",
        "",
        "",
        true,
        status,
        selected.project_root,
      );
      if (!res.ok) {
        setStatusMsg(res.error || "Failed to update plan");
        return;
      }
      if (res.plan) setFullPlan(res.plan);
      if (res.progress) setPlanProgress(res.progress);
      setEditDraft(null);
      setStatusMsg(status === "paused" ? "Paused — edit unfinished steps or add new ones" : "Playing");
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Failed to update plan");
    } finally {
      setBusyId(null);
    }
  };

  const handleOpenAsTab = async (item: PlanListItem) => {
    setBusyId(rowKey(item));
    setStatusMsg("");
    try {
      await openPlanFromCatalog(item);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Failed to open plan");
    } finally {
      setBusyId(null);
    }
  };

  const handleSaveAsTemplate = async (item: PlanListItem) => {
    const api = getApi();
    if (!api?.save_plan_as_template) {
      setStatusMsg("Save as template unavailable — restart the app");
      return;
    }
    setBusyId(rowKey(item));
    setStatusMsg("");
    try {
      const res = await api.save_plan_as_template(item.chat_id, item.project_root);
      if (!res.ok) {
        setStatusMsg(res.error || "Save as template failed");
        return;
      }
      setStatusMsg(`Saved “${item.title}” as template`);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Save as template failed");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (item: PlanListItem) => {
    if (
      !(await confirm({
        message: `Delete plan “${item.title}”? This removes the plan file only — the chat stays.`,
        confirmLabel: "Delete",
        danger: true,
      }))
    ) {
      return;
    }
    const api = getApi();
    if (!api?.delete_plan) return;
    setBusyId(rowKey(item));
    setStatusMsg("");
    try {
      const res = await api.delete_plan(item.chat_id, item.project_root);
      if (!res.ok) {
        setStatusMsg(res.error || "Delete failed");
        return;
      }
      setPlans((prev) => prev.filter((p) => rowKey(p) !== rowKey(item)));
      if (selectedKey === rowKey(item)) setSelectedKey(null);
      setStatusMsg(`Deleted “${item.title}”`);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setBusyId(null);
    }
  };

  const handleAddNode = async (payload: {
    content: string;
    kind: "step" | "subplan";
    parentId: string;
    body_markdown: string;
  }) => {
    if (!addTarget) return;
    const api = getApi();
    if (!api?.plan_add_node) {
      setStatusMsg("Add node unavailable — restart the app");
      return;
    }
    const { item } = addTarget;
    setBusyId(`${rowKey(item)}::add`);
    try {
      const res = await api.plan_add_node(
        item.chat_id,
        payload.content,
        payload.parentId,
        null,
        item.project_root,
        "",
        payload.kind,
        payload.body_markdown,
      );
      if (!res.ok) {
        setStatusMsg(res.error || "Add failed");
        return;
      }
      setAddTarget(null);
      await load();
      await reloadFullPlan(item);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Add failed");
    } finally {
      setBusyId(null);
    }
  };

  const handleDuplicate = async (item: PlanListItem) => {
    const api = getApi();
    if (!api?.copy_plan) {
      setStatusMsg("Duplicate unavailable — restart the app");
      return;
    }
    setBusyId(`${rowKey(item)}::dup`);
    setStatusMsg("");
    try {
      const destChatId = `settings-plan-${Date.now().toString(36)}`;
      const res = await api.copy_plan(item.chat_id, destChatId, item.project_root, item.project_root);
      if (!res.ok || !res.plan) {
        setStatusMsg(res.error || "Duplicate failed");
        return;
      }
      await load();
      const listed = await api.list_plans();
      const hit = (listed.plans ?? []).find((p) => p.chat_id === destChatId);
      if (hit) openDetail(hit);
      setStatusMsg("Duplicated — you can edit this copy");
      setEditDraft(null);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Duplicate failed");
    } finally {
      setBusyId(null);
    }
  };

  const focusedNode = focusPath?.length ? focusPath[focusPath.length - 1]! : null;
  const editingSubplan = Boolean(editDraft?.nodeId);

  const startPlanEdit = () => {
    if (!fullPlan || planLocked) return;
    setEditDraft({
      nodeId: null,
      title: fullPlan.title || "",
      overview: fullPlan.overview || "",
      body_markdown: fullPlan.body_markdown || "",
    });
  };

  const startSubplanEdit = () => {
    if (!fullPlan || planLocked || !focusedNode || nodeKind(focusedNode) !== "subplan") return;
    setEditDraft({
      nodeId: focusedNode.id,
      title: focusedNode.content || "",
      overview: "",
      body_markdown: focusedNode.body_markdown || "",
    });
  };

  const savePlanEdit = async () => {
    if (!selected || !editDraft || !fullPlan) return;
    const api = getApi();
    setBusyId(`${rowKey(selected)}::edit`);
    try {
      if (editDraft.nodeId) {
        if (!api?.plan_update_node) {
          setStatusMsg("Edit subplan unavailable — restart the app");
          return;
        }
        const res = await api.plan_update_node(
          selected.chat_id,
          editDraft.nodeId,
          editDraft.title.trim() || "Subplan",
          undefined,
          selected.project_root,
          undefined,
          undefined,
          editDraft.body_markdown,
        );
        if (!res.ok) {
          setStatusMsg(res.error || "Save failed");
          return;
        }
        setEditDraft(null);
        await load();
        await reloadFullPlan(selected);
        setStatusMsg("Subplan saved");
        return;
      }
      if (!api?.update_plan) return;
      const res = await api.update_plan(
        selected.chat_id,
        undefined,
        editDraft.title.trim() || "Plan",
        editDraft.overview.trim(),
        editDraft.body_markdown,
        true,
        "",
        selected.project_root,
      );
      if (!res.ok) {
        setStatusMsg(res.error || "Save failed");
        return;
      }
      setEditDraft(null);
      await load();
      await reloadFullPlan(selected);
      setStatusMsg("Plan saved");
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusyId(null);
    }
  };

  useEffect(() => {
    setEditDraft(null);
  }, [focusNodeId, selectedKey]);

  const planLinkOptions = useMemo(() => {
    const nodes = viewPlan?.nodes ?? fullPlan?.nodes;
    return flattenOutline(nodes).map(({ label, node }) => ({
      id: node.id,
      label: `${label} · ${node.content}`.slice(0, 80),
    }));
  }, [viewPlan, fullPlan]);

  const selectedBusy =
    selected != null &&
    (busyId === rowKey(selected) || (busyId || "").startsWith(`${rowKey(selected)}::`));

  const handleDetailBack = () => {
    if (focusPath && focusPath.length > 1) {
      setFocusNodeId(focusPath[focusPath.length - 2]!.id);
      setHighlightNodeId(null);
      return;
    }
    if (focusNodeId) {
      setFocusNodeId(null);
      setHighlightNodeId(null);
      return;
    }
    historyCloseDetail();
  };

  const breadcrumbs: CatalogBreadcrumb[] = selected
    ? [
        { id: "root", label: "Plans", onClick: historyCloseDetail },
        {
          id: "plan",
          label: fullPlan?.title || selected.title,
          current: !focusNodeId,
          onClick: focusNodeId
            ? () => {
                setFocusNodeId(null);
                setHighlightNodeId(null);
              }
            : undefined,
        },
        ...(focusPath || []).map((node, i, arr) => ({
          id: node.id,
          label: node.content,
          current: i === arr.length - 1,
          onClick:
            i === arr.length - 1
              ? undefined
              : () => {
                  setFocusNodeId(node.id);
                  setHighlightNodeId(null);
                },
        })),
      ]
    : [];

  const saveNewPlan = async (title: string, overview: string, body: string) => {
    const api = getApi();
    if (!api?.create_plan) throw new Error("Create plan unavailable — restart the app");
    const chatId = `settings-plan-${Date.now().toString(36)}`;
    const res = await api.create_plan(chatId, title, overview, body, [
      { content: overview || title, status: "pending" },
    ]);
    if (!res.ok) throw new Error(res.error || "Create plan failed");
    const listed = await api.list_plans();
    setPlans(listed.plans ?? []);
    const hit = (listed.plans ?? []).find((p) => p.chat_id === chatId);
    if (hit) openDetail(hit);
    setStatusMsg(`Created “${title}”`);
  };

  return (
    <CatalogSlideShell
      className="plans-tab"
      detailOpen={detailOpen}
      detailRendered={detailRendered}
      detailPlaceholder={<p>Select a plan to view it.</p>}
      listAriaLabel="Project plans"
      notice={statusMsg ? <AppNotice message={statusMsg} className="catalog-slide-notice" /> : null}
      detailScrollRef={detailScrollRef}
      listHeader={
        <div className="catalog-slide-header">
          <div className="catalog-slide-header-titles">
            <h2 className="catalog-slide-title">Project Plans</h2>
          </div>
          <div className="catalog-slide-header-actions">
            <label className="catalog-slide-search">
              <span className="catalog-slide-search-icon" aria-hidden>
                <Icons.Search />
              </span>
              <input
                className="catalog-slide-search-input"
                type="search"
                placeholder="Search plans…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="catalog-slide-action catalog-slide-action--primary"
              onClick={() => setCreateOpen(true)}
              title="New plan"
              aria-label="New plan"
            >
              <Icons.Plus />
            </button>
            <button
              type="button"
              className="catalog-slide-refresh-btn"
              onClick={() => void load()}
              disabled={loading}
              title="Refresh"
              aria-label="Refresh"
            >
              <Icons.Refresh />
            </button>
          </div>
        </div>
      }
      listBody={
        loading ? (
          <div className="catalog-slide-empty">Loading plans…</div>
        ) : filtered.length === 0 ? (
          <div className="catalog-slide-empty">
            {plans.length === 0
              ? "No project plans yet. Create one in Plan mode, or use a template."
              : "No plans match your search."}
          </div>
        ) : (
          <ul className="catalog-slide-list">
            {filtered.map((item) => {
              const key = rowKey(item);
              const busy = busyId === key || (busyId || "").startsWith(`${key}::`);
              return (
                <CatalogListRow
                  key={key}
                  title={item.title}
                  source="custom"
                  selected={selectedKey === key}
                  disabled={busy}
                  icon={<Icons.Plan />}
                  meta={
                    <>
                      {item.chat_title ? <span>{item.chat_title}</span> : null}
                      <span>· {progressLabel(item.progress, item.status)}</span>
                      {item.updated_at ? <span>· {formatUpdated(item.updated_at)}</span> : null}
                    </>
                  }
                  overview={item.overview || undefined}
                  onOpen={() => openDetail(item)}
                  actions={
                    <>
                      <button
                        type="button"
                        className="catalog-slide-action"
                        title="Open as tab"
                        aria-label="Open as tab"
                        disabled={busy}
                        onClick={() => void handleOpenAsTab(item)}
                      >
                        <Icons.Split />
                      </button>
                      <button
                        type="button"
                        className="catalog-slide-action catalog-slide-action--danger"
                        title="Delete plan"
                        aria-label="Delete plan"
                        disabled={busy}
                        onClick={() => void handleDelete(item)}
                      >
                        <Icons.Trash />
                      </button>
                    </>
                  }
                />
              );
            })}
          </ul>
        )
      }
      detailHead={
        selected ? (
          <CatalogDetailHead
            breadcrumbs={breadcrumbs}
            onBack={handleDetailBack}
            backAriaLabel={focusNodeId ? "Back to parent plan" : "Back to plans list"}
          />
        ) : null
      }
      detailBody={
        selected ? (
          planLoading && !fullPlan ? (
            <div className="catalog-slide-empty">Loading plan…</div>
          ) : viewPlan && fullPlan ? (
            <PlanDetailSplit
              steps={
                <PlanTodoCard
                  plan={fullPlan}
                  progress={planProgress}
                  hideTitlebar
                  highlightNodeId={focusNodeId || highlightNodeId}
                  onSelectNode={handleSelectOutlineNode}
                />
              }
              aside={
                focusedNode && nodeKind(focusedNode) === "subplan" ? (
                  <>
                    <div className="catalog-slide-detail-kicker">Subplan</div>
                    <div className="catalog-slide-detail-title-row">
                      {editingSubplan && editDraft ? (
                        <input
                          className="memory-tab-input catalog-slide-detail-title-input"
                          value={editDraft.title}
                          onChange={(e) => setEditDraft({ ...editDraft, title: e.target.value })}
                          placeholder="Subplan title"
                          autoFocus
                        />
                      ) : (
                        <h2 className="catalog-slide-detail-title">{focusedNode.content}</h2>
                      )}
                      <div className="catalog-slide-detail-title-actions">
                        {editingSubplan ? (
                          <>
                            <button
                              type="button"
                              className="catalog-slide-action catalog-slide-action--primary"
                              title="Save"
                              aria-label="Save"
                              disabled={selectedBusy}
                              onClick={() => void savePlanEdit()}
                            >
                              Save
                            </button>
                            <button
                              type="button"
                              className="catalog-slide-action"
                              title="Cancel"
                              aria-label="Cancel"
                              disabled={selectedBusy}
                              onClick={() => setEditDraft(null)}
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <>
                            {!planLocked ? (
                              <>
                                <button
                                  type="button"
                                  className="catalog-slide-action"
                                  title="Edit subplan"
                                  aria-label="Edit subplan"
                                  disabled={selectedBusy}
                                  onClick={startSubplanEdit}
                                >
                                  <Icons.Pencil />
                                </button>
                                <button
                                  type="button"
                                  className="catalog-slide-action"
                                  title="Add step or subplan"
                                  aria-label="Add step or subplan"
                                  disabled={selectedBusy}
                                  onClick={() =>
                                    setAddTarget({
                                      item: selected,
                                      parentId: focusNodeId || "",
                                    })
                                  }
                                >
                                  <Icons.Plus />
                                </button>
                              </>
                            ) : null}
                            <button
                              type="button"
                              className="catalog-slide-action"
                              title="Close subplan"
                              aria-label="Close subplan"
                              onClick={() => {
                                setFocusNodeId(null);
                                setHighlightNodeId(null);
                              }}
                            >
                              <Icons.Close />
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                    {editingSubplan && editDraft ? (
                      <MdBlockEditor
                        value={editDraft.body_markdown}
                        onChange={(body_markdown) => setEditDraft({ ...editDraft, body_markdown })}
                        placeholder="Subplan details…"
                        planLinkOptions={planLinkOptions}
                      />
                    ) : (
                      <div className="plan-node-body plans-tab-detail-md plan-pane-markdown">
                        <span className="plan-node-body-label">Subplan details</span>
                        {focusedNode.body_markdown ? (
                          <MarkdownContent text={focusedNode.body_markdown} />
                        ) : (
                          <p className="plans-tab-modal-desc">
                            {planLocked
                              ? "No details for this subplan."
                              : "No details yet — press Edit to write them."}
                          </p>
                        )}
                      </div>
                    )}
                    <PlanTodoCard
                      plan={viewPlan}
                      progress={viewProgress}
                      hideTitlebar
                      highlightNodeId={highlightNodeId}
                      onSelectNode={handleSelectOutlineNode}
                    />
                  </>
                ) : null
              }
              main={
                <>
                  <div className="catalog-slide-detail-kicker">
                    Plan
                    {planPaused ? " · Paused" : showPlayPause ? " · Playing" : ""}
                  </div>
                  <div className="catalog-slide-detail-title-row">
                    {editDraft && !editingSubplan ? (
                      <input
                        className="memory-tab-input catalog-slide-detail-title-input"
                        value={editDraft.title}
                        onChange={(e) => setEditDraft({ ...editDraft, title: e.target.value })}
                        placeholder="Plan title"
                        autoFocus
                      />
                    ) : (
                      <h2 className="catalog-slide-detail-title">{fullPlan.title}</h2>
                    )}
                    <div className="catalog-slide-detail-title-actions">
                      {editDraft && !editingSubplan ? (
                        <>
                          <button
                            type="button"
                            className="catalog-slide-action catalog-slide-action--primary"
                            title="Save"
                            aria-label="Save"
                            disabled={selectedBusy}
                            onClick={() => void savePlanEdit()}
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            className="catalog-slide-action"
                            title="Cancel"
                            aria-label="Cancel"
                            disabled={selectedBusy}
                            onClick={() => setEditDraft(null)}
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <>
                          {showPlayPause ? (
                            planPaused ? (
                              <button
                                type="button"
                                className="catalog-slide-action catalog-slide-action--primary"
                                title="Play — lock structure while agents work"
                                aria-label="Play plan"
                                disabled={selectedBusy}
                                onClick={() => void setPlanPlayback("open")}
                              >
                                <Icons.Play />
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="catalog-slide-action"
                                title="Pause — edit unfinished steps or add new ones"
                                aria-label="Pause plan"
                                disabled={selectedBusy}
                                onClick={() => void setPlanPlayback("paused")}
                              >
                                <Icons.Pause />
                              </button>
                            )
                          ) : null}
                          {!planLocked ? (
                            <>
                              <button
                                type="button"
                                className="catalog-slide-action"
                                title="Edit plan"
                                aria-label="Edit plan"
                                disabled={selectedBusy || !fullPlan}
                                onClick={startPlanEdit}
                              >
                                <Icons.Pencil />
                              </button>
                              <button
                                type="button"
                                className="catalog-slide-action"
                                title="Add step or subplan"
                                aria-label="Add step or subplan"
                                disabled={selectedBusy}
                                onClick={() =>
                                  setAddTarget({
                                    item: selected,
                                    parentId: "",
                                  })
                                }
                              >
                                <Icons.Plus />
                              </button>
                            </>
                          ) : null}
                          <button
                            type="button"
                            className="catalog-slide-action"
                            title="Duplicate plan"
                            aria-label="Duplicate plan"
                            disabled={selectedBusy}
                            onClick={() => void handleDuplicate(selected)}
                          >
                            <Icons.Copy />
                          </button>
                          <button
                            type="button"
                            className="catalog-slide-action"
                            title="Save as template"
                            aria-label="Save as template"
                            disabled={selectedBusy}
                            onClick={() => void handleSaveAsTemplate(selected)}
                          >
                            <Icons.Plan />
                          </button>
                          <button
                            type="button"
                            className="catalog-slide-action catalog-slide-action--danger"
                            title="Delete plan"
                            aria-label="Delete plan"
                            disabled={selectedBusy}
                            onClick={() => void handleDelete(selected)}
                          >
                            <Icons.Trash />
                          </button>
                          <button
                            type="button"
                            className="catalog-slide-action catalog-slide-action--primary"
                            title="Open as tab"
                            aria-label="Open as tab"
                            disabled={selectedBusy}
                            onClick={() => void handleOpenAsTab(selected)}
                          >
                            <Icons.Split />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  {editDraft && !editingSubplan ? (
                    <textarea
                      className="plans-tab-modal-textarea"
                      rows={2}
                      value={editDraft.overview}
                      onChange={(e) => setEditDraft({ ...editDraft, overview: e.target.value })}
                      placeholder="Overview"
                    />
                  ) : null}
                  {!editDraft || editingSubplan ? (
                    fullPlan.overview ? (
                      <p className="catalog-slide-detail-overview">{fullPlan.overview}</p>
                    ) : null
                  ) : null}
                  {!editDraft || editingSubplan ? (
                    <div className="catalog-slide-detail-meta">
                      <span>
                        {progressLabel(
                          planProgress || selected.progress,
                          fullPlan.status || selected.status,
                        )}
                      </span>
                      {selected.updated_at ? <span>· {formatUpdated(selected.updated_at)}</span> : null}
                      <CatalogSourceBadge source="custom" />
                    </div>
                  ) : null}
                  {editDraft && !editingSubplan ? (
                    <MdBlockEditor
                      value={editDraft.body_markdown}
                      onChange={(body_markdown) => setEditDraft({ ...editDraft, body_markdown })}
                      placeholder="Plan details…"
                      planLinkOptions={planLinkOptions}
                    />
                  ) : fullPlan.body_markdown ? (
                    <div className="plans-tab-detail-md plan-pane-markdown">
                      <MarkdownContent text={fullPlan.body_markdown} />
                    </div>
                  ) : null}
                </>
              }
            />
          ) : (
            <div className="catalog-slide-empty">Could not load this plan.</div>
          )
        ) : null
      }
    >
      <AddPlanNodeDialog
        open={addTarget !== null}
        heading="Add to plan"
        nodes={fullPlan?.nodes}
        initialParentId={addTarget?.parentId || ""}
        busy={addTarget != null && busyId === `${rowKey(addTarget.item)}::add`}
        onClose={() => setAddTarget(null)}
        onSubmit={handleAddNode}
      />
      <AgentCatalogCreateModal
        open={createOpen}
        title="New plan"
        nameLabel="Title"
        namePlaceholder="Plan title"
        descriptionLabel="What should this plan cover?"
        descriptionPlaceholder="Goals, constraints, and the outcome you want"
        busy={createBusy}
        onClose={() => {
          setCreateOpen(false);
          pendingPlanBody.current = "";
        }}
        onGenerate={async ({ description }) => {
          pendingPlanBody.current = `# Plan\n\n${description.trim()}\n`;
          return { checklist: ["Draft outline ready"] };
        }}
        onSave={async ({ name, description, generated }) => {
          setCreateBusy(true);
          try {
            const title = name.trim() || "Untitled plan";
            const overview = description.trim();
            const body = generated
              ? pendingPlanBody.current || overview
              : overview
                ? `# ${title}\n\n${overview}\n`
                : "";
            await saveNewPlan(title, overview, body);
          } finally {
            setCreateBusy(false);
            pendingPlanBody.current = "";
          }
        }}
      />
    </CatalogSlideShell>
  );
}

function TemplatesPanel() {
  const { confirm } = useConfirmModal();
  const [templates, setTemplates] = useState<PlanTemplateListItem[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);
  const [fullPlan, setFullPlan] = useState<ChatPlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const detailScrollRef = useRef<HTMLDivElement>(null);
  const [statusMsg, setStatusMsg] = useTimedMessage();
  const [useItem, setUseItem] = useState<PlanTemplateListItem | null>(null);
  const [addTarget, setAddTarget] = useState<{
    item: PlanTemplateListItem;
    parentId: string;
  } | null>(null);
  const [editDraft, setEditDraft] = useState<{
    nodeId: string | null;
    title: string;
    overview: string;
    body_markdown: string;
  } | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const pendingTemplateBody = useRef("");

  const load = useCallback(async () => {
    const api = getApi();
    if (!api?.list_plan_templates) {
      setTemplates([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const listed = await api.list_plan_templates();
      setTemplates(listed.templates ?? []);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Failed to load templates");
    } finally {
      setLoading(false);
    }
  }, [setStatusMsg]);

  useEffect(() => onApiReady(() => void load()), [load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return templates;
    return templates.filter((t) => {
      const hay = [t.title, t.overview, t.template_id].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [templates, query]);

  const selected = useMemo(
    () => (selectedId ? templates.find((t) => t.template_id === selectedId) ?? null : null),
    [templates, selectedId],
  );
  const detailOpen = selected !== null;
  const [detailRendered, setDetailRendered] = useState(detailOpen);
  useEffect(() => {
    if (detailOpen) {
      setDetailRendered(true);
      return;
    }
    const timer = window.setTimeout(() => setDetailRendered(false), DETAIL_SLIDE_MS);
    return () => window.clearTimeout(timer);
  }, [detailOpen]);

  const reloadFullTemplate = useCallback(async (item: PlanTemplateListItem) => {
    const api = getApi();
    if (!api?.get_plan_template) return;
    const res = await api.get_plan_template(item.template_id);
    setFullPlan(res.template ?? null);
    setFocusNodeId((fid) => {
      if (!fid || !res.template) return null;
      return findNodePath(res.template.nodes, fid) ? fid : null;
    });
  }, []);

  useEffect(() => {
    if (!selected) {
      setFullPlan(null);
      setFocusNodeId(null);
      setHighlightNodeId(null);
      return;
    }
    let cancelled = false;
    setPlanLoading(true);
    void reloadFullTemplate(selected)
      .catch((err: unknown) => {
        if (!cancelled) {
          setStatusMsg(err instanceof Error ? err.message : "Failed to load template");
        }
      })
      .finally(() => {
        if (!cancelled) setPlanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected, reloadFullTemplate, setStatusMsg]);

  const focusPath = useMemo(
    () => (fullPlan && focusNodeId ? findNodePath(fullPlan.nodes, focusNodeId) : null),
    [fullPlan, focusNodeId],
  );

  const viewPlan = useMemo(() => {
    if (!fullPlan) return null;
    return planAtFocus(fullPlan, focusNodeId);
  }, [fullPlan, focusNodeId]);

  const viewProgress = useMemo(() => {
    if (!viewPlan) return null;
    return progressForNodes(viewPlan.nodes);
  }, [viewPlan]);

  const openDetail = (item: PlanTemplateListItem) => {
    setSelectedId(item.template_id);
    setFocusNodeId(null);
    setHighlightNodeId(null);
    setStatusMsg("");
  };

  const closeDetail = () => {
    setSelectedId(null);
    setFocusNodeId(null);
    setHighlightNodeId(null);
    setEditDraft(null);
    setAddTarget(null);
  };

  const templatesNavLoc = useMemo<SettingsNavLocation>(() => {
    if (!selectedId) {
      return {
        kind: "settings",
        tab: "Plans",
        sectionTab: "templates",
        name: "Plans · Templates",
      };
    }
    const title = templates.find((t) => t.template_id === selectedId)?.title || selectedId;
    return {
      kind: "settings",
      tab: "Plans",
      sectionTab: "templates",
      drill: { type: "plans", planKey: selectedId },
      name: title,
    };
  }, [selectedId, templates]);
  useRecordSettingsLocation(templatesNavLoc);

  const applyTemplatesDrill = useCallback(
    (loc: SettingsNavLocation) => {
      if (loc.sectionTab && loc.sectionTab !== "templates") return;
      const planKey = loc.drill?.type === "plans" ? loc.drill.planKey : null;
      if (!planKey) {
        closeDetail();
        return;
      }
      if (!templates.some((t) => t.template_id === planKey)) {
        closeDetail();
        return;
      }
      setSelectedId(planKey);
      setFocusNodeId(null);
      setHighlightNodeId(null);
    },
    [templates],
  );
  useApplySettingsDrill("Plans", applyTemplatesDrill);

  const historyCloseDetail = useSettingsHistoryBack(closeDetail);

  const handleSelectOutlineNode = (node: PlanNode) => {
    const isSub = nodeKind(node) === "subplan" || (node.children || []).length > 0;
    if (isSub) {
      setFocusNodeId(node.id);
      setHighlightNodeId(null);
      return;
    }
    setHighlightNodeId(node.id);
    requestAnimationFrame(() => {
      scrollPlanToNode(detailScrollRef.current, node.id, node.content);
    });
  };

  const handleUse = async () => {
    if (!useItem) return;
    const api = getApi();
    if (!api?.instantiate_plan_template || !api.create_conversation) {
      setStatusMsg("Use template unavailable — restart the app");
      return;
    }
    setBusyId(useItem.template_id);
    setStatusMsg("");
    try {
      const settings = await api.get_settings();
      const root = (settings.uefn_project_root || "").trim();
      if (!root) {
        setStatusMsg("Open a UEFN project first");
        return;
      }
      const conv = await api.create_conversation("", undefined, undefined, {
        title: useItem.title || "Plan",
      });
      const res = await api.instantiate_plan_template(useItem.template_id, conv.id, root);
      if (!res.ok || !res.plan) {
        setStatusMsg(res.error || "Instantiate failed");
        return;
      }
      setUseItem(null);
      await openPlanFromCatalog({
        chat_id: conv.id,
        plan_id: res.plan.id,
        title: res.plan.title,
        progress: res.progress || { total: 0, completed: 0, cancelled: 0, in_progress: 0, pending: 0 },
        updated_at: res.plan.updated_at || 0,
        project_root: root,
        project_name: "",
        nodes: res.plan.nodes,
      });
      setStatusMsg(`Started “${res.plan.title}” from template`);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Use template failed");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (item: PlanTemplateListItem) => {
    if (
      !(await confirm({
        message: `Delete template “${item.title}”? Project plans already created from it are unchanged.`,
        confirmLabel: "Delete",
        danger: true,
      }))
    ) {
      return;
    }
    const api = getApi();
    if (!api?.delete_plan_template) return;
    setBusyId(item.template_id);
    try {
      const res = await api.delete_plan_template(item.template_id);
      if (!res.ok) {
        setStatusMsg(res.error || "Delete failed");
        return;
      }
      setTemplates((prev) => prev.filter((t) => t.template_id !== item.template_id));
      if (selectedId === item.template_id) closeDetail();
      setStatusMsg(`Deleted “${item.title}”`);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setBusyId(null);
    }
  };

  const handleAddNode = async (payload: {
    content: string;
    kind: "step" | "subplan";
    parentId: string;
    body_markdown: string;
  }) => {
    if (!addTarget) return;
    const api = getApi();
    if (!api?.plan_add_node) {
      setStatusMsg("Add node unavailable — restart the app");
      return;
    }
    const { item } = addTarget;
    setBusyId(`${item.template_id}::add`);
    try {
      const res = await api.plan_add_node(
        "",
        payload.content,
        payload.parentId,
        null,
        null,
        item.template_id,
        payload.kind,
        payload.body_markdown,
      );
      if (!res.ok) {
        setStatusMsg(res.error || "Add failed");
        return;
      }
      setAddTarget(null);
      await load();
      await reloadFullTemplate(item);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Add failed");
    } finally {
      setBusyId(null);
    }
  };

  const focusedNode = focusPath?.length ? focusPath[focusPath.length - 1]! : null;
  const editingSubplan = Boolean(editDraft?.nodeId);

  const startTemplateEdit = () => {
    if (!fullPlan) return;
    setEditDraft({
      nodeId: null,
      title: fullPlan.title || "",
      overview: fullPlan.overview || "",
      body_markdown: fullPlan.body_markdown || "",
    });
  };

  const startTemplateSubplanEdit = () => {
    if (!fullPlan || !focusedNode || nodeKind(focusedNode) !== "subplan") return;
    setEditDraft({
      nodeId: focusedNode.id,
      title: focusedNode.content || "",
      overview: "",
      body_markdown: focusedNode.body_markdown || "",
    });
  };

  const saveTemplateEdit = async () => {
    if (!selected || !editDraft || !fullPlan) return;
    const api = getApi();
    setBusyId(`${selected.template_id}::edit`);
    try {
      if (editDraft.nodeId) {
        if (!api?.plan_update_node) {
          setStatusMsg("Edit subplan unavailable — restart the app");
          return;
        }
        const res = await api.plan_update_node(
          "",
          editDraft.nodeId,
          editDraft.title.trim() || "Subplan",
          undefined,
          null,
          selected.template_id,
          undefined,
          editDraft.body_markdown,
        );
        if (!res.ok) {
          setStatusMsg(res.error || "Save failed");
          return;
        }
        setEditDraft(null);
        await load();
        await reloadFullTemplate(selected);
        setStatusMsg("Subplan saved");
        return;
      }
      if (!api?.update_plan_template) return;
      const res = await api.update_plan_template(
        selected.template_id,
        editDraft.title.trim() || "Plan template",
        editDraft.overview.trim(),
        editDraft.body_markdown,
      );
      if (!res.ok) {
        setStatusMsg(res.error || "Save failed");
        return;
      }
      setEditDraft(null);
      await load();
      await reloadFullTemplate(selected);
      setStatusMsg("Template saved");
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusyId(null);
    }
  };

  useEffect(() => {
    setEditDraft(null);
  }, [focusNodeId, selectedId]);

  const planLinkOptions = useMemo(() => {
    const nodes = viewPlan?.nodes ?? fullPlan?.nodes;
    return flattenOutline(nodes).map(({ label, node }) => ({
      id: node.id,
      label: `${label} · ${node.content}`.slice(0, 80),
    }));
  }, [viewPlan, fullPlan]);

  const selectedBusy =
    selected != null &&
    (busyId === selected.template_id || (busyId || "").startsWith(`${selected.template_id}::`));

  const handleDetailBack = () => {
    if (focusPath && focusPath.length > 1) {
      setFocusNodeId(focusPath[focusPath.length - 2]!.id);
      setHighlightNodeId(null);
      return;
    }
    if (focusNodeId) {
      setFocusNodeId(null);
      setHighlightNodeId(null);
      return;
    }
    historyCloseDetail();
  };

  const breadcrumbs: CatalogBreadcrumb[] = selected
    ? [
        { id: "root", label: "Templates", onClick: historyCloseDetail },
        {
          id: "template",
          label: fullPlan?.title || selected.title,
          current: !focusNodeId,
          onClick: focusNodeId
            ? () => {
                setFocusNodeId(null);
                setHighlightNodeId(null);
              }
            : undefined,
        },
        ...(focusPath || []).map((node, i, arr) => ({
          id: node.id,
          label: node.content,
          current: i === arr.length - 1,
          onClick:
            i === arr.length - 1
              ? undefined
              : () => {
                  setFocusNodeId(node.id);
                  setHighlightNodeId(null);
                },
        })),
      ]
    : [];

  const saveNewTemplate = async (title: string, overview: string, body: string) => {
    const api = getApi();
    if (!api?.create_plan_template || !api.list_plan_templates) {
      throw new Error("Create template unavailable — restart the app");
    }
    const res = await api.create_plan_template(title, overview, body, []);
    if (!res.ok) throw new Error(res.error || "Create template failed");
    const listed = await api.list_plan_templates();
    setTemplates(listed.templates ?? []);
    const tid = res.template?.id || "";
    const hit = (listed.templates ?? []).find((t) => t.template_id === tid);
    if (hit) openDetail(hit);
    setStatusMsg(`Created “${title}”`);
  };

  return (
    <CatalogSlideShell
      className="plans-tab"
      detailOpen={detailOpen}
      detailRendered={detailRendered}
      detailPlaceholder={<p>Select a template to view it.</p>}
      listAriaLabel="Plan templates"
      notice={statusMsg ? <AppNotice message={statusMsg} className="catalog-slide-notice" /> : null}
      detailScrollRef={detailScrollRef}
      listHeader={
        <div className="catalog-slide-header">
          <div className="catalog-slide-header-titles">
            <h2 className="catalog-slide-title">Templates</h2>
          </div>
          <div className="catalog-slide-header-actions">
            <label className="catalog-slide-search">
              <span className="catalog-slide-search-icon" aria-hidden>
                <Icons.Search />
              </span>
              <input
                className="catalog-slide-search-input"
                type="search"
                placeholder="Search templates…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="catalog-slide-action catalog-slide-action--primary"
              onClick={() => setCreateOpen(true)}
              title="New template"
              aria-label="New template"
            >
              <Icons.Plus />
            </button>
            <button
              type="button"
              className="catalog-slide-refresh-btn"
              onClick={() => void load()}
              disabled={loading}
              title="Refresh"
              aria-label="Refresh"
            >
              <Icons.Refresh />
            </button>
          </div>
        </div>
      }
      listBody={
        loading ? (
          <div className="catalog-slide-empty">Loading templates…</div>
        ) : filtered.length === 0 ? (
          <div className="catalog-slide-empty">
            {templates.length === 0
              ? "No templates yet. Click +, or save a project plan as a template."
              : "No templates match your search."}
          </div>
        ) : (
          <ul className="catalog-slide-list">
            {filtered.map((item) => {
              const busy =
                busyId === item.template_id || (busyId || "").startsWith(`${item.template_id}::`);
              const stepCount = item.node_count ?? flattenOutline(item.nodes).length;
              return (
                <CatalogListRow
                  key={item.template_id}
                  title={item.title}
                  source={item.template_id === "demo-getting-started" ? "builtin" : "custom"}
                  selected={selectedId === item.template_id}
                  disabled={busy}
                  icon={<Icons.Plan />}
                  liRef={
                    item.template_id === "demo-getting-started"
                      ? targetRef("settings.plans.row.demo-getting-started", {
                          kind: "button",
                          label: item.title,
                          route: "settings.plans",
                        })
                      : undefined
                  }
                  meta={
                    <>
                      <span>{stepCount} steps</span>
                      {item.updated_at ? <span>· {formatUpdated(item.updated_at)}</span> : null}
                    </>
                  }
                  overview={item.overview || undefined}
                  onOpen={() => openDetail(item)}
                  actions={
                    <>
                      <button
                        type="button"
                        className="catalog-slide-action catalog-slide-action--primary"
                        title="Use on current project"
                        aria-label="Use template"
                        disabled={busy}
                        onClick={() => setUseItem(item)}
                      >
                        Use
                      </button>
                      <button
                        type="button"
                        className="catalog-slide-action catalog-slide-action--danger"
                        title="Delete template"
                        aria-label="Delete template"
                        disabled={busy}
                        onClick={() => void handleDelete(item)}
                      >
                        <Icons.Trash />
                      </button>
                    </>
                  }
                />
              );
            })}
          </ul>
        )
      }
      detailHead={
        selected ? (
          <CatalogDetailHead
            breadcrumbs={breadcrumbs}
            onBack={handleDetailBack}
            backAriaLabel={focusNodeId ? "Back to parent template" : "Back to templates list"}
          />
        ) : null
      }
      detailBody={
        selected ? (
          planLoading && !fullPlan ? (
            <div className="catalog-slide-empty">Loading template…</div>
          ) : viewPlan && fullPlan ? (
            <PlanDetailSplit
              steps={
                <PlanTodoCard
                  plan={fullPlan}
                  hideTitlebar
                  hideProgress
                  highlightNodeId={focusNodeId || highlightNodeId}
                  onSelectNode={handleSelectOutlineNode}
                />
              }
              aside={
                focusedNode && nodeKind(focusedNode) === "subplan" ? (
                  <>
                    <div className="catalog-slide-detail-kicker">Subplan</div>
                    <div className="catalog-slide-detail-title-row">
                      {editingSubplan && editDraft ? (
                        <input
                          className="memory-tab-input catalog-slide-detail-title-input"
                          value={editDraft.title}
                          onChange={(e) => setEditDraft({ ...editDraft, title: e.target.value })}
                          placeholder="Subplan title"
                          autoFocus
                        />
                      ) : (
                        <h2 className="catalog-slide-detail-title">{focusedNode.content}</h2>
                      )}
                      <div className="catalog-slide-detail-title-actions">
                        {editingSubplan ? (
                          <>
                            <button
                              type="button"
                              className="catalog-slide-action catalog-slide-action--primary"
                              title="Save"
                              aria-label="Save"
                              disabled={selectedBusy}
                              onClick={() => void saveTemplateEdit()}
                            >
                              Save
                            </button>
                            <button
                              type="button"
                              className="catalog-slide-action"
                              title="Cancel"
                              aria-label="Cancel"
                              disabled={selectedBusy}
                              onClick={() => setEditDraft(null)}
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              type="button"
                              className="catalog-slide-action"
                              title="Edit subplan"
                              aria-label="Edit subplan"
                              disabled={selectedBusy}
                              onClick={startTemplateSubplanEdit}
                            >
                              <Icons.Pencil />
                            </button>
                            <button
                              type="button"
                              className="catalog-slide-action"
                              title="Add step or subplan"
                              aria-label="Add step or subplan"
                              disabled={selectedBusy}
                              onClick={() =>
                                setAddTarget({
                                  item: selected,
                                  parentId: focusNodeId || "",
                                })
                              }
                            >
                              <Icons.Plus />
                            </button>
                            <button
                              type="button"
                              className="catalog-slide-action"
                              title="Close subplan"
                              aria-label="Close subplan"
                              onClick={() => {
                                setFocusNodeId(null);
                                setHighlightNodeId(null);
                              }}
                            >
                              <Icons.Close />
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                    {editingSubplan && editDraft ? (
                      <MdBlockEditor
                        value={editDraft.body_markdown}
                        onChange={(body_markdown) => setEditDraft({ ...editDraft, body_markdown })}
                        placeholder="Subplan details…"
                        planLinkOptions={planLinkOptions}
                      />
                    ) : (
                      <div className="plan-node-body plans-tab-detail-md plan-pane-markdown">
                        <span className="plan-node-body-label">Subplan details</span>
                        {focusedNode.body_markdown ? (
                          <MarkdownContent text={focusedNode.body_markdown} />
                        ) : (
                          <p className="plans-tab-modal-desc">
                            No details yet — press Edit to write them.
                          </p>
                        )}
                      </div>
                    )}
                    <PlanTodoCard
                      plan={viewPlan}
                      progress={viewProgress}
                      hideTitlebar
                      hideProgress
                      highlightNodeId={highlightNodeId}
                      onSelectNode={handleSelectOutlineNode}
                    />
                  </>
                ) : null
              }
              main={
                <>
                  <div className="catalog-slide-detail-kicker">Template</div>
                  <div className="catalog-slide-detail-title-row">
                    {editDraft && !editingSubplan ? (
                      <input
                        className="memory-tab-input catalog-slide-detail-title-input"
                        value={editDraft.title}
                        onChange={(e) => setEditDraft({ ...editDraft, title: e.target.value })}
                        placeholder="Template title"
                        autoFocus
                      />
                    ) : (
                      <h2 className="catalog-slide-detail-title">{fullPlan.title}</h2>
                    )}
                    <div className="catalog-slide-detail-title-actions">
                      {editDraft && !editingSubplan ? (
                        <>
                          <button
                            type="button"
                            className="catalog-slide-action catalog-slide-action--primary"
                            title="Save"
                            aria-label="Save"
                            disabled={selectedBusy}
                            onClick={() => void saveTemplateEdit()}
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            className="catalog-slide-action"
                            title="Cancel"
                            aria-label="Cancel"
                            disabled={selectedBusy}
                            onClick={() => setEditDraft(null)}
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="catalog-slide-action"
                            title="Edit template"
                            aria-label="Edit template"
                            disabled={selectedBusy || !fullPlan}
                            onClick={startTemplateEdit}
                          >
                            <Icons.Pencil />
                          </button>
                          <button
                            type="button"
                            className="catalog-slide-action"
                            title="Add step or subplan"
                            aria-label="Add step or subplan"
                            disabled={selectedBusy}
                            onClick={() =>
                              setAddTarget({
                                item: selected,
                                parentId: "",
                              })
                            }
                          >
                            <Icons.Plus />
                          </button>
                          <button
                            type="button"
                            className="catalog-slide-action catalog-slide-action--danger"
                            title="Delete template"
                            aria-label="Delete template"
                            disabled={selectedBusy}
                            onClick={() => void handleDelete(selected)}
                          >
                            <Icons.Trash />
                          </button>
                          <button
                            type="button"
                            className="catalog-slide-action catalog-slide-action--primary"
                            title="Use on current project"
                            aria-label="Use template"
                            disabled={selectedBusy}
                            onClick={() => setUseItem(selected)}
                          >
                            Use
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  {editDraft && !editingSubplan ? (
                    <textarea
                      className="plans-tab-modal-textarea"
                      rows={2}
                      value={editDraft.overview}
                      onChange={(e) => setEditDraft({ ...editDraft, overview: e.target.value })}
                      placeholder="Overview"
                    />
                  ) : null}
                  {!editDraft || editingSubplan ? (
                    fullPlan.overview ? (
                      <p className="catalog-slide-detail-overview">{fullPlan.overview}</p>
                    ) : null
                  ) : null}
                  {!editDraft || editingSubplan ? (
                    <div className="catalog-slide-detail-meta">
                      <span>
                        {progressForNodes(fullPlan.nodes).total || selected.node_count || 0} steps
                      </span>
                      {selected.updated_at ? <span>· {formatUpdated(selected.updated_at)}</span> : null}
                      <CatalogSourceBadge
                        source={selected.template_id === "demo-getting-started" ? "builtin" : "custom"}
                      />
                    </div>
                  ) : null}
                  {editDraft && !editingSubplan ? (
                    <MdBlockEditor
                      value={editDraft.body_markdown}
                      onChange={(body_markdown) => setEditDraft({ ...editDraft, body_markdown })}
                      placeholder="Template details…"
                      planLinkOptions={planLinkOptions}
                    />
                  ) : fullPlan.body_markdown ? (
                    <div className="plans-tab-detail-md plan-pane-markdown">
                      <MarkdownContent text={fullPlan.body_markdown} />
                    </div>
                  ) : null}
                </>
              }
            />
          ) : (
            <div className="catalog-slide-empty">Could not load this template.</div>
          )
        ) : null
      }
    >
      {useItem ? (
        <div className="plans-tab-modal-backdrop" role="presentation" onClick={() => setUseItem(null)}>
          <div
            className="plans-tab-modal"
            role="dialog"
            aria-labelledby="plans-use-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="plans-use-title">Use template</h3>
            <p className="plans-tab-modal-desc">
              Snapshot “{useItem.title}” into a new chat on the current project. The template stays
              unchanged.
            </p>
            <div className="plans-tab-modal-actions">
              <button type="button" className="plans-tab-action" onClick={() => setUseItem(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="plans-tab-action plans-tab-action--primary"
                disabled={Boolean(busyId)}
                onClick={() => void handleUse()}
              >
                Create project plan
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <AddPlanNodeDialog
        open={addTarget !== null}
        heading="Add to template"
        nodes={fullPlan?.nodes ?? addTarget?.item.nodes}
        initialParentId={addTarget?.parentId || ""}
        busy={addTarget != null && busyId === `${addTarget.item.template_id}::add`}
        onClose={() => setAddTarget(null)}
        onSubmit={handleAddNode}
      />
      <AgentCatalogCreateModal
        open={createOpen}
        title="New template"
        nameLabel="Title"
        namePlaceholder="Template title"
        descriptionLabel="What should this template cover?"
        descriptionPlaceholder="Reusable goals, constraints, and outcome"
        busy={createBusy}
        onClose={() => {
          setCreateOpen(false);
          pendingTemplateBody.current = "";
        }}
        onGenerate={async ({ description }) => {
          pendingTemplateBody.current = `# Plan\n\n${description.trim()}\n`;
          return { checklist: ["Draft outline ready"] };
        }}
        onSave={async ({ name, description, generated }) => {
          setCreateBusy(true);
          try {
            const title = name.trim() || "Untitled template";
            const overview = description.trim();
            const body = generated
              ? pendingTemplateBody.current || overview
              : overview
                ? `# ${title}\n\n${overview}\n`
                : "";
            await saveNewTemplate(title, overview, body);
          } finally {
            setCreateBusy(false);
            pendingTemplateBody.current = "";
          }
        }}
      />
    </CatalogSlideShell>
  );
}

/** @deprecated kept for any leftover imports — always returns flat list of roots. */
export function buildPlanForest(plans: PlanListItem[]) {
  return plans.map((item) => ({
    item,
    depth: 0,
    children: [] as never[],
    parentTitle: "",
    childCount: 0,
  }));
}

/** @deprecated nesting removed */
export function wouldNestCycle(): boolean {
  return false;
}