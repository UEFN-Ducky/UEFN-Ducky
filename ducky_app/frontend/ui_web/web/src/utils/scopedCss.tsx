import { useId } from "react";

/** Stable scoped class suffix for dynamic stylesheet rules (no inline `style` on elements). */
export function useScopedClass(prefix: string): string {
  const id = useId().replace(/:/g, "");
  return `${prefix}-${id}`;
}

function rulesToCssText(rules: Record<string, string | undefined | null>): string {
  return Object.entries(rules)
    .filter((entry): entry is [string, string] => entry[1] != null && entry[1] !== "")
    .map(([prop, value]) => `${prop}: ${value}`)
    .join("; ");
}

interface ScopedCssProps {
  selector: string;
  rules: Record<string, string | undefined | null>;
}

/** Injects a scoped rule into the document — values live in a stylesheet, not on the element. */
export function ScopedCss({ selector, rules }: ScopedCssProps) {
  const css = rulesToCssText(rules);
  if (!css) return null;
  return <style>{`${selector} { ${css} }`}</style>;
}
