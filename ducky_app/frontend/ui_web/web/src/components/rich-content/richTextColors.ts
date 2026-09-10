import { Children, isValidElement } from "react";
import type { ReactNode } from "react";
import { defaultUrlTransform } from "react-markdown";

export const RICH_TEXT_COLORS = ["blue", "purple", "green", "amber", "yellow", "red"] as const;
export type RichTextColor = typeof RICH_TEXT_COLORS[number];

/** A presentation marker, never a navigable URL or an arbitrary CSS value. */
export function richColorFromHref(href: string): RichTextColor | undefined {
  const match = /^ducky:(blue|purple|green|amber|yellow|red)$/.exec(href);
  return match?.[1] as RichTextColor | undefined;
}

export function richUrlTransform(url: string, key: string): string {
  if (key === "href" && (richColorFromHref(url) || /^plan-node:[\w-]+$/.test(url))) return url;
  if (key === "href" && /^ducky:\/\/settings\.llms\/[a-z0-9_-]+#login$/i.test(url)) return url;
  return defaultUrlTransform(url);
}

/** Read labels only; never color entire paragraphs or change their contents. */
export function richNodeText(children: ReactNode): string {
  return Children.toArray(children).map((child) => {
    if (typeof child === "string" || typeof child === "number") return String(child);
    if (!isValidElement<{ children?: ReactNode }>(child)) return "";
    return richNodeText(child.props.children);
  }).join("");
}

export function richTextColor(text: string): RichTextColor | undefined {
  if (text.length > 120) return undefined;
  // Category names and identifiers work in ordinary Markdown as well as reports.
  // Status colors require a leading status label, so “no errors” is not red.
  if (/^(?:error|failed|failure|broken)\b/i.test(text)) return "red";
  if (/^(?:warning|caution|gotcha|blocked|pending|loose end|remaining work)\b/i.test(text)) return "amber";
  if (/^(?:verified|verify|validation|passed|success|complete|completed)\b/i.test(text)) return "green";
  if (/\bblender\b|\bblender_|\.blend\b/i.test(text)) return "green";
  if (/\bverse\b|\.verse\b|\bworkspace_\w*verse\w*|@editable\b/i.test(text)) return "purple";
  if (/\bumg\b|\bwidgets?\b|\bUW_|\bWBP_|\bCanvasPanel\b/i.test(text)) return "yellow";
  if (/\bblueprints?\b|\bprefabs?\b|\bEntityPrefab\b|\bBP_|\bP_|\bPF_/i.test(text)) return "amber";
  if (/\buefn\b|\bdevices?\b|\b(trigger|button|hud)_device\b/i.test(text)) return "blue";
  if (/\bmesh(?:es)?\b|\bprops?\b|\bSM_|\bmaterials?\b|\bM_|\bMI_/i.test(text)) return "green";
  return undefined;
}

export function richTextClass(text: string): string {
  const color = richTextColor(text);
  return color ? `rich-tone--${color}` : "";
}
