import { DEFAULT_BUNDLED_DUCKY_STYLE } from "../../generated/bundledDuckies";
import { ScopedCss, useScopedClass } from "../../utils/scopedCss";
import { useDuckyCatalogOptional } from "./DuckyCatalogContext";

export const DUCKY_AVATAR_SIZES = {
  sidebar: 36,
  tab: 18,
  compact: 18,
  history: 18,
  emptyPane: 200,
  picker: 48,
} as const;

const DUCKY_SIZE_CLASS: Record<number, string> = {
  18: "ducky-avatar--18",
  22: "ducky-avatar--22",
  30: "ducky-avatar--30",
  36: "ducky-avatar--36",
  48: "ducky-avatar--48",
  56: "ducky-avatar--56",
};

interface DuckyAvatarProps {
  styleId?: string | null;
  size?: number;
  className?: string;
  title?: string;
}

export function DuckyAvatar({
  styleId,
  size = DUCKY_AVATAR_SIZES.compact,
  className,
  title,
}: DuckyAvatarProps) {
  const catalog = useDuckyCatalogOptional();
  const url = catalog?.resolveUrl(styleId) ?? "";
  const label = title ?? catalog?.labelFor(styleId) ?? "Ducky";
  const presetClass = DUCKY_SIZE_CLASS[size];
  const scopeClass = useScopedClass("ducky-avatar");

  const sizeRules = presetClass
    ? null
    : {
        width: `${size}px`,
        height: `${size}px`,
        maxWidth: `${size}px`,
        maxHeight: `${size}px`,
        "--ducky-avatar-size": `${size}px`,
      };

  const avatarClass = `ducky-avatar ${presetClass ?? scopeClass} ${className ?? ""}`.trim();

  if (!url) {
    return (
      <>
        {sizeRules ? <ScopedCss selector={`.${scopeClass}`} rules={sizeRules} /> : null}
        <span className={avatarClass} role="img" aria-label={label} />
      </>
    );
  }

  return (
    <>
      {sizeRules ? <ScopedCss selector={`.${scopeClass}`} rules={sizeRules} /> : null}
      <img
        className={avatarClass}
        src={url}
        width={size}
        height={size}
        alt={label}
        title={title}
        draggable={false}
      />
    </>
  );
}

export function normalizeDuckyStyle(styleId?: string | null): string {
  const raw = (styleId || "").trim();
  return raw || DEFAULT_BUNDLED_DUCKY_STYLE;
}

export function randomDuckyStyle(): string {
  return DEFAULT_BUNDLED_DUCKY_STYLE;
}

export { randomDuckyStyleFromCatalog, useDuckyCatalog } from "./DuckyCatalogContext";
