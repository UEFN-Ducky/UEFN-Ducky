import { describe, expect, it } from "vitest";

/** Mirror of ChoiceDropdown grouping — keep in sync if grouping rules change. */
function groupOptions(
  options: { value: string; label: string; group?: string }[],
): { group: string | null; items: { value: string; label: string; group?: string }[] }[] {
  const order: string[] = [];
  const map = new Map<string, { value: string; label: string; group?: string }[]>();
  for (const opt of options) {
    const g = opt.group ?? "";
    if (!map.has(g)) {
      map.set(g, []);
      order.push(g);
    }
    map.get(g)!.push(opt);
  }
  return order.map((g) => ({ group: g || null, items: map.get(g)! }));
}

describe("ChoiceDropdown option groups", () => {
  it("keeps ungrouped items first then named groups", () => {
    const groups = groupOptions([
      { value: "", label: "None" },
      { value: "a", label: "A", group: "Built-in" },
      { value: "b", label: "B", group: "Built-in" },
      { value: "c", label: "C", group: "Plugins" },
    ]);
    expect(groups.map((g) => g.group)).toEqual([null, "Built-in", "Plugins"]);
    expect(groups[1]!.items.map((i) => i.value)).toEqual(["a", "b"]);
  });
});
