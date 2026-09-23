export type StackedPanelFlexInput<TId extends string> = {
  order: readonly TId[];
  collapsed: Record<TId, boolean>;
  panelFlex?: Partial<Record<TId, number>>;
  splitRatio: number;
  minPanelHeight: number;
};

export type StackedPanelResizeSnapshot<TId extends string> = {
  order: TId[];
  /** Rendered heights of open panels only, captured once at pointer-down. */
  panelHeights: Partial<Record<TId, number>>;
};

function openPanelIds<TId extends string>(order: readonly TId[], collapsed: Record<TId, boolean>): TId[] {
  return order.filter((id) => !collapsed[id]);
}

/** Normalized flex weights (sum = 1) for each open panel in stack order. */
export function resolveStackedPanelFlex<TId extends string>(
  input: StackedPanelFlexInput<TId>,
): Map<TId, number> {
  const { order, collapsed, panelFlex, splitRatio } = input;
  const openIds = openPanelIds(order, collapsed);
  const flex = new Map<TId, number>();
  if (openIds.length === 0) return flex;
  if (openIds.length === 1) {
    flex.set(openIds[0]!, 1);
    return flex;
  }

  const hasCustomFlex = !!panelFlex && openIds.some((id) => {
    const value = panelFlex[id];
    return typeof value === "number" && Number.isFinite(value) && value > 0;
  });

  if (hasCustomFlex && panelFlex) {
    for (const id of openIds) {
      const value = panelFlex[id];
      flex.set(id, typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 1);
    }
  } else if (openIds.length === 2) {
    flex.set(openIds[0]!, splitRatio);
    flex.set(openIds[1]!, 1 - splitRatio);
  } else {
    const equal = 1 / openIds.length;
    for (const id of openIds) flex.set(id, equal);
  }

  const sum = [...flex.values()].reduce((total, value) => total + value, 0);
  if (sum <= 0) {
    const equal = 1 / openIds.length;
    for (const id of openIds) flex.set(id, equal);
    return flex;
  }

  for (const id of openIds) flex.set(id, flex.get(id)! / sum);
  return flex;
}

export function flexGrowForStackedPanel<TId extends string>(
  input: StackedPanelFlexInput<TId>,
  panelId: TId,
): number {
  if (input.collapsed[panelId]) return 0;
  const openIds = openPanelIds(input.order, input.collapsed);
  if (openIds.length === 0) return 0;
  if (openIds.length === 1) return 1;

  const { panelFlex } = input;
  const hasCustomFlex = !!panelFlex && openIds.some((id) => {
    const value = panelFlex[id];
    return typeof value === "number" && Number.isFinite(value) && value > 0;
  });
  if (hasCustomFlex && panelFlex) {
    const value = panelFlex[panelId];
    if (typeof value === "number" && Number.isFinite(value) && value > 0) return value;
    return 1;
  }

  return resolveStackedPanelFlex(input).get(panelId) ?? 1 / openIds.length;
}

export function resizeStackedPanelSplit<TId extends string>(
  input: StackedPanelFlexInput<TId>,
  splitIndex: number,
  deltaPx: number,
  containerHeight: number,
  snapshot?: StackedPanelResizeSnapshot<TId>,
): { panelFlex: Partial<Record<TId, number>>; splitRatio: number } {
  const { collapsed, panelFlex, splitRatio, minPanelHeight } = input;
  const order = snapshot?.order ?? input.order;
  if (containerHeight <= 0 || !Number.isFinite(deltaPx)) {
    return { panelFlex: panelFlex ?? {}, splitRatio };
  }

  // A collapsed header occupies fixed space. Resize through it to the nearest
  // open panels on either side, then push farther panels when a minimum is hit.
  const above = order.slice(0, splitIndex + 1).filter((id) => !collapsed[id]).reverse();
  const below = order.slice(splitIndex + 1).filter((id) => !collapsed[id]);
  if (!above.length || !below.length) {
    return { panelFlex: panelFlex ?? {}, splitRatio };
  }

  const flex = resolveStackedPanelFlex({ ...input, order });
  const openIds = openPanelIds(order, collapsed);
  const heights = new Map(openIds.map((id) => [
    id, snapshot?.panelHeights[id] ?? (flex.get(id)! * containerHeight),
  ]));
  const totalHeight = [...heights.values()].reduce((sum, height) => sum + height, 0);
  if (totalHeight <= 0) return { panelFlex: panelFlex ?? {}, splitRatio };
  // In a crowded rail, don't make an already-small panel jump on first movement.
  const minimum = (id: TId) => Math.min(minPanelHeight, heights.get(id)!);
  const donors = deltaPx > 0 ? below : above;
  const receiver = (deltaPx > 0 ? above : below)[0]!;
  let remaining = Math.abs(deltaPx);
  let transferred = 0;
  for (const id of donors) {
    const height = heights.get(id)!;
    const amount = Math.min(remaining, Math.max(0, height - minimum(id)));
    heights.set(id, height - amount);
    remaining -= amount;
    transferred += amount;
  }
  heights.set(receiver, heights.get(receiver)! + transferred);

  // Keep the visible stack's weight sum stable, including in family sub-stacks.
  const totalWeight = openIds.reduce((sum, id) => sum + flexGrowForStackedPanel({ ...input, order }, id), 0);
  const nextPanelFlex: Partial<Record<TId, number>> = { ...panelFlex };
  for (const id of openIds) {
    nextPanelFlex[id] = (heights.get(id)! / totalHeight) * totalWeight;
  }

  const nextSplitRatio =
    openIds.length === 2
      ? (nextPanelFlex[openIds[0]!] ?? splitRatio) /
        ((nextPanelFlex[openIds[0]!] ?? splitRatio) + (nextPanelFlex[openIds[1]!] ?? 1 - splitRatio))
      : splitRatio;

  return { panelFlex: nextPanelFlex, splitRatio: nextSplitRatio };
}
