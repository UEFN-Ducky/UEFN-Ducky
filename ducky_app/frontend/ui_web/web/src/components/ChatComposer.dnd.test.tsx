// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { attachmentFileUri, dragAttachmentOut } from "./ChatComposer";

function fakeTransfer() {
  const data: Record<string, string> = {};
  return {
    data,
    effectAllowed: "all",
    setData: (type: string, value: string) => {
      data[type] = value;
    },
  } as unknown as DataTransfer & { data: Record<string, string> };
}

describe("composer attachments drag out as text/uri-list", () => {
  it("turns an absolute saved path into a file URI (Linux and Windows)", () => {
    expect(attachmentFileUri({ kind: "image", storedPath: "/home/me/shot 1.png" })).toBe("file:///home/me/shot%201.png");
    expect(attachmentFileUri({ kind: "image", storedPath: "C:\\Users\\me\\a.png" })).toBe("file:///C:/Users/me/a.png");
    expect(attachmentFileUri({ kind: "image", storedPath: "attachments/a.png" })).toBe("");
    expect(attachmentFileUri({ kind: "file" })).toBe("");
  });

  it("puts the dragged thumbnail's file on the drag", () => {
    const img = document.createElement("img");
    img.setAttribute("src", "/media/a.png");
    const dt = fakeTransfer();
    const atts = [{ kind: "image", dataUrl: "/media/a.png", storedPath: "/home/me/a.png" }];
    expect(dragAttachmentOut({ target: img, dataTransfer: dt }, atts)).toBe(true);
    expect(dt.data["text/uri-list"]).toBe("file:///home/me/a.png\r\n");
    expect(dt.effectAllowed).toBe("copy");
  });

  it("leaves pasted images (no file on disk) and other drags alone", () => {
    const img = document.createElement("img");
    img.setAttribute("src", "data:image/png;base64,AAAA");
    const dt = fakeTransfer();
    expect(dragAttachmentOut({ target: img, dataTransfer: dt }, [{ kind: "image", dataUrl: "data:image/png;base64,AAAA" }])).toBe(false);
    expect(dragAttachmentOut({ target: document.createElement("div"), dataTransfer: dt }, [])).toBe(false);
    expect(dt.data).toEqual({});
  });
});
