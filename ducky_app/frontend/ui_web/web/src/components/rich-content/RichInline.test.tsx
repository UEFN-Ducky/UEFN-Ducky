// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RichLink } from "./RichInline";

const isRemote = vi.fn(() => false);
const openHttpsOnThisDevice = vi.fn();

vi.mock("../../hooks/usePanelApi", () => ({
  isRemote: () => isRemote(),
}));

vi.mock("../../remote/openHttps", () => ({
  openHttpsOnThisDevice: (...args: unknown[]) => openHttpsOnThisDevice(...args),
}));

afterEach(cleanup);

describe("RichLink https", () => {
  beforeEach(() => {
    isRemote.mockReturnValue(false);
    openHttpsOnThisDevice.mockReset();
  });

  it("keeps a normal <a> on desktop", () => {
    const { getByRole } = render(<RichLink href="https://example.com/docs">docs</RichLink>);
    const link = getByRole("link", { name: "docs" });
    expect(link.getAttribute("href")).toBe("https://example.com/docs");
    expect(link.getAttribute("target")).toBe("_blank");
    fireEvent.click(link);
    expect(openHttpsOnThisDevice).not.toHaveBeenCalled();
  });

  it("opens https on this device when remote", () => {
    isRemote.mockReturnValue(true);
    const { getByRole } = render(<RichLink href="https://example.com/docs">docs</RichLink>);
    fireEvent.click(getByRole("link", { name: "docs" }));
    expect(openHttpsOnThisDevice).toHaveBeenCalledWith("https://example.com/docs");
  });
});
