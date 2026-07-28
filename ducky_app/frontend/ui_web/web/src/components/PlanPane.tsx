import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getApi } from "../hooks/usePanelApi";
import { useAgentEventSubscription } from "../hooks/useAgentEventBus";
import { enqueueComposerDraft } from "../hooks/chatComposerCache";
import { requestOpenPlanTab } from "../navigation/openPlanTab";
import type {
  AgentEvent,
  ChatPlan,
  ChatTab,
  FolderItem,
  PlanNode,
  PlanProgress,
  PlanTodoStatus,
} from "../types/panel";
import { flattenOutline } from "../views/settings/PlansTab";
import {
  findNodePath,
  planAtFocus,
  progressForNodes,
  scrollPlanToNode,
} from "../utils/planOutlineNav";
import { PlanDetailSplit } from "./PlanDetailSplit";
import { PlanTodoCard } from "./PlanTodoCard";
import { MarkdownContent } from "./rich-content/MarkdownContent";
import { MdBlockEditor } from "./md-block-editor";
import { DuckyProfileModal, type DuckyProfileModalMode } from "./ducky/DuckyProfileModal";
import { DuckyAvatar, DUCKY_AVATAR_SIZES } from "./ducky/DuckyAvatars";
import { TruncatedText } from "./TruncatedText";
import { Icons } from "../icons/Icons";
import {
  isNodeDone,
  isPlanFinished,
  isPlanPaused,
  isPlanStarted,
  isPlanStructureLocked,
  nodeKind,
} from "../utils/planLock";

function applyDraftToNodes(
  nodes: PlanNode[] | undefined,
  draftTodos: Array<{ id?: string; content: string; status: PlanTodoStatus }>,
): PlanNode[] {
  const byId = new Map(
    draftTodos.filter((t) => t.id && t.content.trim()).map((t) => [t.id as string, t]),
  );

  const walk = (list: PlanNode[]): PlanNode[] => {
    const out: PlanNode[] = [];
    for (const n of list) {
      // Completed work is frozen — keep the original node even if omitted from the draft.
      if (isNodeDone(n)) {
        out.push({ ...n, children: walk(n.children || []) });
        continue;
      }
      const patch = byId.get(n.id);
      if (!patch) continue;
      out.push({
        ...n,
        content: patch.content.trim(),
        status: patch.status,
        children: walk(n.children || []),
      });
    }
    return out;
  };

  const roots = walk(nodes || []);
  for (const t of draftTodos) {
    if (t.id || !t.content.trim()) continue;
    roots.push({
      id: `n${Math.random().toString(16).slice(2, 10)}`,
      content: t.content.trim(),
      status: t.status || "pending",
      kind: "step",
      body_markdown: "",
      children: [],
    });
  }
  return roots;
}

interface PlanPaneProps {
  chatId: string;
  chatName?: string;
  /** Explicit project root; "" = app-data. Omit = active project. */
  projectRoot?: string;
  onOpenChat?: (chat: ChatTab) => void;
}

interface PlanDraft {
  title: string;
  overview: string;
  body_markdown: string;
  todos: Array<{ id?: string; content: string; status: PlanTodoStatus }>;
}

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

export function PlanPane({
  chatId,
  chatName,
  projectRoot,
  onOpenChat,
}: PlanPaneProps) {
  const [plan, setPlan] = useState<ChatPlan | null>(null);
  const [progress, setProgress] = useState<PlanProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<PlanDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [templateMsg, setTemplateMsg] = useState<string | null>(null);
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const [sendOpen, setSendOpen] = useState(false);
  const [sendPos, setSendPos] = useState<{ top: number; left: number } | null>(null);
  const [chats, setChats] = useState<Array<{ id: string; name: string; duckyStyle: string }>>([]);
  const sendBtnRef = useRef<HTMLButtonElement>(null);
  const sendPanelRef = useRef<HTMLDivElement>(null);

  const [duckyModal, setDuckyModal] = useState<DuckyProfileModalMode | null>(null);

  const planProjectArg = projectRoot !== undefined ? projectRoot : undefined;

  const reloadPlan = useCallback(async () => {
    const api = getApi();
    if (!api?.get_plan) return;
    const res = await api.get_plan(chatId, planProjectArg ?? null);
    setPlan(res.plan ?? null);
    setProgress(res.progress ?? null);
  }, [chatId, planProjectArg]);

  useEffect(() => {
    let cancelled = false;
    setTemplateMsg(null);
    setFocusNodeId(null);
    setHighlightNodeId(null);
    void reloadPlan()
      .then(() => {
        if (!cancelled) setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [reloadPlan]);

  const focusPath = useMemo(
    () => (plan && focusNodeId ? findNodePath(plan.nodes, focusNodeId) : null),
    [plan, focusNodeId],
  );
  const viewPlan = useMemo(
    () => (plan ? planAtFocus(plan, focusNodeId) : null),
    [plan, focusNodeId],
  );
  const viewProgress = useMemo(() => {
    if (!viewPlan) return null;
    if (!focusNodeId) return progress;
    return progressForNodes(viewPlan.nodes);
  }, [viewPlan, focusNodeId, progress]);

  useEffect(() => {
    if (!plan || !focusNodeId) return;
    if (!findNodePath(plan.nodes, focusNodeId)) {
      setFocusNodeId(null);
      setHighlightNodeId(null);
    }
  }, [plan, focusNodeId]);

  const handleSelectOutlineNode = (node: PlanNode) => {
    if (nodeKind(node) === "subplan" || (node.children || []).length > 0) {
      setFocusNodeId(node.id);
      setHighlightNodeId(null);
      return;
    }
    setHighlightNodeId(node.id);
    requestAnimationFrame(() => {
      scrollPlanToNode(scrollRef.current, node.id, node.content);
    });
  };

  useAgentEventSubscription(
    chatId,
    (event: AgentEvent) => {
      if (event.type !== "plan_updated") return;
      if (event.plan) {
        setPlan(event.plan);
        setProgress(event.progress ?? null);
      }
    },
    [],
  );

  const planLocked = isPlanStructureLocked(plan, progress);
  const planPaused = isPlanPaused(plan);
  const planFinished = isPlanFinished(plan);
  const showPlayPause = Boolean(plan) && !planFinished && (planPaused || isPlanStarted(plan, progress));

  const setPlanPlayback = async (status: "paused" | "open") => {
    const api = getApi();
    if (!api?.update_plan || !plan) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.update_plan(chatId, undefined, "", "", "", true, status, planProjectArg ?? null);
      if (!res.ok) {
        setError(res.error || "Failed to update plan");
        return;
      }
      if (res.plan) setPlan(res.plan);
      if (res.progress) setProgress(res.progress);
      setDraft(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const startEdit = () => {
    if (!plan || planLocked) return;
    const outline = flattenOutline(plan.nodes?.length ? plan.nodes : undefined);
    const todos = outline.length
      ? outline.map(({ node }) => ({
          id: node.id,
          content: node.content,
          status: node.status,
        }))
      : (plan.todos || []).map((t) => ({ id: t.id, content: t.content, status: t.status }));
    setDraft({
      title: plan.title || "",
      overview: plan.overview || "",
      body_markdown: plan.body_markdown || "",
      todos,
    });
  };

  const saveEdit = async () => {
    const api = getApi();
    if (!api?.update_plan || !draft || !plan) return;
    setSaving(true);
    try {
      const todos = draft.todos
        .map((t) => ({ ...t, content: t.content.trim() }))
        .filter((t) => t.content);
      const nodes = applyDraftToNodes(plan.nodes, todos);
      const res = await api.update_plan(
        chatId,
        undefined,
        draft.title.trim() || "Plan",
        draft.overview.trim(),
        draft.body_markdown.trim(),
        false,
        "",
        planProjectArg ?? null,
        nodes,
      );
      if (res.ok && res.plan) {
        setPlan(res.plan);
        setProgress(res.progress ?? null);
        setDraft(null);
        setError(null);
      } else {
        setError(res.error || "Failed to save plan");
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const patchTodo = (index: number, content: string) => {
    setDraft((d) =>
      d ? { ...d, todos: d.todos.map((t, i) => (i === index ? { ...t, content } : t)) } : d,
    );
  };

  const handleMakeTemplate = async () => {
    const api = getApi();
    if (!api?.save_plan_as_template) {
      setError("Make template unavailable — restart the app");
      return;
    }
    setBusy(true);
    setError(null);
    setTemplateMsg(null);
    try {
      const root =
        projectRoot !== undefined
          ? projectRoot
          : (await api.get_settings())?.uefn_project_root || "";
      const res = await api.save_plan_as_template(chatId, root);
      if (!res.ok || !res.template) {
        setError(res.error || "Failed to save template");
        return;
      }
      setTemplateMsg(`Saved template “${res.template.title || plan?.title || "Plan"}”`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const refreshChats = useCallback(async () => {
    const api = getApi();
    if (!api?.list_all_conversations) return;
    const listed = await api.list_all_conversations();
    setChats(
      (listed ?? []).map((c) => ({
        id: c.id,
        name: c.title || "Chat",
        duckyStyle: c.ducky_style || "",
      })),
    );
  }, []);

  const openSendMenu = () => {
    setSendOpen((v) => !v);
    void refreshChats();
  };

  useLayoutEffect(() => {
    if (!sendOpen || !sendBtnRef.current) {
      setSendPos(null);
      return;
    }
    const update = () => {
      const trigger = sendBtnRef.current;
      if (!trigger) return;
      setSendPos(computeMenuPosition(trigger));
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [sendOpen]);

  useEffect(() => {
    if (!sendOpen) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (sendBtnRef.current?.contains(target)) return;
      if (sendPanelRef.current?.contains(target)) return;
      setSendOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setSendOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [sendOpen]);

  const copyPlanToChat = async (destChatId: string, destName: string, duckyStyle?: string) => {
    const api = getApi();
    if (!api?.copy_plan) throw new Error("copy_plan unavailable");
    const sourceRoot =
      projectRoot !== undefined ? projectRoot : (await api.get_settings())?.uefn_project_root || "";
    const destRoot = (await api.get_settings())?.uefn_project_root || "";
    const res = await api.copy_plan(chatId, destChatId, sourceRoot, destRoot);
    if (!res.ok || !res.plan) throw new Error(res.error || "Failed to copy plan");
    enqueueComposerDraft(destChatId, "Continue this plan.");
    const chat: ChatTab = {
      id: destChatId,
      name: destName,
      duckyStyle: duckyStyle || "",
    };
    onOpenChat?.(chat);
    requestOpenPlanTab({ chatId: destChatId, title: res.plan.title || destName });
  };

  const sendToExisting = async (target: { id: string; name: string; duckyStyle: string }) => {
    setSendOpen(false);
    setBusy(true);
    setError(null);
    try {
      await copyPlanToChat(target.id, target.name, target.duckyStyle);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const openNewDuckyForPlan = async () => {
    setSendOpen(false);
    const api = getApi();
    if (!api) return;
    try {
      const [folderList, convs] = await Promise.all([
        api.list_folders?.() ?? Promise.resolve([]),
        api.list_conversations?.("") ?? Promise.resolve([]),
      ]);
      const mappedRoot = (convs ?? []).map((c) => ({
        id: c.id,
        name: c.title,
        duckyStyle: c.ducky_style || "",
      }));
      const folderItems: FolderItem[] = (folderList ?? []).map((f) => ({
        id: f.id,
        name: f.name,
        parentId: f.parent_id || "",
        sortOrder: f.sort_order ?? 0,
        chats: [],
        children: [],
        expanded: false,
      }));
      setDuckyModal({
        mode: "create",
        folderId: "",
        folders: folderItems,
        rootChats: mappedRoot,
      });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDuckyCreated = async (chat: ChatTab) => {
    setDuckyModal(null);
    setBusy(true);
    setError(null);
    try {
      await copyPlanToChat(chat.id, chat.name, chat.duckyStyle);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const editing = draft !== null;

  const sendMenu =
    sendOpen && sendPos ? (
      <div
        ref={sendPanelRef}
        className="verse-problems-ducky-dropdown verse-problems-ducky-dropdown--portaled no-drag"
        style={{ top: sendPos.top, left: sendPos.left }}
      >
        <div className="verse-problems-ducky-dropdown-header">
          <span className="verse-problems-ducky-dropdown-title">Send to ducky</span>
        </div>
        {chats.filter((c) => c.id !== chatId).length > 0 ? (
          <ul className="verse-problems-ducky-list">
            {chats
              .filter((c) => c.id !== chatId)
              .map((chat) => (
                <li key={chat.id}>
                  <button
                    type="button"
                    className="verse-problems-ducky-item"
                    onClick={() => void sendToExisting(chat)}
                  >
                    <DuckyAvatar
                      styleId={chat.duckyStyle}
                      size={DUCKY_AVATAR_SIZES.sidebar}
                      className="ducky-avatar--sidebar"
                    />
                    <TruncatedText className="verse-problems-ducky-item-label">{chat.name}</TruncatedText>
                  </button>
                </li>
              ))}
          </ul>
        ) : (
          <div className="verse-problems-ducky-empty">No other duckies yet</div>
        )}
        <div className="verse-problems-ducky-footer">
          <button type="button" className="verse-problems-ducky-create" onClick={() => void openNewDuckyForPlan()}>
            <Icons.Plus />
            <span>New ducky</span>
          </button>
        </div>
      </div>
    ) : null;

  return (
    <div className="plan-pane">
      <div className="plan-pane-header">
        <div className="plan-pane-header-row">
          <div className="plan-pane-kicker">Plan</div>
          <div className="plan-pane-actions">
            {editing ? (
              <>
                <button
                  type="button"
                  className="plan-pane-btn plan-pane-btn--save"
                  onClick={() => void saveEdit()}
                  disabled={saving}
                >
                  {saving ? "Saving…" : "Save"}
                </button>
                <button
                  type="button"
                  className="plan-pane-btn plan-pane-btn--ghost"
                  onClick={() => setDraft(null)}
                  disabled={saving}
                >
                  Cancel
                </button>
              </>
            ) : plan ? (
              <>
                <button
                  type="button"
                  className="plan-pane-btn plan-pane-btn--ghost"
                  onClick={() => void handleMakeTemplate()}
                  disabled={busy}
                  title="Save a reusable template copy (does not change this plan)"
                >
                  Make template
                </button>
                <button
                  ref={sendBtnRef}
                  type="button"
                  className={`plan-pane-btn plan-pane-btn--accent${sendOpen ? " is-active" : ""}`}
                  onClick={openSendMenu}
                  disabled={busy}
                  title="Send plan to a ducky"
                >
                  Send to ducky
                </button>
                {showPlayPause ? (
                  planPaused ? (
                    <button
                      type="button"
                      className="plan-pane-btn plan-pane-btn--primary"
                      onClick={() => void setPlanPlayback("open")}
                      disabled={busy}
                      title="Play — lock structure while agents work"
                    >
                      Play
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="plan-pane-btn plan-pane-btn--ghost"
                      onClick={() => void setPlanPlayback("paused")}
                      disabled={busy}
                      title="Pause — edit unfinished steps or add new ones"
                    >
                      Pause
                    </button>
                  )
                ) : null}
                {!planLocked ? (
                  <button
                    type="button"
                    className="plan-pane-btn plan-pane-btn--ghost"
                    onClick={startEdit}
                  >
                    Edit
                  </button>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
        {editing ? (
          <input
            className="plan-pane-edit-title"
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            placeholder="Plan title"
          />
        ) : (
          <h2 className="plan-pane-title">
            {viewPlan?.title || plan?.title || chatName || "Plan"}
          </h2>
        )}
        {!editing && plan ? (
          <nav className="plan-pane-nest-crumb" aria-label="Plan location">
            <span className="plan-pane-nest-crumb-label">Path</span>
            <button
              type="button"
              className="plan-pane-nest-crumb-link"
              onClick={() => {
                setFocusNodeId(null);
                setHighlightNodeId(null);
              }}
            >
              {plan.title || chatName || "Plan"}
            </button>
            {(focusPath || []).map((node) => (
              <span key={node.id}>
                <span className="plan-pane-nest-crumb-sep" aria-hidden>
                  /
                </span>
                <button
                  type="button"
                  className="plan-pane-nest-crumb-link"
                  onClick={() => {
                    setFocusNodeId(node.id);
                    setHighlightNodeId(null);
                  }}
                >
                  {node.content}
                </button>
              </span>
            ))}
          </nav>
        ) : null}
        {editing ? (
          <textarea
            className="plan-pane-edit-overview"
            value={draft.overview}
            onChange={(e) => setDraft({ ...draft, overview: e.target.value })}
            placeholder="Overview"
            rows={2}
          />
        ) : !focusNodeId && plan?.overview ? (
          <p className="plan-pane-overview">{plan.overview}</p>
        ) : null}
      </div>
      <div ref={scrollRef} className="plan-pane-scroll">
        {error ? <div className="plan-pane-error">{error}</div> : null}
        {templateMsg ? <div className="plan-pane-ok">{templateMsg}</div> : null}
        {!plan && !error ? <div className="plan-pane-empty">No plan for this chat yet.</div> : null}
        {editing ? (
          <PlanDetailSplit
            steps={
              <div className="plan-pane-edit-todos">
                {draft.todos.map((t, i) => {
                  const done = isNodeDone(t);
                  return (
                    <div key={t.id ?? `new-${i}`} className={`plan-pane-edit-todo${done ? " is-done" : ""}`}>
                      <input
                        value={t.content}
                        onChange={(e) => patchTodo(i, e.target.value)}
                        placeholder="Step"
                        disabled={done}
                        title={done ? "Completed steps stay locked" : undefined}
                      />
                      {!done ? (
                        <button
                          type="button"
                          className="plan-pane-edit-todo-remove"
                          onClick={() =>
                            setDraft({ ...draft, todos: draft.todos.filter((_, j) => j !== i) })
                          }
                          title="Remove step"
                        >
                          ×
                        </button>
                      ) : null}
                    </div>
                  );
                })}
                <button
                  type="button"
                  className="plan-pane-btn plan-pane-btn--ghost"
                  onClick={() =>
                    setDraft({ ...draft, todos: [...draft.todos, { content: "", status: "pending" }] })
                  }
                >
                  + Add step
                </button>
              </div>
            }
            main={
              <MdBlockEditor
                value={draft.body_markdown}
                onChange={(body_markdown) => setDraft({ ...draft, body_markdown })}
                placeholder="Plan details (markdown)"
              />
            }
          />
        ) : plan && viewPlan ? (
          <PlanDetailSplit
            steps={
              <PlanTodoCard
                plan={plan}
                progress={progress}
                hideTitlebar
                highlightNodeId={focusNodeId || highlightNodeId}
                onSelectNode={handleSelectOutlineNode}
              />
            }
            aside={
              focusPath?.length && nodeKind(focusPath[focusPath.length - 1]!) === "subplan" ? (
                <>
                  <div className="plan-node-body">
                    <span className="plan-node-body-label">Subplan details</span>
                    <h3 className="plan-pane-title">{focusPath[focusPath.length - 1]!.content}</h3>
                    {focusPath[focusPath.length - 1]!.body_markdown ? (
                      <MarkdownContent text={focusPath[focusPath.length - 1]!.body_markdown || ""} />
                    ) : (
                      <p className="plans-tab-modal-desc">No details for this subplan yet.</p>
                    )}
                  </div>
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
              plan.body_markdown ? (
                <div className="plan-pane-markdown">
                  <MarkdownContent text={plan.body_markdown} />
                </div>
              ) : null
            }
          />
        ) : null}
      </div>

      {sendMenu ? createPortal(sendMenu, document.body) : null}

      <DuckyProfileModal
        open={duckyModal !== null}
        state={duckyModal}
        onClose={() => setDuckyModal(null)}
        onCreated={(chat) => void handleDuckyCreated(chat)}
      />
    </div>
  );
}
