// window.pywebview.api for Ducky's page inside the native launcher's WebView2.
// Injected with AddScriptToExecuteOnDocumentCreated, before any page script runs.
(function () {
  "use strict";
  if (window.__duckyShim) return;
  var host = window.chrome && window.chrome.webview;
  if (!host) return;

  var pending = new Map();
  var seq = 0;
  var names = null; // optional allow-list sent by the launcher ({t:"names"})

  function call(name, args) {
    return new Promise(function (resolve, reject) {
      var id = "js-" + ++seq;
      pending.set(id, { resolve: resolve, reject: reject });
      try {
        host.postMessage(JSON.stringify({ t: "call", id: id, name: name, args: args }));
      } catch (err) {
        pending.delete(id);
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
  }

  function settle(msg) {
    var p = pending.get(msg.id);
    if (!p) return;
    pending.delete(msg.id);
    if (msg.ok) {
      p.resolve(msg.value === undefined ? null : msg.value);
    } else {
      var err = new Error(String(msg.error == null ? "call failed" : msg.error));
      err.name = "PyWebviewApiError";
      p.reject(err);
    }
  }

  host.addEventListener("message", function (ev) {
    var msg = ev.data;
    if (typeof msg === "string") {
      try { msg = JSON.parse(msg); } catch (_) { return; }
    }
    if (!msg || typeof msg !== "object") return;
    if (msg.t === "result") settle(msg);
    else if (msg.t === "names" && Array.isArray(msg.names)) names = new Set(msg.names);
    else if (msg.t === "event" && typeof msg.name === "string") {
      var ev2 = new CustomEvent("ducky-native-" + msg.name, { detail: msg.data || {}, cancelable: true });
      var kept = window.dispatchEvent(ev2);
      // Start-up values came from the launcher's cache and Python now says otherwise. The page
      // has no re-fetch path for them, so load it once more (live this time) unless a
      // listener handled it with preventDefault().
      if (msg.name === "startup-refresh" && kept) location.reload();
    }
  });

  var fnCache = Object.create(null);
  var api = new Proxy(Object.create(null), {
    get: function (_t, name) {
      // Never look like a thenable or a primitive, or `await api` would hang.
      if (typeof name !== "string" || name === "then" || name === "toJSON") return undefined;
      if (names && !names.has(name)) return undefined;
      var fn = fnCache[name];
      if (!fn) {
        fn = function () { return call(name, Array.prototype.slice.call(arguments)); };
        fnCache[name] = fn;
      }
      return fn;
    },
    has: function (_t, name) {
      return typeof name === "string" && (!names || names.has(name));
    },
    ownKeys: function () { return names ? Array.from(names) : []; },
    getOwnPropertyDescriptor: function (_t, name) {
      if (!names || !names.has(name)) return undefined;
      return { configurable: true, enumerable: true, writable: false, value: api[name] };
    },
    set: function () { return false; },
  });

  window.__duckyShim = { settle: settle, pendingCount: function () { return pending.size; } };
  window.pywebview = window.pywebview || {};
  window.pywebview.api = api;
  window.pywebview.platform = "edgechromium";
  window.pywebview.native = true;

  function ready() { window.dispatchEvent(new CustomEvent("pywebviewready")); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ready, { once: true });
  } else {
    setTimeout(ready, 0);
  }
})();
