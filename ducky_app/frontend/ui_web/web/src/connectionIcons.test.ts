import { describe, expect, it } from "vitest";
import { connectionIconSrc, connectionModeFromFlags } from "./connectionIcons";

describe("connectionModeFromFlags", () => {
  it("prefers wedged over online when both are set", () => {
    expect(connectionModeFromFlags(true, true)).toBe("wedged");
    expect(connectionIconSrc(true, true)).toBe("./WedgedMCPIcon.png");
  });

  it("reports online when reachable and not wedged", () => {
    expect(connectionModeFromFlags(true, false)).toBe("online");
    expect(connectionIconSrc(true, false)).toBe("./OnlineMCPIcon.png");
  });

  it("reports offline when unreachable", () => {
    expect(connectionModeFromFlags(false, false)).toBe("offline");
    expect(connectionIconSrc(false, false)).toBe("./OfflineMCPIcon.png");
  });
});
