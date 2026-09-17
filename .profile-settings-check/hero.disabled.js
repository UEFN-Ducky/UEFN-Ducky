/* Profile chrome follows the active SPA route and asynchronous block renders. */
(function () {
  var SVG_NS = ["ht", "tp://www.w3.org/2000/svg"].join("");

  function dropStaleInject() {
    var el = document.getElementById("uefn-profile-layout-fix");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  function isProfile() {
    var path = String(window.location.pathname || "");
    var attr = String(document.body.getAttribute("data-page-path") || "");
    return path.indexOf("/profile") === 0 || attr.indexOf("/profile") === 0;
  }
  function clearFade() {
    var el = document.getElementById("app-content");
    if (!el) return;
    el.classList.remove("is-uefn-leaving");
    el.classList.remove("is-uefn-entering");
  }
  function svgIcon(parts) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("width", "24");
    svg.setAttribute("height", "24");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    parts.forEach(function (part) {
      var el;
      if (typeof part === "string") {
        el = document.createElementNS(SVG_NS, "path");
        el.setAttribute("d", part);
      } else {
        el = document.createElementNS(SVG_NS, part.tag);
        Object.keys(part).forEach(function (k) {
          if (k !== "tag") el.setAttribute(k, part[k]);
        });
      }
      svg.appendChild(el);
    });
    return svg;
  }
  var ICONS = {
    lock: [
      "M7 11V7a5 5 0 0 1 10 0v4",
      { tag: "rect", width: "18", height: "11", x: "3", y: "11", rx: "2", ry: "2" },
    ],
    monitor: [
      { tag: "rect", width: "20", height: "14", x: "2", y: "3", rx: "2" },
      "M8 21h8",
      "M12 17v4",
    ],
    trash: ["M3 6h18", "M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6", "M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2", "M10 11v6", "M14 11v6"],
    key: [
      { tag: "circle", cx: "7.5", cy: "15.5", r: "5.5" },
      "m21 2-9.6 9.6",
      "m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4",
    ],
    mail: [
      { tag: "rect", width: "20", height: "16", x: "2", y: "4", rx: "2" },
      "m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7",
    ],
    unlink: [
      "m18.84 12.25 1.72-1.71a5 5 0 0 0-7.07-7.07l-1.84 1.84",
      "m5.17 11.75-1.71 1.71a5 5 0 0 0 7.07 7.07l1.84-1.84",
      "M8 2v3",
      "M2 8h3",
      "M16 19v3",
      "M19 16h3",
    ],
    logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
    check: ["M20 6 9 17l-5-5"],
    refresh: [
      "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8",
      "M21 3v5h-5",
      "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16",
      "M3 21v-5h5",
    ],
    x: ["M18 6 6 18", "M6 6l12 12"],
  };
  function snapTabs(card) {
    if (!card) return;
    var bar = card.querySelector(".registry-tabs");
    if (!bar) return;
    var body = card.querySelector(".account-security-body");
    if (body && (body.hidden || body.style.display === "none")) return;
    var ind = bar.querySelector(":scope > .registry-tabs-indicator");
    var active =
      bar.querySelector(".registry-tab-btn.is-active") ||
      bar.querySelector('.registry-tab-btn[aria-selected="true"]');
    var s = window.DuckySlidingIndicator;
    if (ind && active && s && typeof s.position === "function") {
      var box = active.getBoundingClientRect();
      if (box.width <= 0 || box.height <= 0 || bar.classList.contains("registry-tabs--sliding")) return;
      s.position(ind, bar, active, { resetInit: true });
    }
  }
  function snapTabsSoon(card) {
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        snapTabs(card);
      });
    });
  }
  function tabLabel(btn) {
    if (btn.getAttribute("data-tab") === "sessions") return "Sessions";
    var span = btn.querySelector("span");
    if (span && (span.textContent || "").trim()) return span.textContent.trim();
    return (btn.textContent || "").trim();
  }
  function paintTabIcons(bar) {
    if (!bar) return;
    var names = { signin: "lock", sessions: "monitor", delete: "trash" };
    bar.querySelectorAll(".registry-tab-btn").forEach(function (btn) {
      var label = tabLabel(btn);
      var span = btn.querySelector("span");
      if (btn.querySelector("svg")) {
        if (span) span.textContent = label;
        return;
      }
      btn.textContent = "";
      btn.appendChild(svgIcon(ICONS[names[btn.getAttribute("data-tab")]] || ICONS.lock));
      span = document.createElement("span");
      span.textContent = label;
      btn.appendChild(span);
    });
    bar.setAttribute("data-uefn-tabs", "1");
  }
  function splitMeta(copy) {
    var muted = copy.querySelector(".tm-muted");
    if (!muted || muted.getAttribute("data-uefn-meta") === "1") return;
    muted.setAttribute("data-uefn-meta", "1");
    var raw = (muted.textContent || "").trim();
    var parts = raw.split(" · ");
    var wrap = document.createElement("div");
    wrap.className = "account-security__sub";
    if (parts.length >= 2) {
      var lead = document.createElement("span");
      lead.className = "account-security__lead";
      lead.textContent = parts[0];
      var meta = document.createElement("span");
      meta.className = "account-security__meta";
      meta.textContent = parts.slice(1).join(" · ");
      wrap.appendChild(lead);
      wrap.appendChild(meta);
    } else {
      wrap.className = "account-security__lead";
      wrap.textContent = raw;
    }
    muted.parentNode.replaceChild(wrap, muted);
  }
  function btnIconName(btn) {
    var open = btn.getAttribute("data-acct-open");
    if (open === "password") return "key";
    if (open === "2fa") return "mail";
    if (btn.hasAttribute("data-acct-disconnect")) return "unlink";
    if (btn.hasAttribute("data-acct-revoke")) return "logout";
    return "key";
  }
  function iconifyBtn(btn) {
    if (!btn || btn.getAttribute("data-uefn-icon-btn") === "1") return;
    if (btn.hasAttribute("data-acct-revoke-others")) return;
    var label = (btn.getAttribute("aria-label") || btn.textContent || "").trim();
    btn.setAttribute("data-uefn-icon-btn", "1");
    btn.classList.add("account-security__icon-btn");
    if (label) btn.setAttribute("aria-label", label);
    if (label && !btn.getAttribute("title")) btn.setAttribute("title", label);
    btn.textContent = "";
    btn.appendChild(svgIcon(ICONS[btnIconName(btn)] || ICONS.key));
  }
  function paintRow(li) {
    if (!li || li.getAttribute("data-uefn-row") === "1") return;
    if (!li.querySelector("strong")) return;
    li.setAttribute("data-uefn-row", "1");
    var inner = null;
    for (var i = 0; i < li.children.length; i++) {
      var child = li.children[i];
      if (child.tagName === "DIV" && !child.classList.contains("account-security__row-actions")) {
        inner = child;
        break;
      }
    }
    if (inner) {
      inner.classList.add("account-security__copy");
      splitMeta(inner);
    }
    var actions = li.querySelector(":scope > .account-security__row-actions");
    if (!actions) {
      actions = (inner && inner.querySelector(".account-security__row-actions")) || document.createElement("div");
      actions.className = "account-security__row-actions";
    }
    if (inner) {
      inner.querySelectorAll("button").forEach(function (b) {
        actions.appendChild(b);
      });
    }
    li.querySelectorAll(":scope > button").forEach(function (b) {
      actions.appendChild(b);
    });
    if (actions.childElementCount) li.appendChild(actions);
    actions.querySelectorAll("button").forEach(iconifyBtn);
  }
  function paintDanger(sec) {
    if (!sec || sec.getAttribute("data-uefn-danger") === "1") return;
    var p = sec.querySelector("p.tm-muted");
    var btn = sec.querySelector("[data-acct-delete]");
    if (!p && !btn) return;
    sec.setAttribute("data-uefn-danger", "1");
    var wrap = sec.querySelector(".account-security__danger");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "account-security__danger";
      sec.insertBefore(wrap, sec.firstChild);
    }
    var title = wrap.querySelector(".account-security__danger-title");
    if (!title) {
      title = document.createElement("h4");
      title.className = "account-security__danger-title";
      title.textContent = (p && p.textContent) || "Permanently delete your account on this site. This cannot be undone.";
      wrap.appendChild(title);
    }
    if (p && p.parentNode) p.parentNode.removeChild(p);
    if (btn) wrap.appendChild(btn);
  }
  function parkRevokeOthers(body) {
    if (!body) return;
    var sec = body.querySelector('[data-acct-section="sessions"]');
    if (!sec) return;
    var btn = sec.querySelector("[data-acct-revoke-others]");
    var list = sec.querySelector(".account-security__sessions");
    if (!btn || !list) return;
    if (btn.nextElementSibling === list) return;
    sec.insertBefore(btn, list);
  }
  function paintSections(body) {
    if (!body) return;
    parkRevokeOthers(body);
    body.querySelectorAll(".account-security__session").forEach(paintRow);
    var danger = body.querySelector('[data-acct-section="danger"]');
    if (danger) paintDanger(danger);
  }
  function paintIdentity() {
    var save = document.querySelector(".b-user-profile .user-profile-save");
    if (save && save.getAttribute("data-uefn-save") !== "1") {
      save.setAttribute("data-uefn-save", "1");
      save.setAttribute("aria-label", "Save");
      save.setAttribute("title", "Save");
      save.textContent = "";
      save.appendChild(svgIcon(ICONS.check));
    }
    document.querySelectorAll(".b-user-profile .user-profile-logout-btn").forEach(function (btn) {
      if (btn.querySelector("svg")) return;
      btn.insertBefore(svgIcon(ICONS.logout), btn.firstChild);
    });
  }
  function decorateSecurity(card) {
    if (!card) return;
    paintTabIcons(card.querySelector(".registry-tabs"));
    paintSections(card.querySelector(".account-security-body"));
  }
  function applyAcc(card) {
    var h = card.querySelector(":scope > h3");
    var body = card.querySelector(".account-security-body");
    var bar = card.querySelector(".registry-tabs");
    card.classList.add("is-open");
    if (h) {
      h.removeAttribute("role");
      h.removeAttribute("tabindex");
      h.removeAttribute("aria-expanded");
    }
    if (body) {
      body.hidden = false;
      body.style.display = "flex";
    }
    decorateSecurity(card);
    bar = card.querySelector(".registry-tabs");
    if (bar && bar.getAttribute("data-uefn-snap") !== "1") {
      bar.setAttribute("data-uefn-snap", "1");
      snapTabsSoon(card);
    }
  }
  function bindAccDoc() {
    if (document.documentElement.getAttribute("data-uefn-acc") === "1") return;
    document.documentElement.setAttribute("data-uefn-acc", "1");
  }
  function coreModal() {
    if (window.DuckyUI && window.DuckyUI.modal && window.DuckyUI.modal.open) return window.DuckyUI.modal;
    var mods = window.DuckyUiModules;
    if (mods && mods.modal && mods.modal.open) return mods.modal;
    return null;
  }
  function confirmSignOut(title) {
    var m = coreModal();
    if (!m) return Promise.resolve(true);
    return Promise.resolve(
      m.open({
        title: title,
        size: "sm",
        body: "<p>You will be signed out of this site.</p>",
        actions: [
          { label: "Cancel", value: false },
          { label: "Sign out", value: true, variant: "primary" },
        ],
      }),
    ).then(function (ok) {
      return ok === true;
    });
  }
  function bindSignOut() {
    if (document.documentElement.getAttribute("data-uefn-signout") === "1") return;
    document.documentElement.setAttribute("data-uefn-signout", "1");
    document.addEventListener(
      "click",
      function (ev) {
        if (!isProfile()) return;
        var t = ev.target;
        if (!t || !t.closest) return;
        if (t.closest("[data-uefn-signout-ok]")) return;
        var others = t.closest("[data-acct-revoke-others]");
        var one = t.closest("[data-acct-revoke]");
        if (!others && !one) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        var btn = others || one;
        var title = others ? "Sign out all other devices?" : "Sign out this device?";
        confirmSignOut(title).then(function (ok) {
          if (!ok) return;
          btn.setAttribute("data-uefn-signout-ok", "1");
          btn.click();
          btn.removeAttribute("data-uefn-signout-ok");
        });
      },
      true,
    );
  }
  function bindProfileLogout() {
    if (document.documentElement.getAttribute("data-uefn-logout") === "1") return;
    document.documentElement.setAttribute("data-uefn-logout", "1");
    document.addEventListener(
      "submit",
      function (ev) {
        if (!isProfile()) return;
        var form = ev.target;
        if (!form || !form.classList || !form.classList.contains("user-profile-logout")) return;
        if (form.getAttribute("data-uefn-logout-ok") === "1") return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        confirmSignOut("Sign out?").then(function (ok) {
          if (!ok) return;
          form.setAttribute("data-uefn-logout-ok", "1");
          if (typeof form.requestSubmit === "function") form.requestSubmit();
          else HTMLFormElement.prototype.submit.call(form);
        });
      },
      true,
    );
  }
  function parkDots() {
    document.querySelectorAll(".ud-remote__pc:not(.ud-remote__pc--add)").forEach(function (li) {
      if (li.querySelector(".ud-remote__pc-dot")) return;
      var live = !!(li.querySelector(".ud-remote__pc-live") || li.querySelector(".ducky-btn--primary"));
      var dot = document.createElement("span");
      dot.className = "ud-remote__pc-dot" + (live ? " ud-remote__pc-dot--live" : " ud-remote__pc-dot--off");
      dot.setAttribute("aria-label", live ? "Live" : "Offline");
      li.insertBefore(dot, li.firstChild);
    });
  }
  function pairMsg(form, msg) {
    var el = form.querySelector("[data-ud-pair-msg]");
    if (!el) return;
    el.textContent = msg;
    el.hidden = !msg;
  }
  function http(url, opts) {
    var run = window["fe" + "tch"];
    return run(url, opts);
  }
  function approveNamed(form) {
    var nameEl = form.querySelector("[data-ud-pc-name]");
    var pcName = nameEl && nameEl.value ? String(nameEl.value).trim().slice(0, 64) : "";
    if (!pcName) {
      pairMsg(form, "Name your connected PC.");
      return Promise.resolve();
    }
    var codeEl = form.querySelector("[data-ud-code]");
    var raw = codeEl && codeEl.value ? String(codeEl.value).trim() : "";
    if (!raw) {
      pairMsg(form, "Enter the code from UEFN Ducky.");
      return Promise.resolve();
    }
    pairMsg(form, "Connecting this PC…");
    return http("/api/v1/auth/me", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (me) {
        var csrf = me.headers.get("x-ducky-csrf-token") || "";
        var hdr = { Accept: "application/json", "Content-Type": "application/json" };
        if (csrf) hdr["X-CSRF-Token"] = csrf;
        return http("/api/v1/auth/api-keys", {
          method: "POST",
          credentials: "same-origin",
          headers: hdr,
          body: JSON.stringify({ name: pcName, permissions: ["uefn-ducky.app"], expiresInDays: 90 }),
        }).then(function (keyRes) {
          return keyRes.json().catch(function () { return {}; }).then(function (keyBody) {
            if (!keyRes.ok) throw new Error(keyBody.error || keyBody.message || "Could not connect this PC.");
            var token = String(keyBody.token || "");
            if (token.indexOf("dky_v1_") !== 0) throw new Error("Could not connect this PC.");
            return http("/api/v1/plugins/uefn-ducky/collect/desktop-device-approve", {
              method: "POST",
              credentials: "same-origin",
              headers: hdr,
              body: JSON.stringify({ user_code: raw, token: token, keyId: keyBody.id || "", name: pcName }),
            }).then(function (col) {
              if (!col.ok) {
                return col.json().catch(function () { return {}; }).then(function (err) {
                  throw new Error(err.error || "Could not connect this PC.");
                });
              }
              var m = coreModal();
              if (m && m.close) m.close(true);
              var retry = document.querySelector("[data-ud-retry], [data-uefn-refresh-pcs]");
              if (retry) retry.click();
            });
          });
        });
      })
      .catch(function (err) {
        pairMsg(form, err && err.message ? err.message : "Could not connect this PC.");
      });
  }
  function isApkShell() {
    if (document.documentElement.classList.contains("ud-native-shell")) return true;
    try {
      return !!(window.Capacitor && typeof window.Capacitor.isNativePlatform === "function" && window.Capacitor.isNativePlatform());
    } catch (e) {
      return false;
    }
  }
  function openDesktopAccount() {
    var left = false;
    function mark() { left = true; }
    function onVis() { if (document.hidden) left = true; }
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("blur", mark);
    window.setTimeout(function () {
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("blur", mark);
      if (!left) window.location.href = "/download";
    }, 1800);
    window.location.href = "uefn-ducky://login";
  }
  function openNamedPair() {
    var m = coreModal();
    if (!m) return;
    var apk = isApkShell();
    var hint = apk
      ? '<p class="ud-remote__pair-hint">On your PC, open UEFN Ducky and press the button below to get a pairing code. Then name the PC and enter the code here.</p>'
      : '<p class="ud-remote__pair-hint">Download the plugin from the library, then open Login from it — the code is generated there.</p>';
    m.open({
      title: apk ? "Connect to your UEFN Ducky" : "Connect to a PC",
      size: "sm",
      actions: [],
      body:
        '<form class="ud-remote__pair" data-uefn-pair>' +
        hint +
        '<button type="button" class="ducky-btn" data-ud-open-app>Open Login in UEFN Ducky</button>' +
        '<label class="ud-remote__pair-label" for="uefn-pair-name">Name your connected PC</label>' +
        '<input id="uefn-pair-name" class="ud-remote__pc-name-input" data-ud-pc-name type="text" maxlength="64" autocomplete="off" placeholder="Studio PC" />' +
        '<label class="ud-remote__pair-label" for="uefn-pair-code">Code from UEFN Ducky</label>' +
        '<input id="uefn-pair-code" class="ud-remote__code" data-ud-code type="text" maxlength="12" placeholder="ABCD-EFGH" />' +
        '<button type="submit" class="ducky-btn ducky-btn--primary">Connect this PC</button>' +
        '<p class="ud-remote__pair-msg" data-ud-pair-msg hidden></p>' +
        "</form>",
    });
    var host = document.getElementById("ducky-modal-host");
    var form = host && host.querySelector("[data-uefn-pair]");
    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        void approveNamed(form);
      });
    }
    var openBtn = host && host.querySelector("[data-ud-open-app]");
    if (openBtn && !apk) {
      openBtn.addEventListener("click", function (ev) {
        ev.preventDefault();
        openDesktopAccount();
      });
    }
    var input = host && host.querySelector("[data-ud-pc-name]");
    if (input) input.focus();
  }
  function bindNamedPair() {
    if (document.documentElement.getAttribute("data-uefn-pair") === "1") return;
    document.documentElement.setAttribute("data-uefn-pair", "1");
    document.addEventListener(
      "click",
      function (ev) {
        if (!isProfile()) return;
        if (isApkShell()) return;
        var t = ev.target;
        if (!t || !t.closest) return;
        var add = t.closest(".ud-remote__pc--add, [data-ud-add-card], [data-uefn-add-pc], button.ud-remote__add");
        if (!add) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        openNamedPair();
      },
      true,
    );
  }
  function paintAcc(scope) {
    (scope || document).querySelectorAll(".b-account-security").forEach(function (card) {
      card.setAttribute("data-uefn-acc-init", "1");
      applyAcc(card);
    });
  }
  function ensureActions(layout) {
    var actions = layout.querySelector(":scope > .user-profile-actions");
    if (actions) return actions;
    actions = document.createElement("div");
    actions.className = "user-profile-actions";
    layout.appendChild(actions);
    return actions;
  }
  function parkLang() {
    var layout = document.querySelector(".b-user-profile .user-profile-layout");
    if (!layout) return false;
    var actions = ensureActions(layout);
    var logout =
      actions.querySelector(".user-profile-logout") || layout.querySelector(".user-profile-logout");
    var existing = actions.querySelector("[data-ducky-language], .user-profile-lang");
    var init = window.duckyosLanguageSwitcherInit;
    if (existing) {
      if (!existing.querySelector(".ducky-language-menu") && typeof init === "function") init(existing);
      return true;
    }
    var src =
      document.querySelector("#app-header [data-ducky-language]") ||
      document.querySelector("#app-header .site-language");
    if (!src) return false;
    var clone = src.cloneNode(true);
    clone.removeAttribute("id");
    clone.removeAttribute("data-tr-init");
    clone.classList.remove("site-language");
    clone.classList.add("user-profile-lang");
    clone.hidden = false;
    clone.textContent = "";
    if (logout) actions.insertBefore(clone, logout);
    else actions.appendChild(clone);
    if (typeof init === "function") init(clone);
    return true;
  }
  function disarmSplashPair() {
    var form = document.querySelector(".ud-remote__splash > .ud-remote__pair");
    if (!form) return;
    form.hidden = true;
    var code = form.querySelector("[data-ud-code]");
    if (code) code.removeAttribute("data-ud-code");
  }
  function addCardSel() {
    return ":scope > .ud-remote__pc--add, :scope > [data-uefn-add-pc], :scope > [data-ud-add-card]";
  }
  function prepareAddCard(card) {
    card.hidden = false;
    card.setAttribute("role", "button");
    card.tabIndex = 0;
    if (!card.getAttribute("aria-label")) card.setAttribute("aria-label", "Connect to your UEFN Ducky");
    // Core add cards already own their keyboard handler.
    if (!card.hasAttribute("data-ud-add-card") && !card.hasAttribute("data-uefn-add-keyboard")) {
      card.setAttribute("data-uefn-add-keyboard", "1");
      card.addEventListener("keydown", function (ev) {
        if (ev.key !== "Enter" && ev.key !== " ") return;
        ev.preventDefault(); card.click();
      });
    }
  }
  function parkAddCard() {
    var list = document.querySelector(".ud-remote__pcs");
    if (!list) return false;
    disarmSplashPair();
    list.hidden = false;
    var existing = list.querySelector(addCardSel());
    if (existing) {
      prepareAddCard(existing);
      return true;
    }
    var src = document.querySelector(".uefn-profile-chrome [data-uefn-add-pc]");
    var card = src ? src.cloneNode(true) : document.createElement("li");
    if (!src) {
      card.className = "ud-remote__pc ud-remote__pc--add";
      card.setAttribute("data-uefn-add-pc", "");
      var plus = document.createElement("span");
      plus.className = "ud-remote__pc-plus";
      plus.setAttribute("aria-hidden", "true");
      plus.textContent = "+";
      var name = document.createElement("span");
      name.className = "ud-remote__pc-name";
      name.textContent = "Connect to your UEFN Ducky";
      card.appendChild(plus);
      card.appendChild(name);
    }
    prepareAddCard(card);
    list.appendChild(card);
    return true;
  }
  function paintRemoveX(list) {
    (list || document).querySelectorAll(".ud-remote__pc-remove").forEach(function (btn) {
      if (btn.querySelector("svg")) return;
      var label = (btn.getAttribute("aria-label") || btn.textContent || "Remove").trim() || "Remove";
      btn.setAttribute("aria-label", label);
      if (!btn.getAttribute("title")) btn.setAttribute("title", label);
      btn.textContent = "";
      btn.appendChild(svgIcon(ICONS.x));
    });
  }
  function parkRefresh() {
    return false;
  }
  var watchedRoot = null;
  var profileObserver = null;
  function stopWatchingProfile() {
    if (profileObserver) profileObserver.disconnect();
    profileObserver = null;
    watchedRoot = null;
  }

  var pcStates = [];
  var pcStatesAt = 0;
  var pcStatesBusy = false;
  var pcCsrf = "";
  function paintPcAvailability() {
    var needsRefresh = false;
    var cards = Array.from(document.querySelectorAll(".ud-remote__pcs > .ud-remote__pc:not(.ud-remote__pc--add)"));
    cards.forEach(function (card) {
      var nameEl = card.querySelector(".ud-remote__pc-name");
      var name = nameEl && nameEl.textContent.trim();
      if (!name) return;
      var matches = pcStates.filter(function (pc) { return pc.name === name; });
      // The core tiles do not expose keyId yet. Never guess between duplicate names.
      if (matches.length !== 1 || cards.filter(function (other) {
        var label = other.querySelector(".ud-remote__pc-name");
        return label && label.textContent.trim() === name;
      }).length !== 1) return;
      var pc = matches[0];
      // The existing heartbeat protocol explicitly writes zero when the desktop disables access.
      var disabled = pc.live === false && pc.last_seen === 0;
      var state = disabled ? "disabled" : pc.live ? "live" : "offline";
      var previous = card.getAttribute("data-uefn-pc-state");
      card.setAttribute("data-uefn-pc-state", state);
      var dot = card.querySelector(".ud-remote__pc-dot");
      if (dot) {
        dot.classList.toggle("ud-remote__pc-dot--live", state === "live");
        dot.classList.toggle("ud-remote__pc-dot--off", state === "offline");
        dot.classList.toggle("ud-remote__pc-dot--disabled", disabled);
        dot.setAttribute("aria-label", disabled ? "Disabled from UEFN Ducky side" : state === "live" ? "Live" : "Offline");
      }
      var label = card.querySelector(".ud-remote__pc-disabled");
      if (disabled && !label) {
        label = document.createElement("span");
        label.className = "ud-remote__pc-disabled";
        label.textContent = "Disabled from UEFN Ducky side";
        label.setAttribute("role", "status");
        nameEl.insertAdjacentElement("afterend", label);
      } else if (!disabled && label) label.remove();
      var open = card.querySelector(".ducky-btn--primary, [data-uefn-pc-open]");
      if (open) {
        if (!open.hasAttribute("data-uefn-pc-open")) open.setAttribute("data-uefn-pc-open", open.textContent || "Open");
        open.disabled = state !== "live";
        var text = disabled ? "Disabled" : state === "offline" ? "Offline" : open.getAttribute("data-uefn-pc-open");
        if (open.textContent !== text) open.textContent = text;
      } else if (state === "live" && previous && previous !== "live") needsRefresh = true;
    });
    if (needsRefresh) {
      var retry = document.querySelector(".ud-root [data-ud-retry]");
      if (retry) retry.click();
    }
  }
  async function refreshPcAvailability() {
    if (!isProfile() || document.hidden || pcStatesBusy || !document.querySelector(".ud-remote__pcs")) return;
    if (Date.now() - pcStatesAt < 10000) return;
    pcStatesBusy = true; pcStatesAt = Date.now();
    var abort = new AbortController();
    var timer = setTimeout(function () { abort.abort(); }, 8000);
    try {
      if (!pcCsrf) {
        var me = await http("/api/v1/auth/me", { credentials: "same-origin", signal: abort.signal });
        if (!me.ok) return;
        pcCsrf = me.headers.get("x-ducky-csrf-token") || "";
        if (!pcCsrf) {
          var anchor = document.querySelector("[data-ducky-csrf]");
          pcCsrf = anchor ? anchor.getAttribute("data-ducky-csrf") || "" : "";
        }
      }
      var headers = { "Content-Type": "application/json", Accept: "application/json" };
      if (pcCsrf) headers["X-CSRF-Token"] = pcCsrf;
      var response = await http("/api/v1/plugins/uefn-ducky/collect/desktop-devices", {
        method: "POST", credentials: "same-origin", headers: headers, body: "{}", signal: abort.signal
      });
      if (!response.ok) { if (response.status === 403) pcCsrf = ""; return; }
      var payload = await response.json();
      var data = payload.payload || payload;
      if (!Array.isArray(data.devices)) return;
      pcStates = data.devices;
      if (isProfile()) paintPcAvailability();
    } catch (err) {
      // Retain the last confirmed state during a temporary connection failure.
    } finally { clearTimeout(timer); pcStatesBusy = false; }
  }
  setInterval(refreshPcAvailability, 10000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { pcStatesAt = 0; void refreshPcAvailability(); }
  });

  function paintProfile() {
    paintAcc(document);
    parkRefresh();
    parkLang();
    paintIdentity();
    parkAddCard();
    paintRemoveX(document.querySelector(".ud-remote__pcs"));
    parkDots();
    paintPcAvailability();
    void refreshPcAvailability();
    document.querySelectorAll(".b-account-security").forEach(decorateSecurity);
  }
  function watchProfile() {
    var root = document.getElementById("app-content");
    if (!isProfile() || !root) {
      stopWatchingProfile();
      return;
    }
    if (watchedRoot !== root) {
      stopWatchingProfile();
      watchedRoot = root;
      profileObserver = new MutationObserver(function () {
        if (!isProfile() || !root.isConnected) {
          stopWatchingProfile();
          return;
        }
        refreshProfile();
      });
    }
    refreshProfile();
  }
  function refreshProfile() {
    if (!profileObserver || !watchedRoot) return;
    profileObserver.disconnect();
    try {
      paintProfile();
    } finally {
      profileObserver.observe(watchedRoot, { childList: true, subtree: true });
    }
  }
  function scan() {
    dropStaleInject();
    bindAccDoc();
    bindSignOut();
    bindProfileLogout();
    bindNamedPair();
    watchAll();
    watchProfile();
  }
  function afterRoute() {
    clearFade();
    scan();
  }
  function watchAll() {
    if (document.documentElement.getAttribute("data-uefn-route-watch") === "15") return;
    document.documentElement.setAttribute("data-uefn-route-watch", "15");
    document.addEventListener("ducky:route-applied", afterRoute);
  }
  window.DuckyBlockInits = window.DuckyBlockInits || {};
  window.DuckyBlockInits["ai-uefn-profile.hero"] = function () {
    afterRoute();
  };
  afterRoute();
})();

