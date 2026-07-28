import { ScopedCss, useScopedClass } from "../../utils/scopedCss";

const VERSE_ICON_SIZE_CLASS: Record<number, string> = {
  13: "verse-icon--13",
  14: "verse-icon--14",
  15: "verse-icon--15",
  32: "verse-icon--32",
};

interface VerseIconProps {
  size?: number;
  className?: string;
  title?: string;
}

/** Verse logo from public/verse-icon.svg — tinted via CSS `color` / `--verse-icon-color`. */
export function VerseIcon({ size = 13, className = "", title }: VerseIconProps) {
  const presetClass = VERSE_ICON_SIZE_CLASS[size];
  const scopeClass = useScopedClass("verse-icon");

  return (
    <>
      {!presetClass ? (
        <ScopedCss
          selector={`.${scopeClass}`}
          rules={{ width: `${size}px`, height: `${size}px` }}
        />
      ) : null}
      <span
        className={`verse-icon ${presetClass ?? scopeClass} ${className}`.trim()}
        title={title}
        aria-hidden={title ? undefined : true}
        role={title ? "img" : undefined}
      />
    </>
  );
}
