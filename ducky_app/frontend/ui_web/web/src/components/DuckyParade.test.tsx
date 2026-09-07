// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { BUNDLED_DUCKIES } from "../generated/bundledDuckies";
import { DuckyParade, DuckyParadeOverlay } from "./DuckyParade";

describe("DuckyParade", () => {
  afterEach(() => cleanup());

  it("marches the bundled duck images in family order", () => {
    const { container } = render(<DuckyParade label="Loading" />);
    const imgs = container.querySelectorAll<HTMLImageElement>(".ducky-parade__duck");
    expect(imgs.length).toBe(4);
    expect([...imgs].map((img) => img.getAttribute("src"))).toEqual(
      BUNDLED_DUCKIES.slice(0, 4).map((d) => d.url),
    );
    expect(container.querySelector("[data-ducky-parade]")?.getAttribute("aria-label")).toBe("Loading");
  });

  it("covers a pane when overlay cover is set", () => {
    const { container } = render(<DuckyParadeOverlay cover />);
    expect(container.querySelector(".ducky-parade-overlay--cover")).not.toBeNull();
    expect(container.querySelectorAll(".ducky-parade__duck").length).toBe(4);
  });
});
