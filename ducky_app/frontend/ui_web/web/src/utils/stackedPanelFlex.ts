export type StackedPanelFlexInput<TId extends string> = {
  order: TId[];
  collapsed: Record<TId, boolean>;
  panelFlex?: Partial<Record<TId, number>>;
  splitRatio: number;
  minPanelHeight: number;
};

function openPanelIds<TId extends string>(order: TId[], collapsed: Record<TId, boolean>): TId[] {
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
): { panelFlex: Partial<Record<TId, number>>; splitRatio: number } {
  const { order, collapsed, panelFlex, splitRatio, minPanelHeight } = input;
  if (containerHeight <= 0 || deltaPx === 0) {
    return { panelFlex: panelFlex ?? {}, splitRatio };
  }

  const deltaRatio = deltaPx / containerHeight;
  const topId = order[splitIndex];
  const bottomId = order[splitIndex + 1];
  if (!topId || !bottomId || collapsed[topId] || collapsed[bottomId]) {
    return { panelFlex: panelFlex ?? {}, splitRatio };
  }

  const flex = resolveStackedPanelFlex(input);
  const openIds = openPanelIds(order, collapsed);
  const totalWeight = openIds.reduce((sum, id) => {
    const stored = panelFlex?.[id];
    if (typeof stored === "number" && Number.isFinite(stored) && stored > 0) return sum + stored;
    return sum + (flex.get(id) ?? 0);
  }, 0);
  if (totalWeight <= 0) return { panelFlex: panelFlex ?? {}, splitRatio };

  const deltaWeight = deltaRatio * totalWeight;
  let topFlex = (panelFlex?.[topId] ?? flex.get(topId)!) + deltaWeight;
  let bottomFlex = (panelFlex?.[bottomId] ?? flex.get(bottomId)!) - deltaWeight;

  const minWeight = (minPanelHeight / containerHeight) * totalWeight;
  topFlex = Math.max(minWeight, topFlex);
  bottomFlex = Math.max(minWeight, bottomFlex);

  const nextPanelFlex: Partial<Record<TId, number>> = { ...panelFlex };
  for (const id of openIds) {
    if (nextPanelFlex[id] == null) nextPanelFlex[id] = flex.get(id) ?? 1;
  }
  nextPanelFlex[topId] = topFlex;
  nextPanelFlex[bottomId] = bottomFlex;

  const nextSplitRatio =
    openIds.length === 2
      ? (nextPanelFlex[openIds[0]!] ?? splitRatio) /
        ((nextPanelFlex[openIds[0]!] ?? splitRatio) + (nextPanelFlex[openIds[1]!] ?? 1 - splitRatio))
      : splitRatio;

  return { panelFlex: nextPanelFlex, splitRatio: nextSplitRatio };
}
