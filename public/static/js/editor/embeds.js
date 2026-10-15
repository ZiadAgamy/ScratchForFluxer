/* The pop-up for embed blocks (the card with a coloured edge): title, text, colour, link, pictures, author, footer and
   time, with a live preview of the card. The block keeps the title and the text in its sockets (so another block can
   fill them) and everything else as a small piece of JSON in its OPTIONS field. The server checks all of it again,
   so nothing here is trusted. Text only goes into the page through textContent. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var B = window.Blockly;
  var el = E.el;

  var config = null;
  var LINK = "__link__";
  var UPLOAD = "__upload__";

  function limits() {
    return (config && config.limits) || {};
  }

  /* ------------------------------------------------------------------------------------------ the options */

  var URL_PATTERN = /^https?:\/\/[^\s"'<>\\`]+$/i;

  function isUrl(value) {
    return URL_PATTERN.test(value) && value.length <= (limits().url || 500);
  }

  function isHex(value) {
    return /^#[0-9a-f]{6}$/i.test(value);
  }

  function blank() {
    return { color: config.default_color, url: "", image: "", thumbnail: "", author: "", author_icon: "", footer: "", footer_icon: "", time: false };
  }

  function parse(text) {
    var out = blank();
    var data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (problem) {
      data = null;
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) return out;
    Object.keys(out).forEach(function (key) {
      if (key === "time") out.time = data.time === true;
      else if (typeof data[key] === "string") out[key] = data[key];
    });
    return out;
  }

  /* Only what differs from the default, keys in order (the server writes them the same way). */
  function serialize(options) {
    var out = {};
    Object.keys(options)
      .sort()
      .forEach(function (key) {
        var value = options[key];
        if (key === "color") {
          if (value && value !== config.default_color) out.color = value;
        } else if (key === "time") {
          if (value === true) out.time = true;
        } else if (value) out[key] = value;
      });
    return Object.keys(out).length ? JSON.stringify(out) : "{}";
  }

  function countSet(text) {
    var options = parse(text);
    var count = 0;
    Object.keys(options).forEach(function (key) {
      if (key === "color") count += options.color !== config.default_color ? 1 : 0;
      else if (key === "time") count += options.time ? 1 : 0;
      else if (key.indexOf("_icon") === -1 && options[key]) count += 1;
    });
    return count;
  }

  function colorValue(name) {
    var found = (config.colors || []).filter(function (entry) {
      return entry.name === name;
    })[0];
    return found ? found.rgb : isHex(name) ? name.toLowerCase() : "#176dff";
  }

  /* ------------------------------------------------------------------------------------------ the sockets */

  /* The text in a title or text socket: editable when it is a plain text box, read-only when another block fills it. */
  function socket(block, name) {
    var input = block.getInput(name);
    var target = input && input.connection ? input.connection.targetBlock() : null;
    if (!target) return { block: null, text: "", editable: true };
    if (target.type === "sff_text") return { block: target, text: target.getFieldValue("TEXT") || "", editable: true };
    return { block: target, text: "", editable: false };
  }

  function writeSocket(block, name, found, text) {
    if (!found.editable || text === found.text) return;
    if (found.block) {
      found.block.setFieldValue(text, "TEXT");
      return;
    }
    var input = block.getInput(name);
    var created = block.workspace.newBlock("sff_text");
    created.setFieldValue(text, "TEXT");
    if (created.initSvg) created.initSvg();
    if (created.render) created.render();
    input.connection.connect(created.outputConnection);
  }

  /* ------------------------------------------------------------------------------------------ the preview card */

  function previewCard(state, mode) {
    var o = state.options;
    var card = el("div", { class: "embed-card" });
    card.style.borderLeftColor = colorValue(o.color);
    var body = el("div", { class: "embed-card-body" });
    var main = el("div", { class: "embed-card-main" });
    if (o.author) {
      var author = el("div", { class: "embed-author" });
      if (o.author_icon) author.appendChild(el("span", { class: "embed-icon", "aria-hidden": "true" }, [E.icon("image")]));
      author.appendChild(el("span", { text: o.author }));
      main.appendChild(author);
    }
    var title = state.title.editable ? state.titleText : "‹a block fills this›";
    if (title) main.appendChild(el("div", { class: "embed-title" + (o.url ? " embed-title-link" : ""), text: title }));
    var text = state.text.editable ? state.textText : "‹a block fills this›";
    if (text) main.appendChild(el("div", { class: "embed-text", text: text }));
    if (o.image) main.appendChild(picture(o.image, "embed-image", mode));
    var footer = [];
    if (o.footer) footer.push(o.footer);
    if (o.time) footer.push("today at " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
    if (footer.length) {
      var row = el("div", { class: "embed-footer" });
      if (o.footer_icon) row.appendChild(el("span", { class: "embed-icon", "aria-hidden": "true" }, [E.icon("image")]));
      row.appendChild(el("span", { text: footer.join(" • ") }));
      main.appendChild(row);
    }
    body.appendChild(main);
    if (o.thumbnail) body.appendChild(picture(o.thumbnail, "embed-thumb", mode));
    card.appendChild(body);
    return card;
  }

  /* An uploaded picture is shown. A web address is not loaded here (the editor only loads its own files): a labelled box stands in. */
  function picture(value, className, mode) {
    var name = value.indexOf(config.file_prefix) === 0 ? value.slice(config.file_prefix.length) : null;
    var box = el("div", { class: className });
    if (name && E.files && E.files.find(name)) {
      var img = el("img", { alt: "", src: E.files.url(name) });
      img.addEventListener("error", function () {
        box.classList.add("embed-pic-missing");
      });
      box.appendChild(img);
    } else {
      box.classList.add("embed-pic-placeholder");
      box.appendChild(E.icon(name ? "warning" : "image"));
      box.appendChild(el("span", { text: name ? name : "picture" }));
    }
    return box;
  }

  /* ------------------------------------------------------------------------------------------ the pop-up */

  var counter = 0;

  function edit(block, field) {
    var starting = parse(field.getValue());
    var state = {
      options: starting,
      title: socket(block, "TITLE"),
      text: socket(block, "TEXT"),
      titleText: "",
      textText: "",
    };
    state.titleText = state.title.text;
    state.textText = state.text.text;
    var allowFiles = field.allowFiles;

    return E.dialogs.open(function (dialog, close) {
      counter += 1;
      var uid = "emb" + counter + "-";
      dialog.classList.add("dialog-embed");
      dialog.setAttribute("aria-labelledby", uid + "heading");

      var preview = el("div", { class: "embed-preview-card" });
      var errors = {};
      var done = el("button", { class: "btn btn-primary", type: "submit", text: "Done" });

      function redraw() {
        E.clear(preview);
        preview.appendChild(previewCard(state, "popup"));
        done.disabled = Object.keys(errors).some(function (key) {
          return errors[key];
        });
      }

      function field_(key, label, control, hint) {
        var error = el("span", { class: "field-error", role: "alert", hidden: true });
        errors[key] = false;
        return {
          error: error,
          show: function (problem) {
            error.textContent = problem || "";
            error.hidden = !problem;
            errors[key] = !!problem;
            control.setAttribute("aria-invalid", problem ? "true" : "false");
          },
          node: el("div", { class: "field" }, [
            el("label", { for: control.id, text: label }),
            control,
            hint ? el("span", { class: "hint", text: hint }) : null,
            error,
          ]),
        };
      }

      /* One line of text bound to an option. `kind` is "text" or "url" (a web address that starts with http:// or https://). */
      function line(key, label, kind, placeholder, hint, max) {
        var input = el("input", {
          class: "input",
          id: uid + key,
          type: kind === "url" ? "url" : "text",
          maxlength: max || limits()[key] || 200,
          value: state.options[key],
          placeholder: placeholder || "",
          autocomplete: "off",
          spellcheck: "false",
        });
        var wrapper = field_(key, label, input, hint);
        input.addEventListener("input", function () {
          var value = input.value.trim();
          state.options[key] = value;
          wrapper.show(kind === "url" && value && !isUrl(value) ? "Start the address with https:// and leave out spaces." : "");
          redraw();
        });
        wrapper.input = input;
        return wrapper;
      }

      // ---- title and text (the sockets)
      function socketControl(which, label, multiline) {
        var found = state[which];
        var key = which + "Text";
        var id = uid + which;
        if (!found.editable) {
          return el("div", { class: "field" }, [
            el("span", { class: "label-like", text: label }),
            el("p", { class: "embed-note" }, [
              E.icon("puzzle-piece"),
              " A block is plugged into this socket. Take it out of the block to type here.",
            ]),
          ]);
        }
        var max = which === "title" ? limits().title || 256 : limits().text || 2000;
        var control = multiline
          ? el("textarea", { class: "input", id: id, rows: "4", maxlength: max, spellcheck: "false" })
          : el("input", { class: "input", id: id, type: "text", maxlength: max, autocomplete: "off", spellcheck: "false" });
        if (which === "title") control.setAttribute("data-autofocus", "");
        control.value = state[key];
        control.addEventListener("input", function () {
          state[key] = control.value;
          redraw();
        });
        return el("div", { class: "field" }, [el("label", { for: id, text: label }), control]);
      }

      // ---- colour
      var swatches = el("div", { class: "embed-swatches", role: "radiogroup", "aria-label": "Colour" });
      var hexInput = el("input", { class: "input embed-hex", id: uid + "hex", type: "text", maxlength: "7", value: "", placeholder: "#176dff", autocomplete: "off", spellcheck: "false", "aria-label": "Custom colour, like #176dff" });
      var picker = el("input", { class: "embed-picker", id: uid + "picker", type: "color", "aria-label": "Pick any colour", value: colorValue(state.options.color) });
      var colorError = el("span", { class: "field-error", role: "alert", hidden: true });
      errors.color = false;

      function paintSwatches() {
        Array.prototype.forEach.call(swatches.children, function (button) {
          var on = button.dataset.name === state.options.color;
          button.setAttribute("aria-checked", on ? "true" : "false");
          button.classList.toggle("is-on", on);
        });
        var custom = isHex(state.options.color);
        hexInput.value = custom ? state.options.color : "";
        picker.value = colorValue(state.options.color);
      }

      function chooseColor(name) {
        state.options.color = name;
        colorError.hidden = true;
        errors.color = false;
        paintSwatches();
        redraw();
      }

      (config.colors || []).forEach(function (entry) {
        var button = el("button", { class: "embed-swatch", type: "button", role: "radio", "aria-checked": "false", "aria-label": entry.label, title: entry.label, "data-name": entry.name });
        button.style.setProperty("--swatch", entry.rgb);
        button.addEventListener("click", function () {
          chooseColor(entry.name);
        });
        swatches.appendChild(button);
      });
      picker.addEventListener("input", function () {
        chooseColor(picker.value.toLowerCase());
      });
      hexInput.addEventListener("input", function () {
        var value = hexInput.value.trim();
        if (!value) {
          colorError.hidden = true;
          errors.color = false;
          redraw();
          return;
        }
        if (value.charAt(0) !== "#") value = "#" + value;
        if (isHex(value)) {
          state.options.color = value.toLowerCase();
          colorError.hidden = true;
          errors.color = false;
          Array.prototype.forEach.call(swatches.children, function (button) {
            button.setAttribute("aria-checked", "false");
            button.classList.remove("is-on");
          });
          picker.value = value;
          redraw();
        } else {
          colorError.textContent = "A colour looks like #176dff: a # and six letters or digits (0-9, a-f).";
          colorError.hidden = false;
          errors.color = true;
          redraw();
        }
      });
      var colorField = el("div", { class: "field" }, [
        el("span", { class: "label-like", text: "Colour of the edge" }),
        swatches,
        el("div", { class: "embed-custom" }, [picker, el("label", { class: "embed-custom-label", for: uid + "hex", text: "or any colour" }), hexInput]),
        colorError,
      ]);

      // ---- a picture: none, a web address, or one of your uploaded pictures
      function pictureField(key, label, hint) {
        var current = state.options[key];
        var select = el("select", { class: "input", id: uid + key + "-source" });
        var link = line(key, "Web address of the " + label.toLowerCase(), "url", "https://example.com/picture.png", null, limits()[key]);
        link.node.hidden = true;
        var wrapper = el("div", { class: "field embed-picture" });
        var selectError = null;

        function names() {
          return allowFiles && E.files ? E.files.names(["image"]) : [];
        }

        function options() {
          E.clear(select);
          select.appendChild(el("option", { value: "", text: "None" }));
          select.appendChild(el("option", { value: LINK, text: "A web address…" }));
          var have = names();
          var chosen = state.options[key];
          var chosenName = chosen.indexOf(config.file_prefix) === 0 ? chosen.slice(config.file_prefix.length) : null;
          have.forEach(function (name) {
            select.appendChild(el("option", { value: config.file_prefix + name, text: "Uploaded: " + name }));
          });
          if (chosenName && have.indexOf(chosenName) === -1) {
            select.appendChild(el("option", { value: chosen, text: chosenName + " (no longer uploaded)" }));
          }
          if (allowFiles) select.appendChild(el("option", { value: UPLOAD, text: "Upload a picture…" }));
          var value = !chosen ? (link.node.hidden ? "" : LINK) : chosenName ? chosen : LINK;
          select.value = value;
        }

        function sync() {
          var chosen = state.options[key];
          var isFile = chosen.indexOf(config.file_prefix) === 0;
          link.node.hidden = isFile || select.value === "" ;
          if (!isFile && link.input.value !== chosen) link.input.value = chosen;
        }

        select.addEventListener("change", function () {
          var value = select.value;
          if (value === UPLOAD) {
            E.files.pick(["image"]).then(function (added) {
              if (added && added.length) state.options[key] = config.file_prefix + added[added.length - 1];
              options();
              sync();
              redraw();
            });
            options();
            return;
          }
          if (value === "") {
            state.options[key] = "";
            link.show("");
            link.node.hidden = true;
          } else if (value === LINK) {
            state.options[key] = link.input.value.trim();
            link.node.hidden = false;
            link.input.focus();
            link.show(state.options[key] && !isUrl(state.options[key]) ? "Start the address with https:// and leave out spaces." : "");
          } else {
            state.options[key] = value;
            link.show("");
            link.node.hidden = true;
          }
          redraw();
        });

        link.node.hidden = !(current && current.indexOf(config.file_prefix) !== 0);
        E.bus.on("files", function () {
          if (dialog.isConnected) options();
        });
        options();
        wrapper.appendChild(el("label", { for: select.id, text: label }));
        wrapper.appendChild(select);
        wrapper.appendChild(link.node);
        if (hint) wrapper.appendChild(el("span", { class: "hint", text: hint }));
        return wrapper;
      }

      // ---- the pieces
      var title = socketControl("title", "Title", false);
      var text = socketControl("text", "Text", true);
      var titleLink = line("url", "Link on the title", "url", "https://example.com", "People who click the title go here.");
      var author = line("author", "Author name", "text", "", "Shown small, above the title.");
      var authorIcon = line("author_icon", "Author picture (web address)", "url", "https://example.com/me.png");
      var footer = line("footer", "Footer text", "text", "", "Shown small, at the bottom.");
      var footerIcon = line("footer_icon", "Footer picture (web address)", "url", "https://example.com/icon.png");
      var time = el("input", { type: "checkbox", id: uid + "time" });
      time.checked = state.options.time;
      time.addEventListener("change", function () {
        state.options.time = time.checked;
        redraw();
      });

      var details = function (heading, children, open) {
        return el("details", { class: "embed-more", open: open ? true : null }, [el("summary", { text: heading })].concat(children));
      };

      var hasMore = function (keys) {
        return keys.some(function (key) {
          return !!starting[key];
        });
      };

      var form = el(
        "form",
        {
          class: "embed-form",
          novalidate: true,
          on: {
            submit: function (event) {
              event.preventDefault();
              if (done.disabled) return;
              close({
                options: serialize(state.options),
                title: state.titleText,
                text: state.textText,
              });
            },
          },
        },
        [
          title,
          text,
          colorField,
          details(
            "Link, pictures",
            [
              titleLink.node,
              pictureField("image", "Big picture", "Shown under the text."),
              pictureField("thumbnail", "Small picture (thumbnail)", "Shown in the top right corner."),
            ],
            hasMore(["url", "image", "thumbnail"])
          ),
          details("Author and footer", [author.node, authorIcon.node, footer.node, footerIcon.node, el("label", { class: "embed-check", for: uid + "time" }, [time, " Show the time the message was sent"])], hasMore(["author", "footer", "time"])),
          el("div", { class: "dialog-actions" }, [
            el("button", { class: "btn btn-ghost", type: "button", text: "Reset", title: "Back to a plain card", on: { click: function () {
              state.options = blank();
              Array.prototype.forEach.call(form.querySelectorAll("input.input"), function (input) {
                if (input.id !== uid + "title" && input.id !== uid + "text") input.value = "";
              });
              time.checked = false;
              Object.keys(errors).forEach(function (key) { errors[key] = false; });
              Array.prototype.forEach.call(form.querySelectorAll(".field-error"), function (node) { node.hidden = true; });
              paintSwatches();
              Array.prototype.forEach.call(form.querySelectorAll(".embed-picture select"), function (select) { select.value = ""; });
              Array.prototype.forEach.call(form.querySelectorAll(".embed-picture .field"), function (node) { node.hidden = true; });
              redraw();
            } } }),
            el("span", { class: "embed-spacer" }),
            el("button", { class: "btn btn-ghost", type: "button", text: "Cancel", on: { click: function () { close(null); } } }),
            done,
          ]),
        ]
      );

      // keep the typed title and text in step (the controls only change their own state)
      dialog.appendChild(
        el("div", { class: "dialog-body" }, [
          el("div", { class: "dialog-head" }, [
            el("h2", { id: uid + "heading", text: "Card options" }),
            el("button", { class: "ed-icon-btn", type: "button", "aria-label": "Close", on: { click: function () { close(null); } } }, [E.icon("x")]),
          ]),
          el("div", { class: "embed-layout" }, [
            form,
            el("div", { class: "embed-side" }, [el("span", { class: "label-like", text: "How it will look" }), preview, el("p", { class: "hint", text: "Pictures from a web address are not shown here, but they are sent. The card looks a little different in Fluxer." })]),
          ]),
        ])
      );
      paintSwatches();
      redraw();
    }, null).then(function (result) {
      if (!result) return false;
      var workspace = block.workspace;
      var group = B.Events.getGroup();
      B.Events.setGroup(true);
      try {
        writeSocket(block, "TITLE", state.title, result.title);
        writeSocket(block, "TEXT", state.text, result.text);
        field.setValue(result.options);
      } finally {
        B.Events.setGroup(group || false);
      }
      if (workspace && workspace.render) workspace.render();
      return true;
    });
  }

  /* ------------------------------------------------------------------------------------------ the block's button */

  class FieldEmbed extends B.FieldDropdown {
    constructor(files, value) {
      super(function () {
        return [["Card options", "{}"]];
      });
      this.allowFiles = files !== false;
      this.setValue(typeof value === "string" ? value : "{}");
    }

    static fromJson(options) {
      return new FieldEmbed(options.files, options.value);
    }

    label() {
      if (!config) return "Card options";
      var count = countSet(this.value_ || "{}");
      return count ? "Card options · " + count + " set" : "Card options";
    }

    getOptions() {
      this.generatedOptions = [[this.label(), this.value_ || "{}"]];
      return this.generatedOptions;
    }

    doClassValidation_(value) {
      return typeof value === "string" ? value : null;
    }

    /* The button opens the pop-up, not a menu. */
    showEditor_() {
      var field = this;
      var block = this.getSourceBlock();
      if (!block || !config) return;
      edit(block, field);
    }
  }

  E.embeds = {
    configure: function (value) {
      config = value;
    },
    register: function () {
      B.fieldRegistry.register("field_sff_embed", FieldEmbed);
    },
    /* exposed for the tests */
    parse: parse,
    serialize: serialize,
  };
})();
