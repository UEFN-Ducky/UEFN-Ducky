(function () {
  "use strict";

  const state = {
    screen: "",
    version: "",
    previousVersion: "",
    isUpgrade: false,
    allUsers: false,
    userDir: "",
    machineDir: "",
    dir: "",
    installing: false,
    cancelled: false,
  };

  const $ = (id) => document.getElementById(id);
  const bridge = window.chrome && window.chrome.webview;
  const send = (msg) => {
    try {
      bridge.postMessage(msg);
    } catch (_) {}
  };

  const v = (version) => (version ? "v" + version : "");

  function show(name) {
    state.screen = name;
    document.querySelectorAll(".screen").forEach((el) => {
      el.classList.toggle("active", el.dataset.screen === name);
    });
  }

  function scopeLabel() {
    const who = state.allUsers ? "Everyone on this PC" : "Only me";
    return $("desktop").checked ? who + " · Desktop shortcut" : who;
  }

  function renderSummary() {
    $("sum-dir").textContent = state.dir;
    $("sum-dir").title = state.dir;
    $("sum-scope").textContent = scopeLabel();
  }

  function applyDir() {
    $("dir").value = state.dir;
    renderSummary();
    send({ type: "diskFree", path: state.dir });
  }

  function setScope(allUsers) {
    state.allUsers = allUsers;
    $("scope-user").setAttribute("aria-checked", String(!allUsers));
    $("scope-machine").setAttribute("aria-checked", String(allUsers));
    if (!state.isUpgrade) {
      state.dir = allUsers ? state.machineDir : state.userDir;
      applyDir();
    }
    renderSummary();
  }

  // LICENSE is Markdown (##/### headings, - lists, **bold**, `code`, ---):
  // show it as a document, not raw text. Escaped first, so only our own tags.
  function renderLicense(text) {
    const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const inline = (s) =>
      esc(s)
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/`([^`]+)`/g, "<code>$1</code>");
    const lines = (text || "").replace(/\r/g, "").split("\n");
    let html = "";
    let para = [];
    let list = null;
    const flushPara = () => {
      if (!para.length) return;
      const body = para.join(" ");
      const cls = body.startsWith("SPDX-License-Identifier") ? ' class="license-spdx"' : "";
      html += "<p" + cls + ">" + inline(body) + "</p>";
      para = [];
    };
    const flushList = () => {
      if (!list) return;
      html += "<ul>" + list.map((item) => "<li>" + inline(item) + "</li>").join("") + "</ul>";
      list = null;
    };
    lines.forEach((raw, i) => {
      const line = raw.replace(/\s+$/, "");
      let m;
      if (!line.trim()) {
        flushPara();
        flushList();
      } else if (i === 0 && !line.startsWith("#")) {
        html += '<h3 class="license-title">' + inline(line) + "</h3>";
      } else if ((m = /^(#{1,3})\s+(.*)$/.exec(line))) {
        flushPara();
        flushList();
        const tag = m[1].length === 3 ? "h5" : "h4";
        html += "<" + tag + ">" + inline(m[2]) + "</" + tag + ">";
      } else if (/^-{3,}$/.test(line.trim())) {
        flushPara();
        flushList();
        html += "<hr />";
      } else if ((m = /^[-*] (.*)$/.exec(line))) {
        flushPara();
        (list = list || []).push(m[1]);
      } else if (list && /^\s{2,}\S/.test(line)) {
        list[list.length - 1] += " " + line.trim();
      } else {
        flushList();
        para.push(line.trim());
      }
    });
    flushPara();
    flushList();
    $("license-text").innerHTML = html;
    const title = lines[0] && !lines[0].startsWith("#") ? lines[0].trim() : "";
    if (title) $("read-license").textContent = title;
  }

  function setProgress(percent, status) {
    const pct = Math.max(0, Math.min(100, Math.round(percent || 0)));
    $("bar").style.width = pct + "%";
    $("progress").setAttribute("aria-valuenow", String(pct));
    $("progress").classList.toggle("has-progress", pct > 0);
    $("percent").textContent = pct > 0 ? pct + "%" : "";
    if (status) $("status").textContent = status;
  }

  function startInstall() {
    const typed = $("dir").value.trim();
    if (typed) state.dir = typed;
    if (!state.dir) return show("options");
    state.installing = true;
    state.cancelled = false;
    $("cancel").disabled = false;
    $("cancel").textContent = "Cancel";
    setProgress(0, state.isUpgrade ? "Closing UEFN Ducky…" : "Getting ready…");
    show("progress");
    send({
      type: "startInstall",
      dir: state.dir,
      allUsers: state.allUsers,
      desktopIcon: $("desktop").checked,
      launch: true,
      isUpgrade: state.isUpgrade,
    });
  }

  function onInit(data) {
    state.version = data.version || "";
    state.previousVersion = data.previousVersion || "";
    state.isUpgrade = !!data.isUpgrade;
    state.userDir = data.userDir || "";
    state.machineDir = data.machineDir || "";
    state.allUsers = !!data.allUsers;
    state.dir = data.dir || (state.allUsers ? state.machineDir : state.userDir);
    renderLicense(data.license || "");
    $("welcome-version").textContent = v(state.version);
    document.title = state.isUpgrade ? "Updating UEFN Ducky" : "UEFN Ducky Setup";
    $("progress-title").textContent = state.isUpgrade ? "Updating UEFN Ducky" : "Installing UEFN Ducky";
    $("progress-versions").textContent =
      state.isUpgrade && state.previousVersion && state.previousVersion !== state.version
        ? v(state.previousVersion) + "  →  " + v(state.version)
        : v(state.version);
    $("done-title").textContent = state.isUpgrade ? "UEFN Ducky is up to date" : "UEFN Ducky is ready";
    $("done-sub").textContent = state.isUpgrade
      ? "You're on " + v(state.version) + ". Your chats, projects and settings are right where you left them."
      : v(state.version) + " is installed. Open it to connect UEFN and start building.";
    setScope(state.allUsers);
    applyDir();
    if (state.isUpgrade) startInstall();
    else show("welcome");
  }

  function onInstallDone(data) {
    state.installing = false;
    if (data.ok) {
      setProgress(100, "Done");
      show("done");
      return;
    }
    const cancelled = state.cancelled;
    $("error-title").textContent = cancelled ? "Setup was cancelled" : "Setup didn't finish";
    $("error").textContent = cancelled ? "You can run it again any time." : data.error || "The install did not finish.";
    $("retry").textContent = !cancelled ? "Try again" : state.isUpgrade ? "Update again" : "Install again";
    document.querySelector('[data-screen="error"]').classList.toggle("is-cancelled", cancelled);
    show("error");
  }

  const quit = () => send({ type: "quit" });
  const finish = (launch) => send({ type: "finish", launch });

  function back() {
    show("welcome");
  }

  // The window has no title bar: any spot that isn't a control drags it.
  const INTERACTIVE = "button, input, label, a, .license, .choice";
  $("app").addEventListener("mousedown", (ev) => {
    if (ev.button !== 0 || ev.target.closest(INTERACTIVE)) return;
    send({ type: "drag" });
  });

  $("btn-min").addEventListener("click", () => send({ type: "minimize" }));
  $("btn-close").addEventListener("click", quit);
  $("install").addEventListener("click", startInstall);
  $("options-install").addEventListener("click", startInstall);
  $("change").addEventListener("click", () => show("options"));
  $("read-license").addEventListener("click", () => show("license"));
  document.querySelectorAll("[data-back]").forEach((el) => el.addEventListener("click", back));
  $("browse").addEventListener("click", () => send({ type: "pickFolder", path: $("dir").value }));
  $("dir").addEventListener("change", () => {
    state.dir = $("dir").value.trim();
    applyDir();
  });
  $("scope-user").addEventListener("click", () => setScope(false));
  $("scope-machine").addEventListener("click", () => setScope(true));
  $("desktop").addEventListener("change", renderSummary);
  $("cancel").addEventListener("click", () => {
    if (!state.installing) return;
    state.cancelled = true;
    $("cancel").disabled = true;
    $("cancel").textContent = "Cancelling…";
    send({ type: "cancel" });
  });
  $("open").addEventListener("click", () => finish(true));
  $("close-done").addEventListener("click", () => finish(false));
  $("close-error").addEventListener("click", quit);
  $("retry").addEventListener("click", startInstall);

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      if (state.screen === "options" || state.screen === "license") back();
      else if (!state.installing) quit();
      return;
    }
    if (ev.key !== "Enter" || ev.target.closest("button, input")) return;
    const primary = { welcome: "install", options: "options-install", done: "open", error: "retry" }[state.screen];
    if (primary) $(primary).click();
  });

  if (bridge) {
    bridge.addEventListener("message", (ev) => {
      const data = ev.data || {};
      if (data.type === "init") onInit(data);
      if (data.type === "folderPicked" && data.path) {
        state.dir = data.path;
        applyDir();
      }
      if (data.type === "diskFree") {
        $("space").textContent = data.text || "";
        $("space").classList.toggle("low", !!data.low);
      }
      if (data.type === "progress") setProgress(data.percent, data.status);
      if (data.type === "installDone") onInstallDone(data);
    });
  }

  send({ type: "ready" });
})();
