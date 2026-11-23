import { describe, expect, it } from "vitest";
import { updateButtonLabel, updateFailureNote, updateTitle, type LinuxUpdateFields } from "./appUpdate";

describe("update banner per install kind", () => {
  const kinds: Array<[string, LinuxUpdateFields, string, string]> = [
    ["windows installer", {}, "Update now", "Update ready"],
    ["appimage", { install_kind: "appimage", update_action: "restart" }, "Restart", "Update ready"],
    ["deb", { install_kind: "deb", update_action: "install" }, "Install", "Update available"],
    ["rpm", { install_kind: "rpm", update_action: "install" }, "Install", "Update available"],
    ["flatpak", { install_kind: "flatpak", update_action: "install" }, "Install", "Update available"],
    ["aur", { install_kind: "aur", update_action: "manual" }, "Update now", "Update available"],
  ];

  for (const [name, status, button, title] of kinds) {
    it(`${name}: ${title} · ${button}`, () => {
      expect(updateButtonLabel(status)).toBe(button);
      expect(updateTitle(status, true)).toBe(title);
    });
  }

  it("a portable copy is only told an update exists", () => {
    expect(updateTitle({}, false)).toBe("Update available");
  });

  it("a rolled-back AppImage update says which version it is back on", () => {
    expect(updateFailureNote({ last_failure: { from: "1.2.700", to: "1.2.699" } })).toBe(
      "Update failed, back on v1.2.699",
    );
    expect(updateFailureNote({})).toBeNull();
  });
});
