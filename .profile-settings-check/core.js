(() => {
  // source:C:\Users\tas13\Documents\GitHub\DuckyOS\duckyos\crates\duckyos-core\src\ui\system\platform\container-width.ts
  var APP_DESKTOP_MIN_PX = 52.0625 * 16;
  var APP_MOBILE_MAX_PX = 52 * 16;
  function previewStageEl() {
    const vw = document.body?.getAttribute("data-vw");
    if (vw !== "mobile" && vw !== "tablet") return null;
    return document.getElementById("vw-device-frame");
  }
  function appContainerEl() {
    return previewStageEl() || document.getElementById("ducky-app");
  }
  function appContainerWidth() {
    const el = appContainerEl();
    if (el) return el.getBoundingClientRect().width;
    return window.innerWidth || document.documentElement.clientWidth || 0;
  }
  function isAppDesktop() {
    return appContainerWidth() >= APP_DESKTOP_MIN_PX;
  }
  function layoutViewportRect() {
    const winW = window.innerWidth || document.documentElement.clientWidth || 0;
    const winH = window.innerHeight || document.documentElement.clientHeight || 0;
    const win = new DOMRect(0, 0, winW, winH);
    const el = appContainerEl();
    if (!el) return win;
    const style = window.getComputedStyle(el);
    if (style.overflowX === "visible" && style.overflowY === "visible") return win;
    const box = el.getBoundingClientRect();
    const left = Math.max(0, box.left);
    const top = Math.max(0, box.top);
    const right = Math.min(winW, box.right);
    const bottom = Math.min(winH, box.bottom);
    if (right <= left || bottom <= top) return win;
    return new DOMRect(left, top, right - left, bottom - top);
  }
  function placeFixed(el, left, top) {
    el.style.left = left + "px";
    el.style.top = top + "px";
    const box = el.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) return;
    const dx = box.left - left;
    const dy = box.top - top;
    if (Math.abs(dx) > 0.5) el.style.left = left - dx + "px";
    if (Math.abs(dy) > 0.5) el.style.top = top - dy + "px";
  }
  var listeners = [];
  var ro = null;
  var observed = null;
  var lastW = -1;
  var watching = false;
  function tick() {
    const w = appContainerWidth();
    if (lastW >= 0 && Math.abs(w - lastW) < 2) return;
    lastW = w;
    for (const cb of listeners) cb();
  }
  function attach() {
    const el = appContainerEl();
    if (!ro) {
      ro = new ResizeObserver(() => tick());
    }
    if (el === observed) {
      tick();
      return;
    }
    if (observed) ro.unobserve(observed);
    observed = el;
    if (el) ro.observe(el);
    tick();
  }
  function ensureWatch() {
    if (watching) return;
    watching = true;
    attach();
    window.addEventListener("resize", attach);
    document.addEventListener("ducky:route-applied", attach);
    if (document.body && typeof MutationObserver === "function") {
      new MutationObserver(attach).observe(document.body, {
        attributes: true,
        attributeFilter: ["data-vw", "data-vw-orient"]
      });
    }
  }
  function onAppContainerWidth(cb) {
    listeners.push(cb);
    ensureWatch();
    return () => {
      const i = listeners.indexOf(cb);
      if (i >= 0) listeners.splice(i, 1);
    };
  }
  if (typeof window !== "undefined") {
    window.DuckyUiModules = window.DuckyUiModules || {};
    window.DuckyUiModules.layout = {
      appWidth: appContainerWidth,
      isDesktop: isAppDesktop,
      viewportRect: layoutViewportRect,
      placeFixed,
      onWidth: onAppContainerWidth
    };
  }

  // source:C:\Users\tas13\Documents\GitHub\DuckyOS\duckyos\crates\duckyos-core\src\ui\system\kit\overlay-position.ts
  function prefersReducedMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  // source:C:/Users/tas13/Documents/GitHub/DuckyOS/duckyos/crates/duckyos-core/src/ui/system/sliding-indicator/index.ts
  var pendingSnaps = {};
  function isAdminSidebarRail() {
    const sidebar = document.getElementById("app-sidebar");
    return !!(sidebar && sidebar.classList.contains("admin-sidebar--rail"));
  }
  function measureTarget(container, activeEl) {
    const cRect = container.getBoundingClientRect();
    const tRect = activeEl.getBoundingClientRect();
    if (container.classList.contains("registry-tabs--underline")) {
      const bar = parseFloat(getComputedStyle(container).getPropertyValue("--ducky-border-widthThick")) || 3;
      return { x: tRect.left - cRect.left, y: cRect.height - bar, w: tRect.width, h: bar };
    }
    return {
      x: tRect.left - cRect.left,
      y: tRect.top - cRect.top,
      w: tRect.width,
      h: tRect.height
    };
  }
  function measureAdminNavTarget(container, activeEl) {
    if (!container || !activeEl) return null;
    const cRect = container.getBoundingClientRect();
    const rail = isAdminSidebarRail();
    if (rail) {
      const icon = activeEl.querySelector(".admin-nav-icon") || activeEl;
      const iRect = icon.getBoundingClientRect();
      const size = 40;
      const cx = iRect.left + iRect.width / 2;
      const cy = iRect.top + iRect.height / 2;
      return {
        x: Math.round((cx - cRect.left - size / 2) * 2) / 2,
        y: Math.round((cy - cRect.top - size / 2) * 2) / 2,
        w: size,
        h: size,
        rail: true
      };
    }
    const t = measureTarget(container, activeEl);
    t.rail = false;
    return t;
  }
  function capture(indicator, container) {
    if (!indicator || !container) return null;
    if (indicator.style.opacity === "0") return null;
    const ir = indicator.getBoundingClientRect();
    if (ir.width < 1 || ir.height < 1) return null;
    const cRect = container.getBoundingClientRect();
    return {
      x: ir.left - cRect.left,
      y: ir.top - cRect.top,
      w: ir.width,
      h: ir.height,
      rail: isAdminSidebarRail()
    };
  }
  function stash(key, snap) {
    if (snap) pendingSnaps[key] = snap;
  }
  function consume(key) {
    const snap = pendingSnaps[key];
    delete pendingSnaps[key];
    return snap || null;
  }
  function peek(key) {
    return pendingSnaps[key] || null;
  }
  function captureAdminSidebar() {
    const nav = document.querySelector("#app-sidebar .admin-nav");
    if (!nav) return null;
    return capture(nav.querySelector(".admin-nav-indicator"), nav);
  }
  function applyBox(indicator, box, disableTransition) {
    if (disableTransition) indicator.style.transition = "none";
    indicator.style.position = "absolute";
    indicator.style.top = "-1px";
    indicator.style.left = "-1px";
    indicator.style.width = box.w + "px";
    indicator.style.height = box.h + "px";
    indicator.style.transform = "translate(" + box.x + "px, " + box.y + "px)";
    if (disableTransition) {
      void indicator.offsetWidth;
      indicator.style.transition = "";
    }
  }
  function position(indicator, container, activeEl, opts) {
    opts = opts || {};
    if (!indicator || !container) return;
    if (!activeEl) {
      indicator.style.opacity = "0";
      return;
    }
    if (opts.resetInit) {
      indicator.removeAttribute("data-slide-init");
    }
    const target = opts.target || measureTarget(container, activeEl);
    if (opts.snapOnly || opts.resetInit) {
      indicator.style.opacity = "1";
      applyBox(indicator, target, true);
      indicator.setAttribute("data-slide-init", "1");
      return;
    }
    const reduce = prefersReducedMotion();
    const from = opts.from;
    let startX;
    let startY;
    let startW;
    let startH;
    let animate = false;
    if (from && from.w > 0 && from.h > 0 && !reduce) {
      startX = from.x;
      startY = from.y;
      startW = from.w;
      startH = from.h;
      animate = true;
    } else if (indicator.getAttribute("data-slide-init") === "1" && indicator.style.opacity !== "0" && !reduce) {
      const ir = indicator.getBoundingClientRect();
      if (ir.width > 1 && ir.height > 1) {
        const cRect = container.getBoundingClientRect();
        startX = ir.left - cRect.left;
        startY = ir.top - cRect.top;
        startW = ir.width;
        startH = ir.height;
        animate = Math.abs(startX - target.x) > 0.5 || Math.abs(startY - target.y) > 0.5 || Math.abs(startW - target.w) > 0.5 || Math.abs(startH - target.h) > 0.5;
      }
    }
    indicator.style.opacity = "1";
    indicator.setAttribute("data-slide-init", "1");
    if (!animate) {
      applyBox(indicator, target, true);
      return;
    }
    applyBox(indicator, { x: startX, y: startY, w: startW, h: startH }, true);
    void indicator.offsetWidth;
    indicator.style.transition = "";
    indicator.style.width = target.w + "px";
    indicator.style.height = target.h + "px";
    indicator.style.transform = "translate(" + target.x + "px, " + target.y + "px)";
    if (opts.snapOnEnd && target && !reduce) {
      let onSnapEnd = function(ev) {
        if (ev.target !== ind) return;
        if (ev.propertyName !== "transform" && ev.propertyName !== "width" && ev.propertyName !== "height") {
          return;
        }
        ind.removeEventListener("transitionend", onSnapEnd);
        applyBox(ind, target, true);
      };
      const ind = indicator;
      ind.addEventListener("transitionend", onSnapEnd);
    }
  }
  function positionAdminNav(indicator, container, activeEl, opts) {
    opts = opts || {};
    if (!indicator || !container || !activeEl) {
      if (indicator) indicator.style.opacity = "0";
      return;
    }
    const target = measureAdminNavTarget(container, activeEl);
    position(indicator, container, activeEl, {
      from: opts.from,
      target: target || void 0,
      resetInit: opts.resetInit,
      snapOnly: opts.snapOnly,
      snapOnEnd: opts.snapOnEnd !== false
    });
  }
  var slidingIndicatorApi = {
    position,
    positionAdminNav,
    measureAdminNavTarget,
    capture,
    stash,
    consume,
    peek,
    captureAdminSidebar,
    prefersReducedMotion,
    isAdminSidebarRail
  };
  window.DuckySlidingIndicator = slidingIndicatorApi;

  // source:C:/Users/tas13/Documents/GitHub/DuckyOS/duckyos/crates/duckyos-core/src/ui/system/kit/tabs.ts
  var CONTAINER_SEL = '.registry-tabs, .ducky-pill-tabs, .cm-tabs, .tabs, [data-tabs], [role="tablist"]';
  var ITEM_SEL = '.registry-tab-btn, .cm-tab-btn, .tab, [role="tab"]';
  var INDICATOR_CLASS = "registry-tabs-indicator";
  var SLIDING_CLASS = "registry-tabs--sliding";
  var UNDERLINE_CLASS = "registry-tabs--underline";
  var BOUND_ATTR = "data-ducky-tabs-bound";
  var SLIDE_MS = 400;
  var idSeq = 0;
  function a11y() {
    return window.DuckyA11y || null;
  }
  function slider() {
    return window.DuckySlidingIndicator || null;
  }
  function ensureId(el, prefix) {
    if (!el.id) {
      idSeq += 1;
      el.id = prefix + "-" + idSeq;
    }
    return el.id;
  }
  function tabItems(container) {
    const out = [];
    container.querySelectorAll(ITEM_SEL).forEach(function(el) {
      if (el instanceof HTMLElement && el.closest(CONTAINER_SEL) === container) out.push(el);
    });
    return out;
  }
  function activeItem(container) {
    return tabItems(container).find(function(el) {
      return el.classList.contains("is-active") || el.getAttribute("aria-selected") === "true";
    }) || null;
  }
  function panelFor(item) {
    const id = item.getAttribute("aria-controls") || item.getAttribute("data-tab");
    return id ? document.getElementById(id) : null;
  }
  function ensureIndicator(container, create) {
    let ind = container.querySelector(":scope > ." + INDICATOR_CLASS);
    if (!ind && create) {
      ind = document.createElement("span");
      ind.className = INDICATOR_CLASS;
      ind.setAttribute("aria-hidden", "true");
      container.insertBefore(ind, container.firstChild);
    }
    return ind;
  }
  function captureIndicator(container) {
    const ind = ensureIndicator(container);
    const s = slider();
    return ind && s && typeof s.capture === "function" ? s.capture(ind, container) : null;
  }
  function clearIndicatorStyles(ind) {
    ind.removeAttribute("data-slide-init");
    ind.style.width = "";
    ind.style.height = "";
    ind.style.transform = "";
    ind.style.opacity = "0";
    ind.style.transition = "";
    ind.style.position = "";
  }
  function setSliding(container, on) {
    if (!container) return;
    container.classList.toggle(SLIDING_CLASS, on);
  }
  function moveIndicator(container, active, opts) {
    const ind = ensureIndicator(container);
    if (!ind || ind.parentElement !== container) return;
    const o = opts ? { ...opts } : {};
    const target = o.targetEl ?? active ?? activeItem(container);
    if (o.resetInit) {
      clearIndicatorStyles(ind);
      o.snapOnly = true;
    }
    const s = slider();
    if (!s || typeof s.position !== "function") return;
    s.position(ind, container, target ?? null, o);
  }
  function slideIndicator(container, target, snap) {
    if (!container) return;
    const from = snap === void 0 ? captureIndicator(container) : snap;
    setSliding(container, true);
    moveIndicator(container, target, from ? { from } : {});
    window.setTimeout(function() {
      setSliding(container, false);
    }, SLIDE_MS);
  }
  function applyRoles(container, items) {
    if (!container.getAttribute("role")) container.setAttribute("role", "tablist");
    items.forEach(function(el) {
      if (!el.getAttribute("role")) el.setAttribute("role", "tab");
      const panel = panelFor(el);
      if (!panel) return;
      el.setAttribute("aria-controls", panel.id);
      if (!panel.getAttribute("role")) panel.setAttribute("role", "tabpanel");
      if (!panel.getAttribute("aria-labelledby")) {
        panel.setAttribute("aria-labelledby", ensureId(el, "ducky-tab"));
      }
    });
  }
  function showPanel(item, on) {
    const panel = panelFor(item);
    if (!panel) return;
    if (panel.classList.contains("tab-content")) panel.classList.toggle("active", on);
    else panel.hidden = !on;
  }
  function setActiveTab(container, active, opts) {
    const o = opts || {};
    const items = tabItems(container);
    applyRoles(container, items);
    const snap = o.indicator === false || o.indicator === "snap" ? null : captureIndicator(container);
    items.forEach(function(el) {
      const on = el === active;
      el.classList.toggle("is-active", on);
      if (on && el.classList.contains("tab")) el.classList.add("active");
      else if (!on) el.classList.remove("active");
      el.setAttribute("aria-selected", on ? "true" : "false");
      el.setAttribute("tabindex", on ? "0" : "-1");
      if (o.panels) showPanel(el, on);
    });
    if (o.indicator === "snap") moveIndicator(container, active, { resetInit: true });
    else if (o.indicator !== false) slideIndicator(container, active, snap);
    if (o.announce && active) {
      const ax = a11y();
      const label = ax?.accessibleName?.(active) || (active.textContent || "").trim();
      if (ax && label) ax.announce(label + " selected");
    }
  }
  function containersIn(root) {
    const out = [];
    if (root instanceof Element && root.matches(CONTAINER_SEL)) out.push(root);
    root.querySelectorAll(CONTAINER_SEL).forEach(function(el) {
      out.push(el);
    });
    return out;
  }
  function bindTabs(root, opts) {
    const o = opts || {};
    const releases = [];
    containersIn(root || document).forEach(function(container) {
      const mode = container.getAttribute("data-tabs-mode") || o.mode || "pill";
      container.classList.toggle(UNDERLINE_CLASS, mode === "underline");
      if (o.indicator) ensureIndicator(container, true);
      const sliding = container.classList.contains(SLIDING_CLASS);
      if (container.getAttribute(BOUND_ATTR) === "1") {
        if (!sliding) moveIndicator(container, null, { resetInit: true });
        return;
      }
      container.setAttribute(BOUND_ATTR, "1");
      applyRoles(container, tabItems(container));
      const current = activeItem(container);
      tabItems(container).forEach(function(el) {
        el.setAttribute("tabindex", el === current ? "0" : "-1");
        showPanel(el, el === current);
      });
      if (!sliding) moveIndicator(container, current, { resetInit: true });
      const onClick = function(ev) {
        const t = ev.target;
        if (!(t instanceof Element)) return;
        const item = t.closest(ITEM_SEL);
        if (!(item instanceof HTMLElement) || item.closest(CONTAINER_SEL) !== container) return;
        const href = item.getAttribute("href");
        if (href && href.charAt(0) !== "#" || !panelFor(item)) return;
        ev.preventDefault();
        setActiveTab(container, item, { panels: true, announce: true });
        const panel = panelFor(item);
        const spa = window.DuckyUiModules && window.DuckyUiModules.spa;
        if (panel && spa && typeof spa.runAfterSwapScripts === "function") {
          spa.runAfterSwapScripts(panel);
        }
        const ax2 = a11y();
        if (ax2 && typeof ax2.scrollToTop === "function") ax2.scrollToTop();
        if (o.onChange) o.onChange(item.getAttribute("data-tab") || item.id, item);
      };
      container.addEventListener("click", onClick);
      const ax = a11y();
      const releaseRoving = ax && typeof ax.rovingTabindex === "function" ? ax.rovingTabindex(container, ITEM_SEL, { orientation: "horizontal" }) : null;
      tabItems(container).forEach(function(el) {
        el.setAttribute("tabindex", el === activeItem(container) ? "0" : "-1");
      });
      releases.push(function() {
        container.removeEventListener("click", onClick);
        if (releaseRoving) releaseRoving();
        container.removeAttribute(BOUND_ATTR);
      });
    });
    return function unbind() {
      releases.forEach(function(r) {
        r();
      });
      releases.length = 0;
    };
  }

  // source:C:\Users\tas13\Documents\GitHub\UEFN-Ducky-Release\.profile-settings-check\core-entry.ts
  bindTabs(document);
})();
