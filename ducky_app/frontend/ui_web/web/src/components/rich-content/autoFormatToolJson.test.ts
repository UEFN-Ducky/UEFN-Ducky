import { describe, expect, it } from "vitest";
import { autoFormatToolJson } from "./autoFormatToolJson";

describe("autoFormatToolJson", () => {
  it("formats actor list as table", () => {
    const raw = JSON.stringify({
      ok: true,
      tool: "get_all_actors",
      data: {
        actors: [
          { label: "RR_Wallet", kind: "verse_script", class: "PlayerWallet" },
          { label: "Spawner", kind: "creative_device", class: "Device_Spawner" },
        ],
      },
    });
    const blocks = autoFormatToolJson(raw);
    const table = blocks.find((b) => b.type === "table");
    expect(table?.type).toBe("table");
    if (table?.type === "table") {
      expect(table.headers).toContain("label");
      expect(table.rows).toHaveLength(2);
    }
  });

  it("formats error envelope as callout", () => {
    const raw = JSON.stringify({ ok: false, tool: "ping", error: "Listener offline" });
    const blocks = autoFormatToolJson(raw);
    expect(blocks[0]?.type).toBe("callout");
    if (blocks[0]?.type === "callout") {
      expect(blocks[0].tone).toBe("error");
      expect(blocks[0].text).toContain("offline");
    }
  });

  it("formats flat dict as key_value", () => {
    const raw = JSON.stringify({ ok: true, data: { online: true, port: 7878 } });
    const blocks = autoFormatToolJson(raw);
    expect(blocks.some((b) => b.type === "key_value")).toBe(true);
  });

  it("unwraps result envelope so UI is not a sideways 'result' column", () => {
    const payload = {
      ok: true,
      actor_path: "/Game/Maps/Main.Main:PersistentLevel.GrayBox_Floor_C_1",
      actor_name: "GrayBox_Floor_C_1",
      actor_label: "GrayBox_Floor",
    };
    const raw = JSON.stringify({ result: payload });
    const blocks = autoFormatToolJson(raw);
    expect(blocks.some((b) => b.type === "key_value" && b.pairs.some((p) => p.key === "result"))).toBe(
      false,
    );
    const kv = blocks.find((b) => b.type === "key_value");
    const code = blocks.find((b) => b.type === "code");
    expect(kv || code).toBeTruthy();
    if (kv?.type === "key_value") {
      expect(kv.pairs.some((p) => p.key === "actor_path")).toBe(true);
      expect(kv.pairs.some((p) => p.value.includes("GrayBox_Floor"))).toBe(true);
    }
    if (code?.type === "code") {
      expect(code.text).toContain("GrayBox_Floor");
    }
  });

  it("parses JSON-string result values", () => {
    const raw = JSON.stringify({
      result: JSON.stringify({
        ok: true,
        nested: { a: 1, b: 2, c: 3, d: 4 },
        path: "/Game/Very/Long/Path/That/Exceeds/Eighty/Chars/For/Flat/KeyValue",
      }),
    });
    const blocks = autoFormatToolJson(raw);
    expect(blocks.some((b) => b.type === "key_value" && b.pairs.some((p) => p.key === "result"))).toBe(
      false,
    );
    const code = blocks.find((b) => b.type === "code");
    expect(code?.type).toBe("code");
    if (code?.type === "code") {
      expect(code.text).toContain("Exceeds/Eighty");
      expect(code.text).toContain('"nested"');
    }
  });
});
