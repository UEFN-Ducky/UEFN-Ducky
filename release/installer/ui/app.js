(function () {
  const STEPS = ["welcome", "license", "location", "options", "progress", "done"];
  const state = {
    step: "welcome",
    version: "",
    license: "",
    isUpgrade: false,
    allUsers: false,
    userDir: "",
    machineDir: "",
    dir: "",
    desktopIcon: true,
    launch: true,
    installing: false,
    failed: false,
  };

  const $ = (id) => document.getElementById(id);
  const send = (msg) => {
    try {
      window.chrome.webview.postMessage(msg);
    } catch (_) {}
  };

  function show(step) {
    state.step = step;
    document.querySelectorAll(".step").forEach((el) => {
      el.classList.toggle("active", el.dataset.step === step);
    });
    const idx = STEPS.indexOf(step);
    const dots = $("dots");
    dots.innerHTML = "";
    dots.classList.toggle("hidden", step === "progress" || step === "done" || state.isUpgrade);
    STEPS.slice(0, 4).forEach((_, i) => {
      const d = document.createElement("span");
      d.className = "dot" + (i < idx ? " done" : i === idx ? " on" : "");
      dots.appendChild(d);
    });
    $("back").classList.toggle("hidden", step === "welcome" || step === "progress" || step === "done" || state.isUpgrade);
    $("cancel").classList.toggle("hidden", step !== "progress");
    $("next").classList.toggle("hidden", step === "progress");
    const labels = {
      welcome: state.isUpgrade ? "Update" : "Next",
      license: "Next",
      location: "Next",
      options: "Install",
      done: "Finish",
    };
    $("next").textContent = labels[step] || "Next";
    $("next").disabled = step === "license" && !$("accept").checked;
  }

  function applyDir() {
    $("dir").value = state.dir;
    send({ type: "diskFree", path: state.dir });
  }

  function setScope(allUsers) {
    state.allUsers = allUsers;
    $("scope-user").classList.toggle("selected", !allUsers);
    $("scope-machine").classList.toggle("selected", allUsers);
    if (!state.isUpgrade) {
      state.dir = allUsers ? state.machineDir : state.userDir;
      applyDir();
    }
  }

  function startInstall() {
    state.installing = true;
    show("progress");
    $("error").classList.add("hidden");
    $("bar").style.width = "2%";
    $("status").textContent = "Starting…";
    send({
      type: "startInstall",
      dir: state.dir,
      allUsers: state.allUsers,
      desktopIcon: $("desktop").checked,
      launch: $("launch").checked,
      isUpgrade: state.isUpgrade,
    });
  }

  function onInit(data) {
    state.version = data.version || "";
    state.license = data.license || "";
    state.isUpgrade = !!data.isUpgrade;
    state.userDir = data.userDir || "";
    state.machineDir = data.machineDir || "";
    state.allUsers = !!data.allUsers;
    state.dir = data.dir || (state.allUsers ? state.machineDir : state.userDir);
    $("license-text").textContent = state.license;
    $("window-title").textContent = state.isUpgrade
      ? "Updating UEFN Ducky to v" + state.version
      : "UEFN Ducky Setup";
    $("welcome-title").textContent = state.isUpgrade
      ? "Update UEFN Ducky"
      : "UEFN Ducky";
    $("welcome-lead").textContent = state.isUpgrade
      ? "Version " + state.version + " will replace the copy already on this PC. Your chats and settings stay put."
      : "The control panel for Unreal Editor for Fortnite. Version " + state.version + ".";
    $("progress-title").textContent = state.isUpgrade ? "Updating" : "Installing";
    $("done-title").textContent = state.isUpgrade
      ? "UEFN Ducky is up to date"
      : "UEFN Ducky is installed";
    setScope(state.allUsers);
    applyDir();
    if (state.isUpgrade) startInstall();
    else show("welcome");
  }

  $("drag").addEventListener("mousedown", () => send({ type: "drag" }));
  $("btn-min").addEventListener("click", () => send({ type: "minimize" }));
  $("btn-close").addEventListener("click", () => send({ type: "quit" }));
  $("accept").addEventListener("change", () => {
    $("next").disabled = !$("accept").checked;
  });
  $("browse").addEventListener("click", () => send({ type: "pickFolder", path: $("dir").value }));
  $("dir").addEventListener("change", () => {
    state.dir = $("dir").value;
    send({ type: "diskFree", path: state.dir });
  });
  $("scope-user").addEventListener("click", () => setScope(false));
  $("scope-machine").addEventListener("click", () => setScope(true));
  $("cancel").addEventListener("click", () => send({ type: "cancel" }));
  $("back").addEventListener("click", () => {
    const i = STEPS.indexOf(state.step);
    if (i > 0) show(STEPS[i - 1]);
  });
  $("next").addEventListener("click", () => {
    if (state.failed) return send({ type: "quit" });
    if (state.step === "welcome") return show(state.isUpgrade ? "progress" : "license");
    if (state.step === "license") {
      if (!$("accept").checked) return;
      return show("location");
    }
    if (state.step === "location") {
      state.dir = $("dir").value.trim();
      if (!state.dir) return;
      return show("options");
    }
    if (state.step === "options") return startInstall();
    if (state.step === "done") {
      state.launch = $("launch").checked;
      send({ type: "finish", launch: state.launch });
    }
  });

  window.chrome.webview.addEventListener("message", (ev) => {
    const data = ev.data || {};
    if (data.type === "init") onInit(data);
    if (data.type === "folderPicked" && data.path) {
      state.dir = data.path;
      applyDir();
    }
    if (data.type === "diskFree") {
      const el = $("space");
      el.textContent = data.text || "";
      el.classList.toggle("low", !!data.low);
    }
    if (data.type === "progress") {
      $("bar").style.width = Math.max(0, Math.min(100, data.percent || 0)) + "%";
      if (data.status) $("status").textContent = data.status;
    }
    if (data.type === "installDone") {
      state.installing = false;
      if (data.ok) {
        $("bar").style.width = "100%";
        show("done");
      } else {
        state.failed = true;
        $("error").textContent = data.error || "Install did not finish.";
        $("error").classList.remove("hidden");
        $("cancel").classList.add("hidden");
        $("next").classList.remove("hidden");
        $("next").disabled = false;
        $("next").textContent = "Close";
      }
    }
  });

  send({ type: "ready" });
})();
