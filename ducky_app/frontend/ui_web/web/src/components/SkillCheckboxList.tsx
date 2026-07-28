import { useCallback, useEffect, useState } from "react";
import { Icons } from "../icons/Icons";
import type { SkillPackDto } from "../types/panel";

interface SkillCheckboxListProps {
  packs: SkillPackDto[];
  enabledPacks: string[];
  enabledSubskills: Record<string, string[]>;
  onPackToggle: (packId: string, checked: boolean) => void;
  onSubskillToggle: (packId: string, subskillId: string, checked: boolean) => void;
  emptyHint?: string;
  layout?: "flat" | "accordion";
  /** Card rows with switches (profile detail). Accordion only. */
  appearance?: "default" | "cards";
  /** When true, matching packs stay expanded (composer search). */
  forceExpand?: boolean;
  /** Accordion packs start collapsed until toggled open. */
  defaultCollapsed?: boolean;
}

function rootSubskills(pack: SkillPackDto) {
  return pack.subskills.filter((s) => !s.parent_id);
}

function toggleableRoots(pack: SkillPackDto) {
  return rootSubskills(pack).filter((s) => !s.always_on);
}

export function SkillCheckboxList({
  packs,
  enabledPacks,
  enabledSubskills,
  onPackToggle,
  onSubskillToggle,
  emptyHint = "No skill packs yet — create one in Settings → Skills.",
  layout = "flat",
  appearance = "default",
  forceExpand = false,
  defaultCollapsed = true,
}: SkillCheckboxListProps) {
  const [openPacks, setOpenPacks] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!forceExpand) return;
    setOpenPacks((prev) => {
      const next = { ...prev };
      for (const pack of packs) {
        if (toggleableRoots(pack).length > 0) next[pack.id] = true;
      }
      return next;
    });
  }, [forceExpand, packs]);

  const defaultPackOpen = useCallback(
    (pack: SkillPackDto) => {
      if (defaultCollapsed) return false;
      return enabledPacks.includes(pack.id);
    },
    [defaultCollapsed, enabledPacks],
  );

  const isPackOpen = useCallback(
    (pack: SkillPackDto) => {
      if (toggleableRoots(pack).length === 0) return false;
      if (openPacks[pack.id] !== undefined) return openPacks[pack.id];
      return defaultPackOpen(pack);
    },
    [defaultPackOpen, openPacks],
  );

  const togglePackOpen = useCallback(
    (packId: string, pack: SkillPackDto) => {
      setOpenPacks((prev) => {
        const currently = prev[packId] !== undefined ? prev[packId] : defaultPackOpen(pack);
        return { ...prev, [packId]: !currently };
      });
    },
    [defaultPackOpen],
  );

  if (packs.length === 0) {
    return <div className="skill-checkbox-empty">{emptyHint}</div>;
  }

  if (layout === "accordion" && appearance === "cards") {
    return (
      <div className="skill-checkbox-list skill-checkbox-list--cards">
        {packs.map((pack) => {
          const packOn = enabledPacks.includes(pack.id);
          const subs = enabledSubskills[pack.id] ?? [];
          const toggleable = toggleableRoots(pack);
          const checkedToggleable = toggleable.filter((s) => subs.includes(s.id)).length;
          const allToggleableOn = packOn && (toggleable.length === 0 || checkedToggleable === toggleable.length);
          const someToggleableOn =
            packOn && checkedToggleable > 0 && checkedToggleable < toggleable.length;
          const expanded = isPackOpen(pack);
          const switchId = `ducky-skill-pack-${pack.id}`;

          if (toggleable.length === 0) {
            return (
              <div
                key={pack.id}
                className={`ducky-skill-card ducky-skill-card--solo${packOn ? " is-enabled" : ""}`}
              >
                <div className="ducky-skill-card-row">
                  <label className="general-tab-switch general-tab-switch--compact" htmlFor={switchId}>
                    <input
                      id={switchId}
                      className="general-tab-switch-input"
                      type="checkbox"
                      checked={packOn}
                      onChange={(e) => onPackToggle(pack.id, e.target.checked)}
                    />
                    <span className="general-tab-switch-track" aria-hidden />
                  </label>
                  <div className="ducky-skill-card-copy">
                    <span className="ducky-skill-card-title">{pack.label}</span>
                    <span className="ducky-skill-card-desc">
                      {pack.description || (pack.kind === "bundled" ? "Shipped" : "Custom")}
                    </span>
                  </div>
                </div>
              </div>
            );
          }

          return (
            <div
              key={pack.id}
              className={`ducky-skill-card${packOn ? " is-enabled" : ""}${expanded ? " is-open" : ""}`}
            >
              <div className="ducky-skill-card-row">
                <label className="general-tab-switch general-tab-switch--compact" htmlFor={switchId}>
                  <input
                    id={switchId}
                    className="general-tab-switch-input"
                    type="checkbox"
                    checked={allToggleableOn}
                    ref={(el) => {
                      if (el) el.indeterminate = someToggleableOn;
                    }}
                    onChange={(e) => {
                      onPackToggle(pack.id, e.target.checked);
                      if (e.target.checked) {
                        setOpenPacks((prev) => ({ ...prev, [pack.id]: true }));
                      }
                    }}
                  />
                  <span className="general-tab-switch-track" aria-hidden />
                </label>
                <button
                  type="button"
                  className="ducky-skill-card-toggle"
                  aria-expanded={expanded}
                  onClick={() => togglePackOpen(pack.id, pack)}
                >
                  <span className="ducky-skill-card-copy">
                    <span className="ducky-skill-card-title">{pack.label}</span>
                    <span className="ducky-skill-card-desc">
                      {pack.description || (pack.kind === "bundled" ? "Shipped" : "Custom")}
                    </span>
                  </span>
                  <span className="ducky-skill-card-chevron" aria-hidden>
                    <Icons.ChevronDown />
                  </span>
                </button>
              </div>
              <div className={`ducky-skill-card-drawer${expanded ? " is-open" : ""}`}>
                <div className="ducky-skill-card-drawer-inner">
                  {toggleable.map((sub) => {
                    const checked = subs.includes(sub.id);
                    return (
                      <label key={sub.id} className="ducky-skill-sub">
                        <input
                          type="checkbox"
                          className="ducky-skill-sub-check"
                          checked={checked}
                          onChange={(e) => onSubskillToggle(pack.id, sub.id, e.target.checked)}
                        />
                        <span className="ducky-skill-sub-copy">
                          <span className="ducky-skill-sub-title">{sub.label}</span>
                          {sub.description ? (
                            <span className="ducky-skill-sub-desc">{sub.description}</span>
                          ) : null}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  if (layout === "accordion") {
    return (
      <div className="skill-checkbox-list skill-checkbox-list--accordion">
        {packs.map((pack) => {
          const packOn = enabledPacks.includes(pack.id);
          const subs = enabledSubskills[pack.id] ?? [];
          const toggleable = toggleableRoots(pack);
          const checkedToggleable = toggleable.filter((s) => subs.includes(s.id)).length;
          const allToggleableOn = packOn && checkedToggleable === toggleable.length;
          const someToggleableOn =
            packOn && checkedToggleable > 0 && checkedToggleable < toggleable.length;
          const expanded = isPackOpen(pack);

          if (toggleable.length === 0) {
            return (
              <label
                key={pack.id}
                className={`skill-checkbox-item skill-checkbox-item--solo${packOn ? " is-checked" : ""}`}
              >
                <input
                  className="skill-checkbox-input"
                  type="checkbox"
                  checked={packOn}
                  onChange={(e) => onPackToggle(pack.id, e.target.checked)}
                />
                <span className="skill-checkbox-body">
                  <span className="skill-checkbox-filename">{pack.label}</span>
                  <span className="skill-checkbox-desc">
                    {pack.kind === "bundled" ? "Shipped" : "Custom"} · {pack.description || pack.id}
                  </span>
                </span>
              </label>
            );
          }

          return (
            <div key={pack.id} className={`composer-attach-accordion${expanded ? " is-open" : ""}`}>
              <div className="composer-attach-accordion-header">
                <label
                  className={`skill-checkbox-item skill-checkbox-item--header${packOn ? " is-checked" : ""}`}
                >
                  <input
                    className="skill-checkbox-input"
                    type="checkbox"
                    checked={allToggleableOn}
                    ref={(el) => {
                      if (el) el.indeterminate = someToggleableOn;
                    }}
                    onChange={(e) => {
                      onPackToggle(pack.id, e.target.checked);
                      if (e.target.checked) {
                        setOpenPacks((prev) => ({ ...prev, [pack.id]: true }));
                      }
                    }}
                  />
                  <span className="skill-checkbox-body">
                    <span className="skill-checkbox-filename">{pack.label}</span>
                    <span className="skill-checkbox-desc">
                      {pack.kind === "bundled" ? "Shipped" : "Custom"} · {pack.description || pack.id}
                    </span>
                  </span>
                </label>
                <button
                  type="button"
                  className="composer-attach-accordion-toggle"
                  aria-expanded={expanded}
                  aria-label={`${expanded ? "Collapse" : "Expand"} ${pack.label}`}
                  onClick={() => togglePackOpen(pack.id, pack)}
                >
                  <span className="composer-attach-accordion-chevron">
                    <Icons.ChevronRight />
                  </span>
                </button>
              </div>
              <div className="composer-attach-accordion-clip">
                <div className="composer-attach-accordion-body">
                  <div className="skill-checkbox-subs">
                    {toggleable.map((sub) => {
                      const checked = subs.includes(sub.id);
                      return (
                        <label
                          key={sub.id}
                          className={`skill-checkbox-item${checked ? " is-checked" : ""}`}
                        >
                          <input
                            className="skill-checkbox-input"
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => onSubskillToggle(pack.id, sub.id, e.target.checked)}
                          />
                          <span className="skill-checkbox-body">
                            <span className="skill-checkbox-filename">{sub.label}</span>
                            <span className="skill-checkbox-desc">{sub.description || sub.id}</span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className="skill-checkbox-list">
      {packs.map((pack) => {
        const packOn = enabledPacks.includes(pack.id);
        const subs = enabledSubskills[pack.id] ?? [];
        const toggleable = toggleableRoots(pack);
        return (
          <div key={pack.id}>
            <label className={`skill-checkbox-item${packOn ? " is-checked" : ""}`}>
              <input
                className="skill-checkbox-input"
                type="checkbox"
                checked={packOn}
                onChange={(e) => onPackToggle(pack.id, e.target.checked)}
              />
              <span className="skill-checkbox-body">
                <span className="skill-checkbox-filename">{pack.label}</span>
                <span className="skill-checkbox-desc">
                  {pack.kind === "bundled" ? "Shipped" : "Custom"} · {pack.description || pack.id}
                </span>
              </span>
            </label>
            {packOn && toggleable.length > 0 ? (
              <div className="skill-checkbox-subs">
                {toggleable.map((sub) => {
                  const checked = subs.includes(sub.id);
                  return (
                    <label
                      key={sub.id}
                      className={`skill-checkbox-item${checked ? " is-checked" : ""}`}
                    >
                      <input
                        className="skill-checkbox-input"
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => onSubskillToggle(pack.id, sub.id, e.target.checked)}
                      />
                      <span className="skill-checkbox-body">
                        <span className="skill-checkbox-filename">{sub.label}</span>
                        <span className="skill-checkbox-desc">{sub.description || sub.id}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
