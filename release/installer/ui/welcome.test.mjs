// Setup's Welcome screen: run app.js against a tiny fake page and the WebView2 bridge.
// Run: node --test release/installer/ui/welcome.test.mjs (not shipped: the csproj embeds named files only).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("./app.js", import.meta.url), "utf8");

function element() {
  return {
    textContent: "",
    value: "",
    checked: true,
    disabled: false,
    title: "",
    style: {},
    dataset: {},
    classList: { toggle() {} },
    setAttribute() {},
    addEventListener() {},
    click() {},
    closest() { return null; },
  };
}

function openSetup(init) {
  const els = {};
  const sent = [];
  let onMessage = null;
  const document = {
    title: "",
    getElementById: (id) => (els[id] ||= element()),
    querySelectorAll: () => [],
    querySelector: () => element(),
    addEventListener() {},
  };
  const window = {
    chrome: {
      webview: {
        postMessage: (msg) => sent.push(msg),
        addEventListener: (_type, fn) => { onMessage = fn; },
      },
    },
  };
  vm.runInNewContext(source, { window, document });
  onMessage({ data: { type: "init", version: "1.2.488", license: "", ...init } });
  return { els, document, sent };
}

test("Ducky was here before: Welcome offers Repair and says chats stay", () => {
  const { els, document, sent } = openSetup({ hadDucky: true });
  assert.equal(els["welcome-title"].textContent, "Repair UEFN Ducky");
  assert.match(els["welcome-tagline"].textContent, /Repair keeps your chats and settings/);
  assert.equal(els["install"].textContent, "Repair");
  assert.equal(document.title, "Repair UEFN Ducky");
  assert.ok(!sent.some((m) => m.type === "startInstall"), "waits on Welcome for the Repair click");
});

test("installed version is named when Setup knows it", () => {
  const { els } = openSetup({ hadDucky: true, previousVersion: "1.2.480" });
  assert.equal(els["welcome-tagline"].textContent, "UEFN Ducky v1.2.480 is installed. Repair keeps your chats and settings.");
});

test("a new PC keeps the plain Install welcome", () => {
  const { document } = openSetup({});
  assert.equal(document.getElementById("welcome-title").textContent, "");
  assert.equal(document.getElementById("install").textContent, "");
  assert.equal(document.title, "UEFN Ducky Setup");
});

test("an update still starts by itself", () => {
  const { sent, document } = openSetup({ isUpgrade: true, hadDucky: true, dir: "C:\\Ducky" });
  assert.ok(sent.some((m) => m.type === "startInstall"));
  assert.equal(document.title, "Updating UEFN Ducky");
});
