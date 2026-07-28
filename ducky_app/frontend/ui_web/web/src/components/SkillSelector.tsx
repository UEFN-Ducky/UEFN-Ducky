import { useCallback, useEffect, useRef, useState } from "react";
import { DropdownPanel } from "./DropdownPanel";
import { SkillCheckboxList } from "./SkillCheckboxList";
import { Icons } from "../icons/Icons";
import { getApi } from "../hooks/usePanelApi";
import { onApiReady } from "../hooks/onApiReady";
import type { ConversationSkillsDto } from "../types/panel";

interface SkillSelectorProps {
  convId: string;
  onSkillsChanged?: () => void;
}

export function SkillSelector({ convId, onSkillsChanged }: SkillSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [packs, setPacks] = useState<ConversationSkillsDto["packs"]>([]);
  const [enabledPacks, setEnabledPacks] = useState<string[]>([]);
  const [enabledSubskills, setEnabledSubskills] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(true);
  const anchorRef = useRef<HTMLButtonElement>(null);

  const refresh = useCallback(() => {
    const api = getApi();
    if (!api?.get_conversation_skills || !convId) return;
    setLoading(true);
    void api
      .get_conversation_skills(convId)
      .then((data: ConversationSkillsDto) => {
        setPacks(data.packs ?? data.catalog ?? []);
        setEnabledPacks(data.enabled_packs ?? data.enabled_skills ?? []);
        setEnabledSubskills(data.enabled_subskills ?? {});
      })
      .finally(() => setLoading(false));
  }, [convId]);

  useEffect(() => onApiReady(() => refresh()), [refresh]);
  useEffect(() => {
    refresh();
  }, [convId, refresh]);

  const persist = useCallback(
    async (nextPacks: string[], nextSubs: Record<string, string[]>) => {
      const api = getApi();
      if (!api?.set_conversation_skill_selection) return;
      setEnabledPacks(nextPacks);
      setEnabledSubskills(nextSubs);
      try {
        const res = await api.set_conversation_skill_selection(convId, nextPacks, nextSubs);
        setEnabledPacks(res.enabled_packs ?? nextPacks);
        setEnabledSubskills(res.enabled_subskills ?? nextSubs);
        onSkillsChanged?.();
      } catch {
        refresh();
      }
    },
    [convId, onSkillsChanged, refresh],
  );

  const handlePackToggle = useCallback(
    (packId: string, checked: boolean) => {
      const nextPacks = checked
        ? [...enabledPacks, packId]
        : enabledPacks.filter((p) => p !== packId);
      const nextSubs = { ...enabledSubskills };
      if (!checked) {
        delete nextSubs[packId];
      } else {
        const pack = packs.find((p) => p.id === packId);
        if (pack && !nextSubs[packId]) {
          nextSubs[packId] = pack.subskills
            .filter((s) => s.default_enabled || s.always_on)
            .map((s) => s.id);
        }
      }
      void persist(nextPacks, nextSubs);
    },
    [enabledPacks, enabledSubskills, packs, persist],
  );

  const handleSubskillToggle = useCallback(
    (packId: string, subskillId: string, checked: boolean) => {
      const current = enabledSubskills[packId] ?? [];
      const next = checked
        ? [...current, subskillId]
        : current.filter((id) => id !== subskillId);
      const nextSubs = { ...enabledSubskills, [packId]: next };
      const nextPacks = enabledPacks.includes(packId) ? enabledPacks : [...enabledPacks, packId];
      void persist(nextPacks, nextSubs);
    },
    [enabledPacks, enabledSubskills, persist],
  );

  const subCount = Object.values(enabledSubskills).reduce((n, ids) => n + ids.length, 0);
  const label = subCount > 0 ? `+${subCount}` : "+";

  return (
    <div className="skill-selector">
      <button
        ref={anchorRef}
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        title="Skills for this ducky"
        className={`skill-selector-btn${isOpen ? " is-open" : ""}${subCount > 0 ? " has-skills" : ""}`}
      >
        <Icons.Plus />
        <span>{label}</span>
      </button>

      <DropdownPanel
        anchorRef={anchorRef}
        open={isOpen}
        onClose={() => setIsOpen(false)}
        placement="top"
        minWidth={280}
        width={300}
      >
        <div className="skill-selector-panel">
          <div className="skill-selector-panel-title">Skills</div>
          {loading ? (
            <div className="skill-checkbox-empty">Loading…</div>
          ) : (
            <SkillCheckboxList
              packs={packs}
              enabledPacks={enabledPacks}
              enabledSubskills={enabledSubskills}
              onPackToggle={handlePackToggle}
              onSubskillToggle={handleSubskillToggle}
            />
          )}
        </div>
      </DropdownPanel>
    </div>
  );
}
