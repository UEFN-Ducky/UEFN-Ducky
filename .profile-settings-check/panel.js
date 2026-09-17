(function () {
  var SVG_NS = ["ht", "tp://www.w3.org/2000/svg"].join("");
  var securityCard = null;
  function isProfile() {
    var path = String(window.location.pathname || "");
    var attr = String(document.body.getAttribute("data-page-path") || "");
    return path.indexOf("/profile") === 0 || attr.indexOf("/profile") === 0;
  }
  function svgEl(parts) {
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
  function svgGear() {
    return svgEl([
      { tag: "circle", cx: "12", cy: "12", r: "3" },
      "M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z",
    ]);
  }
  function svgRefresh() {
    return svgEl([
      "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8",
      "M21 3v5h-5",
      "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16",
      "M3 21v-5h5",
    ]);
  }
  function parkEl() {
    var park = document.getElementById("uefn-security-park");
    if (park) return park;
    park = document.createElement("div");
    park.id = "uefn-security-park";
    park.hidden = true;
    document.body.appendChild(park);
    return park;
  }
  function overlayEl() {
    return document.getElementById("uefn-security-modal");
  }

  var tabsResize = null;
  var tabsMutation = null;
  var tabsFrame = 0;
  var tabsRetry = 0;
  function stopSecurityTabs() {
    if (tabsResize) tabsResize.disconnect();
    if (tabsMutation) tabsMutation.disconnect();
    tabsResize = null;
    tabsMutation = null;
    cancelAnimationFrame(tabsFrame);
    clearTimeout(tabsRetry);
    tabsFrame = 0;
    tabsRetry = 0;
  }
  function watchSecurityTabs(card) {
    stopSecurityTabs();
    function schedule() {
      cancelAnimationFrame(tabsFrame);
      tabsFrame = requestAnimationFrame(function () {
        tabsFrame = 0;
        if (!card.isConnected || !card.closest("#uefn-security-modal")) return;
        var bar = card.querySelector(".registry-tabs");
        if (!bar) return;
        if (bar.classList.contains("registry-tabs--sliding")) {
          clearTimeout(tabsRetry);
          tabsRetry = setTimeout(schedule, 420);
          return;
        }
        var active = bar.querySelector('[aria-selected="true"]') || bar.querySelector(".is-active");
        var indicator = bar.querySelector(".registry-tabs-indicator");
        var slider = window.DuckySlidingIndicator;
        if (!active || !indicator || !slider) return;
        var box = active.getBoundingClientRect();
        if (box.width <= 0 || box.height <= 0) return;
        slider.position(indicator, bar, active, { resetInit: true });
      });
    }
    function observeTabs() {
      mountPhotoSettings(card);
      if (tabsResize) {
        tabsResize.disconnect();
        card.querySelectorAll(".registry-tabs, .registry-tab-btn").forEach(function (el) {
          tabsResize.observe(el);
        });
      }
      schedule();
    }
    if (typeof ResizeObserver === "function") tabsResize = new ResizeObserver(schedule);
    tabsMutation = new MutationObserver(observeTabs);
    tabsMutation.observe(card, { childList: true, subtree: true });
    observeTabs();
  }

  function closeSecurity() {
    stopSecurityTabs();
    var overlay = overlayEl();
    var card = securityCard || document.querySelector(".b-account-security");
    if (card) {
      securityCard = card;
      parkEl().appendChild(card);
    }
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
  }
  function parkSecurity() {
    var modalCard = document.querySelector("#uefn-security-modal .b-account-security");
    if (modalCard) {
      securityCard = modalCard;
      return true;
    }
    var parked = document.querySelector("#uefn-security-park .b-account-security");
    if (parked) {
      securityCard = parked;
      return true;
    }
    var card =
      document.querySelector("#app-content .b-account-security") ||
      document.querySelector(".b-account-security");
    if (!card) return false;
    securityCard = card;
    parkEl().appendChild(card);
    return true;
  }
  function openSecurity() {
    var card = securityCard || document.querySelector(".b-account-security");
    if (!card) return;
    securityCard = card;
    closeSecurity();
    securityCard = card;
    var overlay = document.createElement("div");
    overlay.id = "uefn-security-modal";
    overlay.className = "ducky-modal-backdrop";
    overlay.setAttribute("role", "presentation");
    var panel = document.createElement("div");
    panel.className = "ducky-modal ducky-modal--lg";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-labelledby", "uefn-security-title");
    var header = document.createElement("div");
    header.className = "ducky-modal__header";
    var title = document.createElement("h2");
    title.id = "uefn-security-title";
    title.textContent = "Security & Authentication";
    var x = document.createElement("button");
    x.type = "button";
    x.className = "modal-close";
    x.setAttribute("data-uefn-security-close", "");
    x.setAttribute("aria-label", "Close");
    x.textContent = "\u00d7";
    header.appendChild(title);
    header.appendChild(x);
    var body = document.createElement("div");
    body.className = "ducky-modal__body";
    var mount = document.createElement("div");
    mount.setAttribute("data-uefn-security-mount", "");
    body.appendChild(mount);
    panel.appendChild(header);
    panel.appendChild(body);
    overlay.appendChild(panel);
    overlay.addEventListener("click", function (ev) {
      if (ev.target === overlay) closeSecurity();
    });
    var host = document.getElementById("ducky-modal-host");
    if (host && host.parentNode) host.parentNode.insertBefore(overlay, host);
    else document.body.appendChild(overlay);
    mount.appendChild(card);
    var general = card.querySelector('[data-tab="general"]');
    if (general) general.click();
    watchSecurityTabs(card);
  }

  var profilePhoto = null;
  function profileSettingsControls() {
    document.querySelectorAll(".b-user-profile .user-profile-avatar-btn, .b-user-profile .user-profile-name-display").forEach(function (el) {
      el.setAttribute("role", "button"); el.setAttribute("tabindex", "0");
      el.setAttribute("title", "Open profile settings");
      el.setAttribute("aria-label", "Open profile settings"); el.setAttribute("aria-haspopup", "dialog");
    });
  }
  function mountLogout(card) {
    var profile = card.querySelector('[data-acct-section="profile"]');
    var general = profile && profile.closest(".ducky-tabs__panel");
    if (!general) return;
    var section = general.querySelector("[data-uefn-account-logout]");
    if (!section) {
      section = document.createElement("section");
      section.className = "uefn-account-logout";
      section.setAttribute("data-uefn-account-logout", "");
      var copy = document.createElement("div");
      var title = document.createElement("h3"); title.textContent = "Log out";
      var hint = document.createElement("p"); hint.textContent = "Sign out of this browser.";
      copy.appendChild(title); copy.appendChild(hint); section.appendChild(copy);
      var danger = general.querySelector('[data-acct-section="danger"]');
      general.insertBefore(section, danger);
    }
    if (section.querySelector(".user-profile-logout")) return;
    var form = document.querySelector(".b-user-profile .user-profile-logout");
    if (!form) {
      var source = document.querySelector(".uefn-profile-chrome .user-profile-logout");
      if (source) form = source.cloneNode(true);
      else {
        form = document.createElement("form");
        form.className = "user-profile-logout"; form.method = "post"; form.action = "/logout";
        var button = document.createElement("button"); button.type = "submit";
        button.className = "user-profile-logout-btn"; button.setAttribute("aria-label", "Log out");
        var label = document.createElement("span"); label.textContent = "Log out";
        button.appendChild(label); form.appendChild(button);
      }
    }
    var btn = form.querySelector("button");
    if (btn && !btn.querySelector("svg")) btn.insertBefore(svgEl(["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"]), btn.firstChild);
    section.appendChild(form);
  }
  function mountProfileFields(card) {
    var form = card.querySelector("[data-acct-profile]");
    if (!form) return;
    var input = form.querySelector('input[name="displayName"]');
    var save = form.querySelector('button[type="submit"]');
    if (input && save && !form.querySelector(".uefn-name-control")) {
      var label = input.closest("label");
      if (label) {
        input.id = input.id || (input.closest(".ducky-tabs__panel").id + "-display-name");
        label.setAttribute("for", input.id);
        var control = document.createElement("div");
        control.className = "uefn-name-control";
        label.insertAdjacentElement("afterend", control);
        control.appendChild(input);
        control.appendChild(save);
      }
    }
    var values = form.querySelectorAll(":scope > p.tm-muted");
    if (values.length) {
      var details = document.createElement("dl");
      details.className = "uefn-account-details";
      form.insertBefore(details, values[0]);
      values.forEach(function (value) {
        var row = document.createElement("div");
        var label = document.createElement("dt");
        var content = document.createElement("dd");
        label.textContent = value.textContent.indexOf("@") !== -1 ? "Email" : "Membership";
        content.appendChild(value);
        row.appendChild(label); row.appendChild(content); details.appendChild(row);
      });
    }
  }
  function mountPhotoSettings(card) {
    mountProfileFields(card);
    mountLogout(card);
    var section = card.querySelector('[data-acct-section="profile"]');
    if (!section) return;
    var input = document.querySelector(".b-user-profile [data-profile-photo]") || profilePhoto;
    if (!input) return;
    profilePhoto = input;
    var row = section.querySelector(".uefn-avatar-settings");
    if (!row) {
      row = document.createElement("div"); row.className = "uefn-avatar-settings";
      var button = document.createElement("button"); button.type = "button";
      button.className = "uefn-avatar-change";
      button.setAttribute("aria-label", "Change profile photo");
      var preview = document.createElement("span"); preview.className = "uefn-avatar-preview";
      preview.setAttribute("aria-hidden", "true");
      var badge = document.createElement("span"); badge.className = "uefn-avatar-edit";
      badge.appendChild(svgEl(["M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z", {tag:"circle",cx:"12",cy:"13",r:"3"}]));
      var copy = document.createElement("span"); copy.className = "uefn-avatar-copy";
      var title = document.createElement("strong"); title.textContent = "Profile photo";
      var action = document.createElement("span"); action.textContent = "Change photo";
      copy.appendChild(title); copy.appendChild(action);
      button.appendChild(preview); button.appendChild(badge); button.appendChild(copy);
      button.addEventListener("click", function () { if (profilePhoto) profilePhoto.click(); });
      row.appendChild(button); section.insertBefore(row, section.firstChild);
    }
    var source = document.querySelector(".b-user-profile .user-profile-avatar img");
    var name = document.querySelector(".b-user-profile .user-profile-name-display");
    var initial = ((name && name.textContent.trim()) || "?").charAt(0).toUpperCase();
    var photo = row.querySelector(".uefn-avatar-preview");
    var key = source ? source.getAttribute("src") : initial;
    if (photo.getAttribute("data-preview") !== key) {
      photo.setAttribute("data-preview", key || "");
      photo.textContent = "";
      if (source) {
        var image = document.createElement("img"); image.src = source.src; image.alt = "";
        photo.appendChild(image);
      } else photo.textContent = initial;
    }
    input.hidden = true;
    if (input.parentNode !== row) row.appendChild(input);
    var status = document.querySelector(".b-user-profile [data-profile-status]");
    if (status) { status.setAttribute("role", "status"); row.appendChild(status); }
  }

  function parkGear() {
    var layout = document.querySelector(".b-user-profile .user-profile-layout");
    if (!layout) return false;
    var actions = layout.querySelector(":scope > .user-profile-actions");
    if (!actions) return false;
    var btn = actions.querySelector(".user-profile-security-btn");
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "user-profile-security-btn";
      btn.setAttribute("aria-label", "Security & Authentication");
      btn.setAttribute("title", "Security & Authentication");
      btn.appendChild(svgGear());
      actions.appendChild(btn);
    }
    var logout = actions.querySelector(":scope > .user-profile-logout");
    if (logout && btn.nextElementSibling !== logout) actions.insertBefore(btn, logout);
    return true;
  }
  function parkCardRefresh() {
    var headBtn = document.querySelector(".ud-remote__head [data-uefn-refresh-pcs]");
    document.querySelectorAll(".ud-remote__pc:not(.ud-remote__pc--add)").forEach(function (li) {
      if (li.querySelector("[data-uefn-refresh-pcs]")) return;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ud-remote__refresh";
      btn.setAttribute("data-uefn-refresh-pcs", "");
      btn.setAttribute("aria-label", "Refresh");
      btn.setAttribute("title", "Refresh");
      btn.appendChild(svgRefresh());
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var retry = document.querySelector("[data-ud-retry]");
        if (retry) retry.click();
      });
      li.appendChild(btn);
    });
    if (headBtn && headBtn.parentNode) headBtn.parentNode.removeChild(headBtn);
  }
  function paint() {
    if (!isProfile()) return;
    parkSecurity();
    if (securityCard) mountLogout(securityCard);
    profileSettingsControls();
    if (overlayEl() && securityCard) mountPhotoSettings(securityCard);
    parkCardRefresh();
  }
  function bind() {
    document.documentElement.removeAttribute("data-uefn-security");
    if (document.documentElement.getAttribute("data-uefn-security-bound") === "2") return;
    document.documentElement.setAttribute("data-uefn-security-bound", "2");
    document.addEventListener(
      "click",
      function (ev) {
        var t = ev.target;
        if (!t || !t.closest) return;
        if (t.closest("[data-uefn-security-close]")) {
          ev.preventDefault();
          closeSecurity();
          return;
        }
        if (!isProfile()) return;
        var btn = t.closest(".user-profile-security-btn, .b-user-profile .user-profile-avatar-btn, .b-user-profile .user-profile-name-display");
        if (!btn) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        openSecurity();
      },
      true,
    );
    document.addEventListener(
      "keydown",
      function (ev) {
        var target = ev.target;
        if ((ev.key === "Enter" || ev.key === " ") && target && target.closest && target.closest(".b-user-profile .user-profile-avatar-btn, .b-user-profile .user-profile-name-display")) {
          ev.preventDefault(); ev.stopImmediatePropagation(); openSecurity(); return;
        }
        if (ev.key !== "Escape") return;
        if (!overlayEl()) return;
        if (document.querySelector("#ducky-modal-host .ducky-modal-backdrop")) return;
        ev.preventDefault();
        closeSecurity();
      },
      true,
    );
  }
  var watched = null;
  var obs = null;
  function watch() {
    var root = document.getElementById("app-content");
    if (!isProfile() || !root) return;
    bind();
    if (watched !== root) {
      if (obs) obs.disconnect();
      watched = root;
      obs = new MutationObserver(function () {
        if (!isProfile()) return;
        obs.disconnect();
        try {
          paint();
        } finally {
          obs.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] });
        }
      });
    }
    if (obs) {
      obs.disconnect();
      try {
        paint();
      } finally {
        obs.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] });
      }
    }
  }
  window.DuckyBlockInits = window.DuckyBlockInits || {};
  window.DuckyBlockInits["ai-uefn-profile.panel"] = watch;
  watch();
  document.addEventListener("ducky:route-applied", watch);
})();
