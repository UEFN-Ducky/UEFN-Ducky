import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppNotice } from "../components/AppNotice";
import {
  AgentCatalogCreateModal,
  CatalogDetailHead,
  CatalogListRow,
  CatalogSlideShell,
  useCatalogSlideNav,
  type CatalogBreadcrumb,
} from "../components/catalog-slide";
import { getCachedModels } from "../hooks/modelsCatalogCache";
import { onApiReady } from "../hooks/onApiReady";
import {
  peekSkillPacksCatalogCache,
  rememberSkillPackFiles,
  rememberSkillPacksCatalog,
} from "../hooks/skillPacksCatalogCache";
import { useTimedMessage } from "../hooks/useTimedMessage";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { Icons } from "../icons/Icons";
import type { SettingsNavLocation } from "../navigation/settingsHistory";
import {
  useApplySettingsDrill,
  useRecordSettingsLocation,
  useSettingsHistoryBack,
} from "../navigation/useSettingsHistory";
import { fileKey, type PackWithFiles, type SkillFile } from "./model/types";
import { FileEditorPane } from "./components/FileEditorPane";
import { PackEditorPane } from "./components/PackEditorPane";
import { CreateReferenceFileModal } from "./components/CreateReferenceFileModal";
import * as api from "./api/skillPackStudioApi";
import type { SkillPackDraft } from "./api/skillPackStudioApi";
import { fileToBase64, pickSkillPackZipFile } from "./utils/fileTransfer";
import { targetRef } from "../ui-targets/registry";
import { fileBasename } from "./utils/fileDisplay";
import { packCatalogSource, packOriginBadge } from "./utils/packOrigin";
import "./skill-pack-studio.css";

function providerForModel(modelId: string): string {
  return getCachedModels()?.find((m) => m.id === modelId)?.providerKey ?? "";
}

function matchesQuery(pack: PackWithFiles, query: string): boolean {
  const q = query.toLowerCase();
  if (
    pack.label.toLowerCase().includes(q) ||
    pack.description.toLowerCase().includes(q) ||
    pack.id.toLowerCase().includes(q)
  ) {
    return true;
  }
  return pack.files.some(
    (f) =>
      f.title.toLowerCase().includes(q) ||
      f.file.toLowerCase().includes(q) ||
      fileBasename(f.file).toLowerCase().includes(q),
  );
}

interface SkillPackStudioProps {
  sectionTab?: "skills" | "mcps";
}

export function SkillPackStudio({ sectionTab = "skills" }: SkillPackStudioProps) {
  const { confirm } = useConfirmModal();
  const {
    selectedKey,
    setSelectedKey,
    focusId,
    setFocusId,
    detailOpen,
    detailRendered,
    openDetail,
    closeDetail,
  } = useCatalogSlideNav();
  const cached = peekSkillPacksCatalogCache();
  const [packs, setPacks] = useState<PackWithFiles[]>(() => cached?.packs ?? []);
  const [dirtyKeys, setDirtyKeys] = useState<Record<string, boolean>>({});
  // Match Store/Duckies: only splash Loading when this session has nothing cached.
  const [busy, setBusy] = useState(() => !(cached?.packs.length));
  const [contentLoadingPackId, setContentLoadingPackId] = useState<string | null>(null);
  const [contentLoadedPackIds, setContentLoadedPackIds] = useState<Record<string, boolean>>(
    () => cached?.contentLoadedPackIds ?? {},
  );
  const contentLoadedRef = useRef<Record<string, boolean>>(cached?.contentLoadedPackIds ?? {});
  const contentLoadingRef = useRef<string | null>(null);
  const [query, setQuery] = useState("");
  const [statusMsg, setStatusMsg] = useTimedMessage();
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [addFilePackId, setAddFilePackId] = useState<string | null>(null);

  const ensurePackContent = useCallback(
    async (packId: string, force = false) => {
      if (!force && contentLoadedRef.current[packId]) return;
      if (!force && contentLoadingRef.current === packId) return;
      contentLoadingRef.current = packId;
      setContentLoadingPackId(packId);
      try {
        const files = await api.loadPackFiles(packId);
        setPacks((prev) => prev.map((p) => (p.id === packId ? { ...p, files } : p)));
        contentLoadedRef.current = { ...contentLoadedRef.current, [packId]: true };
        setContentLoadedPackIds(contentLoadedRef.current);
        rememberSkillPackFiles(packId, files);
      } catch (e) {
        setStatusMsg(e instanceof Error ? e.message : String(e));
      } finally {
        if (contentLoadingRef.current === packId) contentLoadingRef.current = null;
        setContentLoadingPackId((cur) => (cur === packId ? null : cur));
      }
    },
    [setStatusMsg],
  );

  const reload = useCallback(
    async (selectPackId?: string) => {
      const hadCache = Boolean(peekSkillPacksCatalogCache()?.packs.length);
      // Soft refresh: keep the list visible so tab switches don't flash Loading…
      if (!hadCache) setBusy(true);
      try {
        const { packs: next, error } = await api.listPacksWithFiles();
        const prev = peekSkillPacksCatalogCache();
        const loadedIds: Record<string, boolean> = {};
        for (const [id, ok] of Object.entries(prev?.contentLoadedPackIds ?? {})) {
          if (ok && next.some((p) => p.id === id)) loadedIds[id] = true;
        }
        const merged = next.map((p) => {
          if (!loadedIds[p.id]) return p;
          const old = prev?.packs.find((x) => x.id === p.id);
          return old ? { ...p, files: old.files } : p;
        });
        setPacks(merged);
        contentLoadedRef.current = loadedIds;
        setContentLoadedPackIds(loadedIds);
        rememberSkillPacksCatalog(merged, loadedIds);
        if (error) setStatusMsg(error);
        let reopen: string | null = null;
        if (selectPackId) {
          if (next.some((p) => p.id === selectPackId)) {
            openDetail(selectPackId);
            reopen = selectPackId;
          } else {
            closeDetail();
          }
        } else {
          setSelectedKey((prevKey) => {
            const nextKey = prevKey && next.some((p) => p.id === prevKey) ? prevKey : null;
            reopen = nextKey;
            setFocusId((prevFocus) => {
              if (!prevFocus || !nextKey) return null;
              const pack = merged.find((p) => p.id === nextKey);
              return pack?.files.some((f) => f.id === prevFocus) ? prevFocus : null;
            });
            return nextKey;
          });
        }
        // Mutations pass selectPackId → force body reload; soft remount keeps cache.
        if (reopen) await ensurePackContent(reopen, Boolean(selectPackId));
      } catch (e) {
        setStatusMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [closeDetail, ensurePackContent, openDetail, setFocusId, setSelectedKey, setStatusMsg],
  );

  useEffect(() => onApiReady(() => void reload()), [reload]);

  // Open a pack → load that pack's markdown only (list stays metadata-only).
  useEffect(() => {
    if (!selectedKey || contentLoadedRef.current[selectedKey]) return;
    void ensurePackContent(selectedKey);
  }, [selectedKey, ensurePackContent]);

  const patchFile = useCallback((packId: string, fileId: string, patch: Partial<SkillFile>) => {
    setPacks((prev) =>
      prev.map((p) =>
        p.id === packId
          ? { ...p, files: p.files.map((f) => (f.id === fileId ? { ...f, ...patch } : f)) }
          : p,
      ),
    );
  }, []);

  const patchPack = useCallback((packId: string, patch: Partial<PackWithFiles>) => {
    setPacks((prev) => prev.map((p) => (p.id === packId ? { ...p, ...patch } : p)));
  }, []);

  const pendingDraft = useRef<SkillPackDraft | null>(null);
  const existingPackIds = useMemo(() => packs.map((p) => p.id), [packs]);

  const addFilePack = useMemo(
    () => packs.find((p) => p.id === addFilePackId) ?? null,
    [addFilePackId, packs],
  );

  const handleFileCreated = useCallback(
    async (packId: string, fileId: string) => {
      setBusy(true);
      try {
        await reload(packId);
        setFocusId(fileId);
        setStatusMsg("Reference file added");
      } catch (e) {
        setStatusMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [reload, setFocusId],
  );

  const handleDeletePack = useCallback(
    async (packId: string) => {
      const pack = packs.find((p) => p.id === packId);
      if (
        !(await confirm({
          message: `Delete skill pack "${pack?.label ?? packId}"?`,
          confirmLabel: "Delete",
          danger: true,
        }))
      ) {
        return;
      }
      setBusy(true);
      try {
        await api.deletePack(packId);
        if (selectedKey === packId) closeDetail();
        setStatusMsg(`Deleted pack ${packId}`);
        await reload();
      } catch (e) {
        setStatusMsg(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [closeDetail, confirm, packs, reload, selectedKey],
  );

  const handleImport = useCallback(async () => {
    setBusy(true);
    setStatusMsg("");
    try {
      const file = await pickSkillPackZipFile();
      if (!file) return;
      const dataBase64 = await fileToBase64(file);
      let res = await api.importPackBytes(file.name, dataBase64);
      if (res.conflict && res.existing_id) {
        const rename = await confirm({
          message: `Pack "${res.existing_id}" already exists. Import as a new id instead?`,
          confirmLabel: "Rename & import",
          cancelLabel: "Cancel",
        });
        if (!rename) return;
        const newId = `${res.existing_id}_imported`;
        res = await api.importPackBytes(file.name, dataBase64, newId, false);
      }
      if (res.cancelled) return;
      if (!res.ok) throw new Error(res.error ?? "Import failed");
      await reload(res.pack_id ?? undefined);
      const termsNote =
        res.allow_redistribute === false ? " (personal use / no redistribute)" : "";
      setStatusMsg(`Imported ${res.label ?? res.pack_id}${termsNote}`);
    } catch (e) {
      setStatusMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [confirm, reload]);

  const filtered = useMemo(() => {
    const q = query.trim();
    if (!q) return packs;
    return packs.filter((p) => matchesQuery(p, q));
  }, [packs, query]);

  const selectedPack = selectedKey ? packs.find((p) => p.id === selectedKey) ?? null : null;
  const focusFile =
    focusId && selectedPack ? selectedPack.files.find((f) => f.id === focusId) ?? null : null;

  const skillsNavLoc = useMemo<SettingsNavLocation | null>(() => {
    if (sectionTab !== "skills") return null;
    if (!selectedKey) {
      return {
        kind: "settings",
        tab: "LLMs",
        sectionTab: "skills",
        name: "LLMs · skills",
      };
    }
    return {
      kind: "settings",
      tab: "LLMs",
      sectionTab: "skills",
      drill: { type: "skills", packId: selectedKey, fileId: focusId },
      name: focusFile
        ? fileBasename(focusFile.file)
        : selectedPack?.label || selectedKey,
    };
  }, [sectionTab, selectedKey, focusId, focusFile, selectedPack?.label]);
  useRecordSettingsLocation(skillsNavLoc);

  const applySkillsDrill = useCallback(
    (loc: SettingsNavLocation) => {
      if (loc.sectionTab && loc.sectionTab !== "skills") return;
      const drill = loc.drill?.type === "skills" ? loc.drill : null;
      const packId = drill?.packId ?? null;
      if (!packId) {
        closeDetail();
        return;
      }
      if (!packs.some((p) => p.id === packId)) {
        closeDetail();
        return;
      }
      setSelectedKey(packId);
      setFocusId(drill?.fileId || null);
    },
    [closeDetail, packs, setFocusId, setSelectedKey],
  );
  useApplySettingsDrill("LLMs", applySkillsDrill);

  const historyCloseDetail = useSettingsHistoryBack(closeDetail);

  // History records pack + file levels — one back always goes to the previous level.
  const handleDetailBack = () => {
    historyCloseDetail();
  };

  const goToSkillsList = () => {
    // From a file, pop file→pack then pack→list so "Skills" lands on the catalog.
    if (focusId) historyCloseDetail();
    historyCloseDetail();
  };

  const breadcrumbs: CatalogBreadcrumb[] = selectedPack
    ? [
        { id: "root", label: "Skills", onClick: goToSkillsList },
        {
          id: "pack",
          label: selectedPack.label,
          current: !focusFile,
          onClick: focusFile ? historyCloseDetail : undefined,
        },
        ...(focusFile
          ? [{ id: "file", label: fileBasename(focusFile.file), current: true }]
          : []),
      ]
    : [];

  const canDeletePack = selectedPack ? packOriginBadge(selectedPack) === "Created" : false;

  return (
    <CatalogSlideShell
      className="skills-tab"
      detailOpen={detailOpen}
      detailRendered={detailRendered}
      detailFlush
      detailPlaceholder={<p>Select a skill pack to view it.</p>}
      listAriaLabel="Skill packs"
      notice={statusMsg ? <AppNotice message={statusMsg} className="catalog-slide-notice" /> : null}
      listHeader={
        <div className="catalog-slide-header">
          <div className="catalog-slide-header-titles">
            <h2
              className="catalog-slide-title"
              ref={targetRef("settings.skills.list", {
                kind: "settings_field",
                label: "Skill packs",
                route: "settings.skills",
              })}
            >
              Skill packs
            </h2>
          </div>
          <div className="catalog-slide-header-actions">
            <label className="catalog-slide-search">
              <span className="catalog-slide-search-icon" aria-hidden>
                <Icons.Search />
              </span>
              <input
                className="catalog-slide-search-input"
                type="search"
                placeholder="Search packs…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="catalog-slide-action"
              disabled={busy}
              title="Create a new skill pack"
              aria-label="Create a new skill pack"
              onClick={() => setCreateModalOpen(true)}
            >
              <Icons.Plus />
            </button>
            <button
              type="button"
              className="catalog-slide-action"
              disabled={busy}
              title="Import a .ducky-skill-pack"
              aria-label="Import a .ducky-skill-pack"
              onClick={() => void handleImport()}
            >
              <Icons.Upload />
            </button>
            <button
              type="button"
              className="catalog-slide-refresh-btn"
              onClick={() => void reload()}
              disabled={busy}
              title="Refresh"
              aria-label="Refresh"
            >
              <Icons.Refresh />
            </button>
          </div>
        </div>
      }
      listBody={
        busy && packs.length === 0 ? (
          <div className="catalog-slide-empty">Loading skill packs…</div>
        ) : filtered.length === 0 ? (
          <div className="catalog-slide-empty">
            {packs.length === 0
              ? "No skill packs yet — create or import one."
              : "No packs match your search."}
          </div>
        ) : (
          <ul className="catalog-slide-list">
            {filtered.map((pack) => {
              const canDelete = packOriginBadge(pack) === "Created";
              return (
                <CatalogListRow
                  key={pack.id}
                  liRef={
                    pack.id === "ducky"
                      ? targetRef("settings.skills.row.ducky", {
                          kind: "skill_row",
                          label: pack.label,
                          route: "settings.skills",
                        })
                      : targetRef(`settings.skills.row.${pack.id}`, {
                          kind: "skill_row",
                          label: pack.label,
                          route: "settings.skills",
                        })
                  }
                  title={pack.label}
                  source={packCatalogSource(pack)}
                  selected={selectedKey === pack.id}
                  disabled={busy}
                  icon={<Icons.Sparkles />}
                  meta={
                    <>
                      <span className="sps-mono">{pack.id}</span>
                      <span>
                        · {pack.files.length} file{pack.files.length === 1 ? "" : "s"}
                      </span>
                    </>
                  }
                  overview={pack.description || undefined}
                  onOpen={() => openDetail(pack.id)}
                  actions={
                    canDelete ? (
                      <button
                        type="button"
                        className="catalog-slide-action catalog-slide-action--danger"
                        title="Delete pack"
                        disabled={busy}
                        onClick={() => void handleDeletePack(pack.id)}
                      >
                        <Icons.Trash />
                      </button>
                    ) : undefined
                  }
                />
              );
            })}
          </ul>
        )
      }
      detailHead={
        selectedPack ? (
          <CatalogDetailHead
            breadcrumbs={breadcrumbs}
            onBack={handleDetailBack}
            backButtonRef={targetRef("settings.skills.back", {
              kind: "button",
              label: "Back",
              route: "settings.skills",
            })}
            backAriaLabel={focusFile ? "Back to pack" : "Back to skill packs"}
            actions={
              focusFile ? null : (
                <>
                  {selectedPack.kind !== "plugin" ? (
                    <button
                      type="button"
                      className="catalog-slide-action"
                      title="Add a reference file"
                      aria-label="Add a reference file"
                      disabled={busy}
                      onClick={() => setAddFilePackId(selectedPack.id)}
                    >
                      <Icons.Plus />
                    </button>
                  ) : null}
                  {canDeletePack ? (
                    <button
                      type="button"
                      className="catalog-slide-action catalog-slide-action--danger"
                      title="Delete pack"
                      aria-label="Delete pack"
                      disabled={busy}
                      onClick={() => void handleDeletePack(selectedPack.id)}
                    >
                      <Icons.Trash />
                    </button>
                  ) : null}
                </>
              )
            }
          />
        ) : null
      }
      detailBody={
        selectedPack
          ? focusFile
            ? contentLoadingPackId === selectedPack.id || !contentLoadedPackIds[selectedPack.id]
              ? (
                  <div className="catalog-slide-empty">Loading pack content…</div>
                )
              : (
                <FileEditorPane
                  pack={selectedPack}
                  file={focusFile}
                  dirty={!!dirtyKeys[fileKey(selectedPack.id, focusFile.id)]}
                  busy={busy}
                  onPatchFile={patchFile}
                  onDirtyChange={(key, dirty) =>
                    setDirtyKeys((prev) => ({ ...prev, [key]: dirty }))
                  }
                  onReload={() => void reload(selectedPack.id)}
                  onStatus={setStatusMsg}
                />
              )
            : (
                <PackEditorPane
                  pack={selectedPack}
                  busy={busy || contentLoadingPackId === selectedPack.id}
                  onPatchPack={patchPack}
                  onSelectFile={(_packId, fileId) => setFocusId(fileId)}
                  onStatus={setStatusMsg}
                />
              )
          : null
      }
    >
      <AgentCatalogCreateModal
        open={createModalOpen}
        title="New skill pack"
        nameLabel="Pack name"
        namePlaceholder="e.g. level-design"
        descriptionLabel="What should this pack teach?"
        descriptionPlaceholder="Describe the skill, when to load it, and what files it needs"
        busy={busy}
        onClose={() => {
          setCreateModalOpen(false);
          pendingDraft.current = null;
        }}
        onGenerate={async ({ name, description, model }) => {
          if (!model) throw new Error("Pick a model first");
          const prompt = name.trim()
            ? `Pack name: ${name.trim()}\n\n${description.trim()}`
            : description.trim();
          const next = await api.draftSkillPack(prompt, model, providerForModel(model));
          const slug = api.packSlugFromLabel(name.trim() || next.label);
          if (existingPackIds.includes(slug)) {
            throw new Error(`A pack named "${slug}" already exists — change the name or delete the old pack.`);
          }
          pendingDraft.current = {
            ...next,
            label: name.trim() || next.label,
            slug,
          };
          const checklist = ["Created SKILL.md", "Filled SKILL.md"];
          for (const f of pendingDraft.current.files) {
            checklist.push(`Added references/${f.id}.md`);
          }
          return { checklist };
        }}
        onSave={async ({ name, description, generated }) => {
          setBusy(true);
          try {
            let draft = pendingDraft.current;
            if (!generated || !draft) {
              if (!name.trim()) throw new Error("Enter a pack name first");
              draft = api.emptyDraftFromDescription(description.trim() || name.trim(), name.trim());
              if (existingPackIds.includes(draft.slug)) {
                throw new Error(`A pack named "${draft.slug}" already exists.`);
              }
            }
            const packId = await api.commitSkillPackDraft(draft);
            pendingDraft.current = null;
            await reload(packId);
            setStatusMsg(`Created skill pack "${draft.label}"`);
          } finally {
            setBusy(false);
          }
        }}
      />
      <CreateReferenceFileModal
        open={addFilePackId !== null}
        packId={addFilePackId}
        packLabel={addFilePack?.label ?? addFilePackId ?? ""}
        existingFileIds={addFilePack?.files.map((f) => f.id) ?? []}
        busy={busy}
        onClose={() => setAddFilePackId(null)}
        onCreated={(packId, fileId) => void handleFileCreated(packId, fileId)}
      />
    </CatalogSlideShell>
  );
}
