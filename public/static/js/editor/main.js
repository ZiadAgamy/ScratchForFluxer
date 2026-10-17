/* The block editor starts here: loads the blocks and the bot's saved work, puts the pieces together (blocks, Python,
   files, problems, console, saving), and runs the toolbar and the panel layout. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var sff = window.sff;
  var root = document.getElementById("editor");
  if (!root) return;

  var botId = root.dataset.botId;
  var workspaceUrl = "/api/bots/" + encodeURIComponent(botId) + "/workspace";
  var saver = null;
  var latest = null;
  var started = false;

  var refs = {
    status: document.getElementById("ed-status"),
    statusText: document.getElementById("ed-status-text"),
    loading: document.getElementById("ed-loading"),
    failed: document.getElementById("ed-failed"),
    failedText: document.getElementById("ed-failed-text"),
    tools: document.getElementById("ed-tools"),
    tip: document.getElementById("ed-tip"),
  };

  // ------------------------------------------------------------------------------------------------ the save status
  var STATUS = {
    loading: ["ph-spinner-gap ed-spin", "Loading"],
    saved: ["ph-cloud-check", "Saved"],
    dirty: ["ph-cloud-arrow-up", "Unsaved changes"],
    saving: ["ph-spinner-gap ed-spin", "Saving…"],
    offline: ["ph-cloud-slash", "Can't reach the server. Trying again…"],
    limited: ["ph-hourglass", "Saving slowly. Trying again…"],
    error: ["ph-warning-circle", "Not saved"],
    conflict: ["ph-warning", "Changed somewhere else"],
  };

  function setStatus(name, detail) {
    var spec = STATUS[name] || STATUS.saved;
    var icon = refs.status.querySelector("i");
    refs.status.dataset.state = name;
    icon.className = "ph-fill " + spec[0];
    refs.statusText.textContent = name === "error" && detail ? "Not saved" : spec[1];
    refs.status.title = detail || "";
    var action = name === "conflict" || name === "error";
    refs.status.classList.toggle("is-action", action);
    if (action) {
      refs.status.setAttribute("role", "button");
      refs.status.setAttribute("tabindex", "0");
      if (name === "error" && detail) refs.status.title = detail + " Click to try again.";
    } else {
      refs.status.removeAttribute("tabindex");
      refs.status.setAttribute("role", "status");
    }
  }

  // ------------------------------------------------------------------------------------------------ what the server compiled
  function resolveOwner(blockId) {
    var workspace = E.blocks.workspace();
    var block = workspace && workspace.getBlockById(blockId);
    while (block) {
      if (latest && latest.lines && latest.lines[block.id]) return block.id;
      block = block.getParent();
    }
    return null;
  }

  function updateDownloads(compile) {
    var ok = compile.ok;
    ["get-py", "get-zip"].forEach(function (id) {
      var link = document.getElementById(id);
      link.setAttribute("aria-disabled", ok ? "false" : "true");
      link.classList.toggle("is-disabled", !ok);
    });
    document.getElementById("get-note").hidden = ok;
  }

  function applyCompile(compile) {
    latest = compile;
    E.code.render(compile);
    E.problems.render(compile);
    E.blocks.showProblems(compile.diagnostics);
    updateDownloads(compile);
    if (E.runner) E.runner.refresh();
  }

  // ------------------------------------------------------------------------------------------------ tabs and views
  var TABS = ["code", "files", "problems"];

  function selectTab(name) {
    if (TABS.indexOf(name) === -1) return;
    TABS.forEach(function (tab) {
      var button = document.getElementById("tab-" + tab);
      var pane = document.getElementById("pane-" + tab);
      var on = tab === name;
      button.setAttribute("aria-selected", on ? "true" : "false");
      button.tabIndex = on ? 0 : -1;
      pane.hidden = !on;
    });
    E.store.set("tab", name);
    if (root.dataset.view !== "blocks" && root.dataset.view !== "console") showView(name);
  }

  /* On a phone the editor shows one thing at a time: blocks, Python, files, problems or the console. */
  function showView(view) {
    root.dataset.view = view;
    document.querySelectorAll("#ed-dock button").forEach(function (button) {
      button.setAttribute("aria-pressed", button.dataset.view === view ? "true" : "false");
    });
    if (view === "blocks") requestAnimationFrame(E.blocks.resize);
  }

  function isPhone() {
    return window.matchMedia("(max-width: 56rem)").matches;
  }

  function goTo(view) {
    if (TABS.indexOf(view) !== -1) {
      selectTab(view);
      if (isPhone()) showView(view);
    } else if (isPhone()) {
      showView(view);
    }
  }

  function wireTabs() {
    var buttons = TABS.map(function (tab) {
      return document.getElementById("tab-" + tab);
    });
    buttons.forEach(function (button, index) {
      button.addEventListener("click", function () {
        selectTab(TABS[index]);
      });
      button.addEventListener("keydown", function (event) {
        var next = null;
        if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
        else if (event.key === "ArrowLeft") next = (index + TABS.length - 1) % TABS.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = TABS.length - 1;
        if (next === null) return;
        event.preventDefault();
        selectTab(TABS[next]);
        buttons[next].focus();
      });
    });
    document.querySelectorAll("#ed-dock button").forEach(function (button) {
      button.addEventListener("click", function () {
        goTo(button.dataset.view);
        if (button.dataset.view === "blocks") showView("blocks");
      });
    });
    E.bus.on("tab", function (name) {
      goTo(name);
    });
    var saved = E.store.get("tab", "code");
    selectTab(TABS.indexOf(saved) === -1 ? "code" : saved);
    showView("blocks");
  }

  // ------------------------------------------------------------------------------------------------ the draggable dividers
  function wireSplitter(handle, options) {
    var shell = root;
    var stored = E.store.get(options.key, null);
    if (typeof stored === "number") shell.style.setProperty(options.property, stored + "px");

    function limit(value) {
      var max = options.max();
      return Math.round(Math.max(options.min, Math.min(max, value)));
    }

    function set(value, remember) {
      var next = limit(value);
      shell.style.setProperty(options.property, next + "px");
      if (remember) E.store.set(options.key, next);
      E.blocks.resize();
      return next;
    }

    handle.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) return;
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("is-dragging");
      document.body.classList.add("is-resizing");
    });
    handle.addEventListener("pointermove", function (event) {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      var rect = shell.getBoundingClientRect();
      set(options.fromPointer(event, rect), false);
    });
    function stop(event) {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      handle.releasePointerCapture(event.pointerId);
      handle.classList.remove("is-dragging");
      document.body.classList.remove("is-resizing");
      var size = options.current();
      E.store.set(options.key, size);
    }
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
    handle.addEventListener("keydown", function (event) {
      var step = event.shiftKey ? 80 : 24;
      var delta = 0;
      if (event.key === options.grow) delta = step;
      else if (event.key === options.shrink) delta = -step;
      else return;
      event.preventDefault();
      set(options.current() + delta, true);
    });
    handle.addEventListener("dblclick", function () {
      shell.style.removeProperty(options.property);
      E.store.set(options.key, null);
      E.blocks.resize();
    });
  }

  function wireSplitters() {
    var side = document.querySelector(".ed-side");
    var details = document.querySelector(".ed-details");
    wireSplitter(document.getElementById("split-side"), {
      key: "side-w",
      property: "--side-w",
      min: 300,
      max: function () { return Math.min(760, window.innerWidth - 420); },
      fromPointer: function (event, rect) { return rect.right - event.clientX; },
      current: function () { return side.getBoundingClientRect().width; },
      grow: "ArrowLeft",
      shrink: "ArrowRight",
    });
    wireSplitter(document.getElementById("split-console"), {
      key: "console-h",
      property: "--console-h",
      min: 120,
      max: function () { return Math.max(160, side.getBoundingClientRect().height - 180); },
      fromPointer: function (event) { return side.getBoundingClientRect().bottom - event.clientY; },
      current: function () { return document.getElementById("view-console").getBoundingClientRect().height; },
      grow: "ArrowUp",
      shrink: "ArrowDown",
    });
    void details;
  }

  // ------------------------------------------------------------------------------------------------ toolbar buttons
  function updateUndo() {
    document.getElementById("tool-undo").disabled = !E.blocks.canUndo();
    document.getElementById("tool-redo").disabled = !E.blocks.canRedo();
  }

  function wireTools() {
    var on = function (id, fn) {
      document.getElementById(id).addEventListener("click", fn);
    };
    on("tool-undo", function () { E.blocks.undo(false); updateUndo(); });
    on("tool-redo", function () { E.blocks.undo(true); updateUndo(); });
    on("tool-tidy", function () { E.blocks.tidy(); });
    on("tool-zoom-in", function () { E.blocks.zoom(1); });
    on("tool-zoom-out", function () { E.blocks.zoom(-1); });
    on("tool-zoom-fit", function () { E.blocks.fit(); });
    E.bus.on("undo-state", updateUndo);

    document.getElementById("ed-tip-close").addEventListener("click", function () {
      refs.tip.hidden = true;
      E.store.set("tip-closed", true);
    });
    if (!E.store.get("tip-closed", false)) refs.tip.hidden = false;

    ["get-py", "get-zip"].forEach(function (id) {
      document.getElementById(id).addEventListener("click", function (event) {
        if (latest && !latest.ok) {
          event.preventDefault();
          sff.toast("Fix the problems listed in the editor first, then download your code.", "warn", 5000);
          return;
        }
        document.getElementById("ed-get").removeAttribute("open");
        /* make sure the file has everything the person just did */
        if (saver && saver.isDirty()) {
          event.preventDefault();
          var link = event.currentTarget;
          saver.flush().then(function (ok) {
            if (ok) link.click();
          });
        }
      });
    });

    document.addEventListener("keydown", function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (!saver) return;
        saver.flush().then(function (ok) {
          if (ok) sff.toast("Saved", "ok", 1400);
        });
      }
    });

    refs.status.addEventListener("click", function () {
      if (refs.status.classList.contains("is-action") && saver) saver.retry();
    });
    refs.status.addEventListener("keydown", function (event) {
      if ((event.key === "Enter" || event.key === " ") && refs.status.classList.contains("is-action") && saver) {
        event.preventDefault();
        saver.retry();
      }
    });
  }

  // ------------------------------------------------------------------------------------------------ saving
  async function reloadFromServer() {
    var data = await sff.api(workspaceUrl);
    E.blocks.load(data.workspace);
    saver.setVersion(data.version);
    E.files.set(data.files);
    applyCompile(data.compile);
    sff.toast("Loaded the newer version of your blocks.", "info", 3200);
  }

  function onConflict() {
    return E.dialogs
      .choose({
        title: "These blocks changed somewhere else",
        message:
          "Another tab or device saved this bot after you opened it. Pick which version to keep. You can only keep one of them.",
        choices: [
          { id: "load", label: "Load the other version", hint: "The changes you made in this tab are dropped.", kind: "primary" },
          { id: "keep", label: "Keep the version in this tab", hint: "The other version is replaced.", kind: "danger" },
        ],
        cancelLabel: "Decide later",
      })
      .then(function (choice) {
        if (choice === "load") {
          return reloadFromServer().then(
            function () { return "load"; },
            function (error) {
              sff.toast(error.message, "error", 6000);
              return null;
            }
          );
        }
        return choice;
      });
  }

  var reopenWarned = false;

  function onSaved(result, newer) {
    E.files.set(result.files);
    applyCompile(result.compile);
    if (result.workspace && !newer) {
      try {
        E.blocks.load(result.workspace);
      } catch (error) {
        /* The blocks were saved, but the editor can't open the version the server kept. What is on screen is still
           the person's own work (the failed load put it back), so it stays. */
        if (window.console) console.error("[editor] could not open the saved blocks", error);
        if (!reopenWarned) {
          reopenWarned = true;
          sff.toast(
            "Your blocks were saved, but the editor can't reopen the saved copy" +
              (error && error.message ? " (" + String(error.message).slice(0, 120) + ")" : "") +
              ". What you see here is your own work. Please tell the site's maker what you were doing.",
            "warn",
            12000
          );
        }
        return;
      }
      if (result.dropped) {
        sff.toast(E.plural(result.dropped, "block") + " couldn't be kept and " + (result.dropped === 1 ? "was" : "were") + " removed.", "warn", 6000);
      }
    }
  }

  // ------------------------------------------------------------------------------------------------ start
  function start(blocksPayload, data) {
    if (!window.Blockly) throw new Error("The block library didn't load. Check your connection and try again.");

    E.blocks.init(blocksPayload);
    E.files.init(botId, data.files, data.limits);
    E.code.init(resolveOwner);
    E.problems.init();
    E.console.init();
    E.runner.init({
      botId: botId,
      mode: root.dataset.runner,
      compile: function () { return latest; },
      flush: function () { return saver.flush(); },
      showProblems: function () { goTo("problems"); },
      showConsole: function () { if (isPhone()) showView("console"); },
    });

    E.blocks.create(document.getElementById("blockly"));
    E.blocks.load(data.workspace);

    saver = E.autosave.create({
      botId: botId,
      version: data.version,
      serialize: function () {
        return E.blocks.isEmpty() ? null : E.blocks.save();
      },
      onSaved: onSaved,
      onConflict: onConflict,
      onStatus: setStatus,
    });

    applyCompile(data.compile);
    setStatus("saved");

    E.bus.on("change", function () {
      saver.touch();
      updateUndo();
    });
    E.bus.on("select", function (id) {
      E.code.highlight(id, resolveOwner);
    });
    E.bus.on("code:pick", function (id) {
      if (isPhone()) showView("blocks");
      E.blocks.reveal(id);
    });
    E.bus.on("problem:pick", function (id) {
      if (isPhone()) showView("blocks");
      E.blocks.reveal(id);
    });
    E.bus.on("files:reveal", function (name) {
      if (isPhone()) showView("blocks");
      if (!E.blocks.revealFile(name)) sff.toast("No block uses " + name + " right now.", "info", 2600);
    });
    E.bus.on("flyout", function (open) {
      refs.tip.classList.toggle("is-covered", open); /* the block list would hide half of the tip */
    });
    E.bus.on("compile", applyCompile);
    E.bus.on("files:dropped", function () {
      goTo("files");
    });

    refs.tools.hidden = false;
    refs.loading.hidden = true;
    E.blocks.resize();
    updateUndo();
    started = true;

    if (data.damaged) {
      sff.toast("Your saved blocks were damaged, so you're starting again with the starter blocks.", "warn", 8000);
    } else if (data.dropped) {
      sff.toast(E.plural(data.dropped, "saved block") + " couldn't be loaded and " + (data.dropped === 1 ? "was" : "were") + " left out.", "warn", 8000);
    }
  }

  async function boot() {
    refs.failed.hidden = true;
    refs.loading.hidden = false;
    setStatus("loading");
    try {
      var results = await Promise.all([sff.api("/api/blocks"), sff.api(workspaceUrl), E.fontsReady()]);
      start(results[0], results[1]);
    } catch (error) {
      if (window.console) console.error("[editor] could not start", error);
      refs.loading.hidden = true;
      refs.failed.hidden = false;
      refs.failedText.textContent = (error && error.message) || "Something went wrong while loading your blocks.";
      setStatus("error", "");
      refs.statusText.textContent = "Not loaded";
    }
  }

  wireTabs();
  wireTools();
  wireSplitters();
  document.getElementById("ed-retry").addEventListener("click", function () {
    if (started) return;
    boot();
  });
  window.addEventListener("resize", function () {
    E.blocks.resize();
  });
  if (window.ResizeObserver) {
    var pending = null;
    new ResizeObserver(function () {
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(E.blocks.resize);
    }).observe(document.getElementById("blockly"));
  }
  boot();
})();
