/* Runs inside a plugin page the phone panel built over the PC link (src/plugin-ui/remotePluginPage.ts).
 *
 * The frame is sandboxed (an opaque origin) under the site's CSP: it runs scripts from the
 * site or blob: URLs it made itself, nothing inline. So each of the page's scripts is this
 * file with its code in data-dk-run: the code becomes a blob: URL made here and is
 * document.written in place, keeping script order and timing.
 *
 * The data-dk-boot copy, first in <head>, sends the page's later loads of its own files
 * (fetch, <img>/<video>/<audio>/<source> src, stylesheet links, worklet modules) to the
 * desktop over the host bridge (desktop.file): a URL under this file's folder +
 * "plugin-ui/" is a desktop path (the page's <base> sits there).
 */
(function () {
  "use strict";
  var me = document.currentScript;
  if (!me) return;

  var code = me.getAttribute("data-dk-run");
  if (code !== null) {
    var url = URL.createObjectURL(new Blob([code], { type: "text/javascript" }));
    var module = me.hasAttribute("data-dk-module");
    if (document.readyState === "loading") {
      var attrs = (module ? ' type="module"' : "") + (me.hasAttribute("data-dk-defer") ? " defer" : "");
      document.write("<script" + attrs + ' src="' + url + '"></scr' + "ipt>");
    } else {
      var s = document.createElement("script");
      if (module) s.type = "module";
      s.src = url;
      me.after(s);
    }
    return;
  }
  if (!me.hasAttribute("data-dk-boot") || window.__dkFrame) return;
  window.__dkFrame = true;

  var CHANNEL = "uefn-plugin-ui";
  var panelBase = new URL(".", me.src).href;
  var prefix = panelBase + "plugin-ui/";
  var seq = 0;
  var waiting = {};

  window.addEventListener("message", function (e) {
    var d = e.data;
    if (!d || d.channel !== CHANNEL || !d.id || !waiting[d.id]) return;
    var w = waiting[d.id];
    delete waiting[d.id];
    if (d.ok) w[0](d.result);
    else w[1](new Error(d.error || "bridge error"));
  });

  /** The desktop path a URL of the page points to, or "" when it is not one of its files. */
  function desktopPath(ref) {
    var u;
    try {
      u = new URL(String(ref), document.baseURI);
    } catch (_e) {
      return "";
    }
    return u.href.indexOf(prefix) === 0 ? "/" + u.href.slice(panelBase.length).split("#")[0] : "";
  }

  function fromDesktop(path) {
    return new Promise(function (resolve, reject) {
      seq += 1;
      var id = "dk-file-" + seq;
      waiting[id] = [resolve, reject];
      parent.postMessage({ channel: CHANNEL, id: id, method: "desktop.file", params: { path: path } }, "*");
    }).then(function (r) {
      var status = r.status >= 200 && r.status <= 599 ? r.status : 502;
      var empty = status === 204 || status === 205 || status === 304;
      return new Response(empty ? null : r.body, {
        status: status,
        headers: { "Content-Type": r.type || "application/octet-stream" },
      });
    });
  }

  var realFetch = window.fetch;
  window.fetch = function (input, init) {
    var isReq = typeof Request !== "undefined" && input instanceof Request;
    var method = String((init && init.method) || (isReq ? input.method : "GET")).toUpperCase();
    var path = method === "GET" ? desktopPath(isReq ? input.url : input) : "";
    return path ? fromDesktop(path) : realFetch.apply(this, arguments);
  };

  function localUrl(path, type) {
    return fromDesktop(path)
      .then(function (r) {
        return r.ok ? r.blob() : null;
      })
      .then(function (b) {
        return b ? URL.createObjectURL(type ? new Blob([b], { type: type }) : b) : null;
      });
  }

  if (window.Worklet && Worklet.prototype.addModule) {
    var addModule = Worklet.prototype.addModule;
    Worklet.prototype.addModule = function (url, options) {
      var self = this;
      var path = desktopPath(url);
      if (!path) return addModule.call(this, url, options);
      return localUrl(path, "text/javascript").then(function (u) {
        return addModule.call(self, u || url, options);
      });
    };
  }

  var MEDIA = { IMG: "src", SOURCE: "src", VIDEO: "src", AUDIO: "src", TRACK: "src" };

  function fixMedia(el, attr) {
    var value = el.getAttribute(attr);
    var path = value ? desktopPath(value) : "";
    if (!path) return;
    localUrl(path).then(function (u) {
      if (u && el.getAttribute(attr) === value) el.setAttribute(attr, u);
    });
  }

  function fixLink(el) {
    if (!/(^|\s)stylesheet(\s|$)/i.test(el.rel || "")) return;
    var path = desktopPath(el.getAttribute("href") || "");
    if (!path) return;
    fromDesktop(path)
      .then(function (r) {
        return r.ok ? r.text() : null;
      })
      .then(function (css) {
        if (css === null || !el.parentNode) return;
        var style = document.createElement("style");
        if (el.media) style.media = el.media;
        style.textContent = css;
        el.replaceWith(style);
      });
  }

  function fixOne(el) {
    if (MEDIA[el.tagName]) fixMedia(el, MEDIA[el.tagName]);
    if (el.tagName === "VIDEO") fixMedia(el, "poster");
    if (el.tagName === "LINK") fixLink(el);
  }

  function fix(el) {
    if (el.nodeType !== 1) return;
    fixOne(el);
    var inner = el.querySelectorAll("img,source,video,audio,track,link");
    for (var i = 0; i < inner.length; i++) fixOne(inner[i]);
  }

  new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      var r = records[i];
      if (r.type === "attributes") fix(r.target);
      else for (var j = 0; j < r.addedNodes.length; j++) fix(r.addedNodes[j]);
    }
  }).observe(document, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["src", "href", "poster"],
  });
})();
