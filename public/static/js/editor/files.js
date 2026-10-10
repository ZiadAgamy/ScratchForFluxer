/* The Files tab: the pictures, JSON and text a bot's blocks can use. Lists them, uploads (button, drag and drop, or from a
   block's own dropdown), previews, downloads and deletes. The file list also feeds the file dropdowns on the blocks. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var el = E.el;
  var sff = window.sff;

  var state = {
    botId: "",
    files: [],
    limits: { max_files: 40, max_file_bytes: 2097152, max_total_bytes: 8388608, accepted: "", extensions: [] },
    pending: [],
  };

  var KIND_LABEL = { image: "Image", json: "JSON", text: "Text" };
  var KIND_ICON = { image: "ph-image", json: "ph-file-code", text: "ph-file-text" };

  var refs = {};

  function url(name) {
    return "/api/bots/" + encodeURIComponent(state.botId) + "/assets" + (name ? "/" + encodeURIComponent(name) : "");
  }

  function extensionOf(name) {
    var dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(dot).toLowerCase() : "";
  }

  // ------------------------------------------------------------------------------------------------ list
  function names(kinds) {
    return state.files
      .filter(function (file) {
        return !kinds || !kinds.length || kinds.indexOf(file.kind) !== -1;
      })
      .map(function (file) {
        return file.name;
      });
  }

  function find(name) {
    for (var i = 0; i < state.files.length; i++) if (state.files[i].name === name) return state.files[i];
    return null;
  }

  function totalBytes() {
    return state.files.reduce(function (sum, file) {
      return sum + file.size;
    }, 0);
  }

  function setFiles(files) {
    var next = files || [];
    if (JSON.stringify(next) === JSON.stringify(state.files)) return;
    state.files = next;
    render();
    E.bus.emit("files", state.files);
  }

  // ------------------------------------------------------------------------------------------------ rendering
  function thumb(file) {
    var button = el("button", { class: "ed-thumb ed-thumb-" + file.kind, type: "button", "aria-label": "Preview " + file.name, title: "Preview" });
    if (file.kind === "image") {
      var img = el("img", { alt: "", loading: "lazy", decoding: "async", width: 44, height: 44 });
      img.src = url(file.name) + "?v=" + encodeURIComponent(file.created_at || "");
      img.addEventListener("error", function () {
        button.replaceChildren(E.icon("image"));
      });
      button.appendChild(img);
    } else {
      button.appendChild(E.icon(file.kind === "json" ? "file-code" : "file-text"));
    }
    button.addEventListener("click", function () {
      preview(file);
    });
    return button;
  }

  function usedBy(file) {
    if (!file.used_by) return el("span", { class: "ed-muted", text: "Not used by any block yet" });
    return el("button", {
      class: "ed-link",
      type: "button",
      text: "Used by " + E.plural(file.used_by, "block"),
      title: "Show the block",
      on: {
        click: function () {
          E.bus.emit("files:reveal", file.name);
        },
      },
    });
  }

  function row(file) {
    return el("div", { class: "ed-file", data: { name: file.name } }, [
      thumb(file),
      el("div", { class: "ed-file-main" }, [
        el("strong", { class: "ed-file-name", text: file.name, title: file.name }),
        el("span", { class: "ed-file-meta" }, [
          el("span", { text: (KIND_LABEL[file.kind] || file.kind) + " · " + E.formatBytes(file.size) }),
          usedBy(file),
        ]),
      ]),
      el("div", { class: "ed-file-actions" }, [
        el("a", { class: "ed-icon-btn", href: url(file.name), download: file.name, "aria-label": "Download " + file.name, title: "Download" }, [E.icon("download-simple")]),
        el("button", { class: "ed-icon-btn ed-icon-danger", type: "button", "aria-label": "Delete " + file.name, title: "Delete", on: { click: function () { remove(file); } } }, [E.icon("trash")]),
      ]),
    ]);
  }

  function render() {
    if (!refs.list) return;
    var limits = state.limits;
    var count = state.files.length;
    var used = totalBytes();
    refs.count.textContent = String(count);
    refs.usage.textContent =
      E.plural(count, "file") + " of " + limits.max_files + " · " + E.formatBytes(used) + " of " + E.formatBytes(limits.max_total_bytes);
    refs.bar.style.width = Math.min(100, Math.round((used / limits.max_total_bytes) * 100)) + "%";
    refs.accepted.textContent = limits.accepted + ". Up to " + E.formatBytes(limits.max_file_bytes) + " each.";
    refs.add.disabled = false;

    E.clear(refs.list);
    state.pending.forEach(function (name) {
      refs.list.appendChild(
        el("div", { class: "ed-file ed-file-pending" }, [
          el("span", { class: "ed-thumb" }, [E.icon("spinner-gap", "ed-spin")]),
          el("div", { class: "ed-file-main" }, [el("strong", { class: "ed-file-name", text: name }), el("span", { class: "ed-file-meta ed-muted", text: "Uploading…" })]),
        ])
      );
    });
    state.files.forEach(function (file) {
      refs.list.appendChild(row(file));
    });
    refs.list.hidden = !refs.list.firstChild;
    refs.drop.classList.toggle("is-compact", count > 0);
  }

  // ------------------------------------------------------------------------------------------------ preview
  function preview(file) {
    E.dialogs.show({
      title: file.name,
      build: function (body) {
        var meta = el("p", { class: "ed-muted", text: (KIND_LABEL[file.kind] || file.kind) + " · " + E.formatBytes(file.size) });
        if (file.kind === "image") {
          var img = el("img", { class: "ed-preview-img", alt: "Preview of " + file.name });
          img.src = url(file.name) + "?v=" + encodeURIComponent(file.created_at || "");
          body.appendChild(el("div", { class: "ed-preview-frame" }, [img]));
          body.appendChild(meta);
          return;
        }
        var pre = el("pre", { class: "ed-preview-text", text: "Loading…" });
        body.appendChild(pre);
        body.appendChild(meta);
        fetch(url(file.name), { credentials: "same-origin" })
          .then(function (response) {
            if (!response.ok) throw new Error("status " + response.status);
            return response.text();
          })
          .then(function (text) {
            var shown = text;
            if (file.kind === "json" && text.length < 200000) {
              try {
                shown = JSON.stringify(JSON.parse(text), null, 2);
              } catch (_) {
                shown = text;
              }
            }
            if (shown.length > 20000) shown = shown.slice(0, 20000) + "\n… (the rest of the file isn't shown here)";
            pre.textContent = shown;
          })
          .catch(function () {
            pre.textContent = "Couldn't load the file to show it.";
          });
      },
    });
  }

  // ------------------------------------------------------------------------------------------------ upload
  function accepted(file) {
    return state.limits.extensions.indexOf(extensionOf(file.name)) !== -1;
  }

  async function uploadOne(file) {
    if (!accepted(file)) {
      sff.toast(file.name + " isn't a file type that can be used. Use " + state.limits.accepted + ".", "error", 6000);
      return null;
    }
    if (!file.size) {
      sff.toast(file.name + " is empty.", "error");
      return null;
    }
    if (file.size > state.limits.max_file_bytes) {
      sff.toast(file.name + " is bigger than " + E.formatBytes(state.limits.max_file_bytes) + ".", "error", 6000);
      return null;
    }
    var existing = find(file.name);
    if (existing) {
      var replace = await E.dialogs.confirm({
        title: "Replace " + file.name + "?",
        message: "A file with this name is already in this bot. The new file takes its place, and the blocks that use " + file.name + " will use the new one.",
        confirmLabel: "Replace",
      });
      if (!replace) return null;
    }
    state.pending.push(file.name);
    render();
    try {
      var result = await sff.api(url(file.name), { method: "PUT", body: file });
      setFiles(result.files);
      E.bus.emit("compile", result.compile);
      sff.toast((result.replaced ? "Replaced " : "Added ") + file.name, "ok", 2600);
      return result.file.name;
    } catch (error) {
      if (error.name !== "AbortError") sff.toast(file.name + ": " + error.message, "error", 7000);
      return null;
    } finally {
      state.pending.splice(state.pending.indexOf(file.name), 1);
      render();
    }
  }

  /* Upload several files one after another (the server counts them against the limits as they arrive). */
  async function upload(fileList) {
    var added = [];
    var files = Array.prototype.slice.call(fileList || []);
    for (var i = 0; i < files.length; i++) {
      var name = await uploadOne(files[i]);
      if (name) added.push(name);
    }
    return added;
  }

  /* Open the system's file chooser (limited to the kinds a block can use). Resolves with the names that were added. */
  function pick(kinds) {
    return new Promise(function (resolve) {
      var input = refs.input;
      var wanted = state.limits.extensions.filter(function (ext) {
        if (!kinds || !kinds.length) return true;
        return kinds.indexOf(kindOfExtension(ext)) !== -1;
      });
      input.accept = wanted.join(",");
      input.value = "";
      function finish(list) {
        input.removeEventListener("change", onChange);
        input.removeEventListener("cancel", onCancel);
        input.accept = state.limits.extensions.join(",");
        resolve(list);
      }
      function onChange() {
        upload(input.files).then(finish);
      }
      function onCancel() {
        finish([]);
      }
      input.addEventListener("change", onChange);
      input.addEventListener("cancel", onCancel);
      input.click();
    });
  }

  var IMAGE_EXT = [".png", ".jpg", ".jpeg", ".gif", ".webp"];
  function kindOfExtension(ext) {
    if (IMAGE_EXT.indexOf(ext) !== -1) return "image";
    return ext === ".json" ? "json" : "text";
  }

  // ------------------------------------------------------------------------------------------------ delete
  async function remove(file) {
    var message = file.used_by
      ? E.plural(file.used_by, "block") + " use" + (file.used_by === 1 ? "s" : "") + " this file. They will show a problem until you choose another file."
      : "This can't be undone.";
    var ok = await E.dialogs.confirm({ title: "Delete " + file.name + "?", message: message, confirmLabel: "Delete", danger: true });
    if (!ok) return;
    try {
      var result = await sff.api(url(file.name), { method: "DELETE" });
      setFiles(result.files);
      E.bus.emit("compile", result.compile);
      sff.toast("Deleted " + file.name, "ok", 2400);
    } catch (error) {
      sff.toast(error.message, "error", 6000);
    }
  }

  // ------------------------------------------------------------------------------------------------ drag and drop
  function hasFiles(event) {
    var types = event.dataTransfer && event.dataTransfer.types;
    return !!types && Array.prototype.indexOf.call(types, "Files") !== -1;
  }

  function wireDrop() {
    var depth = 0;
    var veil = document.getElementById("ed-dropveil");
    function show(on) {
      veil.hidden = !on;
    }
    document.addEventListener("dragenter", function (event) {
      if (!hasFiles(event)) return;
      depth += 1;
      show(true);
    });
    document.addEventListener("dragleave", function (event) {
      if (!hasFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (!depth) show(false);
    });
    document.addEventListener("dragover", function (event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    document.addEventListener("drop", function (event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth = 0;
      show(false);
      E.bus.emit("files:dropped");
      upload(event.dataTransfer.files);
    });
  }

  // ------------------------------------------------------------------------------------------------ start
  function init(botId, files, limits) {
    state.botId = botId;
    state.limits = limits;
    refs = {
      list: document.getElementById("files-list"),
      count: document.getElementById("files-count"),
      usage: document.getElementById("files-usage"),
      bar: document.getElementById("files-bar"),
      add: document.getElementById("files-add"),
      drop: document.getElementById("files-drop"),
      accepted: document.getElementById("files-accepted"),
      input: document.getElementById("file-input"),
    };
    refs.add.addEventListener("click", function () {
      pick(null);
    });
    refs.drop.addEventListener("click", function () {
      pick(null);
    });
    wireDrop();
    state.files = files || [];
    render();
    E.bus.emit("files", state.files);
  }

  E.files = {
    init: init,
    set: setFiles,
    names: names,
    find: find,
    list: function () {
      return state.files.slice();
    },
    pick: pick,
    upload: upload,
    limits: function () {
      return state.limits;
    },
  };
})();
