import { useCallback, useEffect, useMemo, useState } from "react";
import { AppNotice } from "../../components/AppNotice";
import { ChoiceDropdown } from "../../components/ChoiceDropdown";
import { DuckyModelPicker } from "../../components/ducky/DuckyModelPicker";
import { MarkdownContent } from "../../components/rich-content/MarkdownContent";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { onApiReady } from "../../hooks/onApiReady";
import { useTimedMessage } from "../../hooks/useTimedMessage";
import { getApi } from "../../hooks/usePanelApi";
import { Icons } from "../../icons/Icons";
import type { ChatContextMemoryDto, MemorySettingsDto, RecentProject } from "../../types/panel";
import { MemoryEntriesCatalog } from "./MemoryEntriesCatalog";
import { SettingsToggleRow } from "./SettingsToggleRow";

function projectLabel(root: string): string {
  return root.replace(/\\/g, "/").split("/").filter(Boolean).pop() || root || "No project";
}

export type MemorySectionTab = "entries" | "context";

const DEFAULT_MEM_SETTINGS: MemorySettingsDto = {
  memory_auto_compress: true,
  prompt_dedupe_exact_blocks: false,
  memory_keep_last_messages: 20,
  memory_compress_messages: 40,
  memory_compress_tokens: 80_000,
  memory_index_max_chars: 2_500,
  memory_summary_model: "",
  index_est_tokens: 0,
};

interface MemoryTabProps {
  sectionTab?: MemorySectionTab;
}

export function MemoryTab({ sectionTab = "entries" }: MemoryTabProps) {
  const { confirm } = useConfirmModal();
  const [projects, setProjects] = useState<RecentProject[]>([]);
  const [activeRoot, setActiveRoot] = useState("");
  const [viewRoot, setViewRoot] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [entriesReload, setEntriesReload] = useState(0);
  const [memSettings, setMemSettings] = useState<MemorySettingsDto>(DEFAULT_MEM_SETTINGS);
  const [chatOptions, setChatOptions] = useState<Array<{ id: string; label: string }>>([]);
  const [chatConvId, setChatConvId] = useState("");
  const [chatCtx, setChatCtx] = useState<ChatContextMemoryDto | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useTimedMessage();

  const effectiveRoot = viewRoot !== null ? viewRoot : activeRoot;

  const load = useCallback(async () => {
    const api = getApi();
    if (!api?.get_settings) return;
    setLoading(true);
    try {
      const [settings, recent, mem] = await Promise.all([
        api.get_settings(),
        api.list_recent_projects?.() ?? Promise.resolve([] as RecentProject[]),
        api.get_memory_settings?.() ?? Promise.resolve(DEFAULT_MEM_SETTINGS),
      ]);
      const active = (settings.uefn_project_root || "").trim();
      setActiveRoot(active);
      setProjects(recent ?? []);
      if (mem) setMemSettings({ ...DEFAULT_MEM_SETTINGS, ...mem });
      const chats = (await api.list_all_conversations?.()) ?? [];
      const aiChats = chats.filter((c) => !c.is_group);
      const nextOptions = aiChats
        .map((c) => ({
          id: String(c.id || ""),
          label: String(c.ducky_name || c.title || c.id || "Chat"),
        }))
        .filter((c) => c.id);
      setChatOptions(nextOptions);
      setChatConvId((prev) => (prev && nextOptions.some((o) => o.id === prev) ? prev : ""));
      if (sectionTab === "entries") setEntriesReload((n) => n + 1);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Failed to load memory");
    } finally {
      setLoading(false);
    }
  }, [sectionTab, setStatusMsg]);

  useEffect(() => onApiReady(() => void load()), [load]);

  const loadChatCtx = useCallback(
    async (convId: string) => {
      const api = getApi();
      if (!api?.get_chat_context_memory || !convId) {
        setChatCtx(null);
        return;
      }
      try {
        const res = await api.get_chat_context_memory(convId, effectiveRoot);
        setChatCtx(res.ok === false ? { ...res, ok: false } : res);
      } catch (err) {
        setChatCtx({ ok: false, error: err instanceof Error ? err.message : "Failed to load chat context" });
      }
    },
    [effectiveRoot],
  );

  useEffect(() => {
    if (chatConvId) void loadChatCtx(chatConvId);
    else setChatCtx(null);
  }, [chatConvId, loadChatCtx]);

  const patchMemSettings = async (patch: Partial<MemorySettingsDto>) => {
    const api = getApi();
    if (!api?.set_memory_settings) return;
    const next = { ...memSettings, ...patch };
    setMemSettings(next);
    try {
      const saved = await api.set_memory_settings(patch);
      setMemSettings({ ...DEFAULT_MEM_SETTINGS, ...saved });
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Failed to save memory settings");
      void load();
    }
  };

  const compressNow = async () => {
    const api = getApi();
    if (!api?.compress_chat_context || !chatConvId) return;
    setChatBusy(true);
    try {
      const res = await api.compress_chat_context(chatConvId, effectiveRoot, true);
      if (res.ok === false) {
        setStatusMsg(res.error || "Compress failed");
      } else if (res.compressed) {
        setStatusMsg(`Compressed (${res.method || "ok"}) — ~${res.context_summary_tokens ?? 0} summary tokens`);
      } else {
        setStatusMsg(res.reason === "under_threshold" ? "Nothing to compress yet." : "Compress skipped.");
      }
      await loadChatCtx(chatConvId);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Compress failed");
    } finally {
      setChatBusy(false);
    }
  };

  const clearSummary = async () => {
    const api = getApi();
    if (!api?.clear_chat_context_summary || !chatConvId) return;
    if (
      !(await confirm({
        message: "Clear the rolling context summary? Chat messages stay intact.",
        confirmLabel: "Clear summary",
      }))
    ) {
      return;
    }
    setChatBusy(true);
    try {
      const res = await api.clear_chat_context_summary(chatConvId, effectiveRoot);
      if (res.ok === false) setStatusMsg(res.error || "Clear failed");
      else setStatusMsg("Summary cleared — full history still on disk.");
      await loadChatCtx(chatConvId);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Clear failed");
    } finally {
      setChatBusy(false);
    }
  };

  const projectOptions = useMemo(() => {
    const opts: { root: string; label: string }[] = [];
    const seen = new Set<string>();
    const add = (root: string, label: string) => {
      const key = (root || "").trim().toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      opts.push({ root, label });
    };
    if (activeRoot) add(activeRoot, `${projectLabel(activeRoot)} (current)`);
    for (const p of projects) {
      const root = (p.path || "").trim();
      if (root) add(root, p.name || projectLabel(root));
    }
    return opts;
  }, [activeRoot, projects]);

  const viewingOtherProject = effectiveRoot.trim().toLowerCase() !== activeRoot.trim().toLowerCase();

  const projectPicker = (
    <ChoiceDropdown
      className="memory-tab-project-select"
      aria-label="Which project's memory to view"
      mode="radio"
      value={effectiveRoot}
      options={
        projectOptions.length === 0
          ? [{ value: "", label: "Current / app data" }]
          : projectOptions.map((o) => ({ value: o.root, label: o.label }))
      }
      onChange={(next) => {
        setViewRoot(next === activeRoot ? null : next);
      }}
    />
  );

  if (sectionTab === "entries") {
    return (
      <MemoryEntriesCatalog
        effectiveRoot={effectiveRoot}
        viewingOtherProject={viewingOtherProject}
        reloadToken={entriesReload}
        headerActions={projectPicker}
      />
    );
  }

  return (
    <div className="plans-tab memory-tab">
      {statusMsg ? <AppNotice message={statusMsg} className="plans-tab-notice" /> : null}
      <div className="plans-tab-header">
        <div className="plans-tab-header-titles">
          <h2 className="plans-tab-title">Context</h2>
        </div>
        <div className="plans-tab-header-actions">
          {projectPicker}
          <button
            type="button"
            className="plans-tab-refresh-btn plans-tab-refresh-btn--icon"
            onClick={() => void load()}
            disabled={loading}
            title="Refresh"
            aria-label="Refresh"
          >
            <Icons.Refresh />
          </button>
        </div>
      </div>

      <section className="memory-tab-settings" aria-label="Context memory settings">
            <div className="general-tab-toggle-card memory-tab-toggle-card">
              <SettingsToggleRow
                id="memory-auto-compress"
                label="Auto-compress chat context"
                checked={!!memSettings.memory_auto_compress}
                onChange={(checked) => void patchMemSettings({ memory_auto_compress: checked })}
              />
            </div>
            <div className="general-tab-toggle-card memory-tab-toggle-card">
              <SettingsToggleRow
                id="prompt-dedupe-exact-blocks"
                label="Strip exact duplicate paste blocks"
                description="Before send/resend, drop exact duplicate multi-line paste blocks (80+ chars). Short repeats like 'ok' stay. Not fuzzy / word-level."
                checked={!!memSettings.prompt_dedupe_exact_blocks}
                onChange={(checked) =>
                  void patchMemSettings({ prompt_dedupe_exact_blocks: checked })
                }
              />
            </div>
            <div className="memory-tab-settings-grid">
              <label className="memory-tab-field">
                <span className="memory-tab-field-label">Keep last messages</span>
                <input
                  className="memory-tab-input"
                  type="number"
                  min={1}
                  max={100}
                  value={memSettings.memory_keep_last_messages}
                  onChange={(e) =>
                    setMemSettings((prev) => ({
                      ...prev,
                      memory_keep_last_messages: Number(e.target.value) || 20,
                    }))
                  }
                  onBlur={() =>
                    void patchMemSettings({
                      memory_keep_last_messages: memSettings.memory_keep_last_messages,
                    })
                  }
                />
              </label>
              <label className="memory-tab-field">
                <span className="memory-tab-field-label">Compress at messages</span>
                <input
                  className="memory-tab-input"
                  type="number"
                  min={2}
                  value={memSettings.memory_compress_messages}
                  onChange={(e) =>
                    setMemSettings((prev) => ({
                      ...prev,
                      memory_compress_messages: Number(e.target.value) || 40,
                    }))
                  }
                  onBlur={() =>
                    void patchMemSettings({
                      memory_compress_messages: memSettings.memory_compress_messages,
                    })
                  }
                />
              </label>
              <label className="memory-tab-field">
                <span className="memory-tab-field-label">Compress at tokens</span>
                <input
                  className="memory-tab-input"
                  type="number"
                  min={1000}
                  step={1000}
                  value={memSettings.memory_compress_tokens}
                  onChange={(e) =>
                    setMemSettings((prev) => ({
                      ...prev,
                      memory_compress_tokens: Number(e.target.value) || 80_000,
                    }))
                  }
                  onBlur={() =>
                    void patchMemSettings({
                      memory_compress_tokens: memSettings.memory_compress_tokens,
                    })
                  }
                />
              </label>
              <label className="memory-tab-field">
                <span className="memory-tab-field-label">Index max chars</span>
                <input
                  className="memory-tab-input"
                  type="number"
                  min={200}
                  max={20000}
                  value={memSettings.memory_index_max_chars}
                  onChange={(e) =>
                    setMemSettings((prev) => ({
                      ...prev,
                      memory_index_max_chars: Number(e.target.value) || 2500,
                    }))
                  }
                  onBlur={() =>
                    void patchMemSettings({
                      memory_index_max_chars: memSettings.memory_index_max_chars,
                    })
                  }
                />
              </label>
            </div>
            <div className="memory-tab-model-row">
              <DuckyModelPicker
                model={memSettings.memory_summary_model || ""}
                onChange={(next) => void patchMemSettings({ memory_summary_model: next })}
                label="Summary model"
                placeholder="Voice / Default model"
                hint="Used when compressing older turns. Leave empty to use Voice summary model, then Default Model."
                allowClear
                requireTools={false}
              />
            </div>
            <p className="memory-tab-index-hint">
              Index ≈ {memSettings.index_est_tokens ?? 0} tokens
              {typeof memSettings.index_chars === "number" ? ` · ${memSettings.index_chars} chars` : ""}.
            </p>
          </section>

          <section className="memory-tab-chat-ctx" aria-label="Chat context summary">
            <div className="memory-tab-chat-ctx-head">
              <div className="memory-tab-chat-ctx-titles">
                <h3 className="memory-tab-section-title">Chat context</h3>
                <p className="memory-tab-field-hint">AI duckies only — pick a chat to read its rolling summary.</p>
              </div>
              <div className="memory-tab-chat-ctx-actions">
                <ChoiceDropdown
                  className="memory-tab-chat-select"
                  aria-label="AI chat to inspect"
                  mode="radio"
                  value={chatConvId}
                  options={[
                    { value: "", label: "Select a chat…" },
                    ...chatOptions.map((c) => ({ value: c.id, label: c.label })),
                  ]}
                  onChange={(id) => setChatConvId(id)}
                />
                <button
                  type="button"
                  className="plans-tab-action plans-tab-action--primary"
                  disabled={!chatConvId || chatBusy}
                  onClick={() => void compressNow()}
                >
                  Compress now
                </button>
                <button
                  type="button"
                  className="plans-tab-action"
                  disabled={!chatConvId || chatBusy || !chatCtx?.context_summary}
                  onClick={() => void clearSummary()}
                >
                  Clear summary
                </button>
              </div>
            </div>
            {!chatConvId ? (
              <div className="memory-tab-chat-empty">Select an AI chat to preview its rewritten summary.</div>
            ) : chatCtx?.ok === false ? (
              <div className="memory-tab-chat-empty">{chatCtx.error || "Failed to load chat context."}</div>
            ) : chatCtx ? (
              <div className="memory-tab-chat-ctx-body">
                <div className="memory-tab-chat-meta">
                  <span className="memory-tab-stat">
                    <strong>{chatCtx.message_count ?? 0}</strong> messages
                  </span>
                  <span className="memory-tab-stat">
                    keep last <strong>{chatCtx.keep_last ?? memSettings.memory_keep_last_messages}</strong>
                  </span>
                  <span className="memory-tab-stat">
                    summary ~<strong>{chatCtx.context_summary_tokens ?? 0}</strong> tokens
                  </span>
                  <span className="memory-tab-stat">
                    covered through #<strong>{chatCtx.context_summary_through ?? 0}</strong>
                  </span>
                  {typeof chatCtx.estimated_history_tokens === "number" ? (
                    <span className="memory-tab-stat">
                      history ~<strong>{chatCtx.estimated_history_tokens}</strong> tokens
                    </span>
                  ) : null}
                  {chatCtx.compress_recommended ? (
                    <span className="memory-tab-chip-warn">Compress recommended</span>
                  ) : null}
                </div>
                {chatCtx.context_summary?.trim() ? (
                  <div className="memory-tab-summary-preview">
                    <MarkdownContent text={chatCtx.context_summary} />
                  </div>
                ) : (
                  <div className="memory-tab-chat-empty">
                    No summary yet. Compress now to rewrite older turns into a rolling summary (messages stay on disk).
                  </div>
                )}
              </div>
            ) : (
              <div className="memory-tab-chat-empty">Loading chat context…</div>
            )}
          </section>
    </div>
  );
}
