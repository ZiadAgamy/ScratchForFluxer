/* Dialogs for the editor, built with <dialog> so focus, Escape and the backdrop work without extra code.
   Every function returns a promise, and each one is safe to call while another dialog is open (they queue up). */
(function () {
  "use strict";

  var E = window.sffEditor;
  var el = E.el;
  var queue = Promise.resolve();

  /* Open `build(close)`'s dialog and resolve with whatever it passes to close(). Escape or the backdrop gives `fallback`. */
  function open(build, fallback) {
    var run = function () {
      return new Promise(function (resolve) {
        var dialog = el("dialog", { class: "dialog" });
        var done = false;
        function close(value) {
          if (done) return;
          done = true;
          if (dialog.open) dialog.close();
          dialog.remove();
          resolve(value);
        }
        build(dialog, close);
        dialog.addEventListener("cancel", function (event) {
          event.preventDefault();
          close(fallback);
        });
        dialog.addEventListener("mousedown", function (event) {
          if (event.target === dialog) close(fallback);
        });
        document.body.appendChild(dialog);
        dialog.showModal();
        var focus = dialog.querySelector("[data-autofocus]") || dialog.querySelector("input, textarea, button");
        if (focus) {
          focus.focus();
          if (focus.select && focus.tagName === "INPUT") focus.select();
        }
      });
    };
    var result = queue.then(run, run);
    queue = result.then(function () {}, function () {});
    return result;
  }

  function heading(title, id) {
    return el("h2", { id: id, text: title });
  }

  /* Ask for one line of text. `validate(value)` returns a sentence to show, or "" when the text is fine. */
  function prompt(options) {
    var opts = options || {};
    return open(function (dialog, close) {
      var input = el("input", {
        class: "input",
        id: "dlg-input",
        value: opts.value || "",
        maxlength: opts.maxLength || 80,
        autocomplete: "off",
        spellcheck: "false",
        placeholder: opts.placeholder || "",
        "data-autofocus": true,
      });
      var error = el("p", { class: "field-error", role: "alert", hidden: true });
      var form = el(
        "form",
        {
          class: "dialog-body",
          novalidate: true,
          on: {
            submit: function (event) {
              event.preventDefault();
              var value = input.value.trim();
              var problem = opts.validate ? opts.validate(value) : value ? "" : "Type something first.";
              if (problem) {
                error.textContent = problem;
                error.hidden = false;
                input.setAttribute("aria-invalid", "true");
                input.focus();
                return;
              }
              close(value);
            },
          },
        },
        [
          heading(opts.title || "Name it", "dlg-title"),
          opts.message ? el("p", { class: "dialog-text", text: opts.message }) : null,
          el("div", { class: "field" }, [
            opts.label ? el("label", { for: "dlg-input", text: opts.label }) : null,
            input,
            opts.hint ? el("span", { class: "hint", text: opts.hint }) : null,
          ]),
          error,
          el("div", { class: "dialog-actions" }, [
            el("button", { class: "btn btn-ghost", type: "button", text: "Cancel", on: { click: function () { close(null); } } }),
            el("button", { class: "btn btn-primary", type: "submit", text: opts.confirmLabel || "OK" }),
          ]),
        ]
      );
      input.addEventListener("input", function () {
        error.hidden = true;
        input.removeAttribute("aria-invalid");
      });
      dialog.setAttribute("aria-labelledby", "dlg-title");
      dialog.appendChild(form);
    }, null);
  }

  /* A question with two answers. Resolves true or false. */
  function confirm(options) {
    var opts = options || {};
    return open(function (dialog, close) {
      dialog.setAttribute("aria-labelledby", "dlg-title");
      dialog.appendChild(
        el("div", { class: "dialog-body" }, [
          heading(opts.title || "Are you sure?", "dlg-title"),
          opts.message ? el("p", { class: "dialog-text", text: opts.message }) : null,
          el("div", { class: "dialog-actions" }, [
            el("button", { class: "btn btn-ghost", type: "button", text: opts.cancelLabel || "Cancel", on: { click: function () { close(false); } } }),
            el("button", {
              class: "btn " + (opts.danger ? "btn-danger" : "btn-primary"),
              type: "button",
              text: opts.confirmLabel || "OK",
              "data-autofocus": true,
              on: { click: function () { close(true); } },
            }),
          ]),
        ])
      );
    }, false);
  }

  /* A message with one button. */
  function alert(options) {
    var opts = options || {};
    return open(function (dialog, close) {
      dialog.setAttribute("aria-labelledby", "dlg-title");
      dialog.appendChild(
        el("div", { class: "dialog-body" }, [
          heading(opts.title || "Heads up", "dlg-title"),
          opts.message ? el("p", { class: "dialog-text", text: opts.message }) : null,
          el("div", { class: "dialog-actions" }, [
            el("button", { class: "btn btn-primary", type: "button", text: opts.confirmLabel || "OK", "data-autofocus": true, on: { click: function () { close(); } } }),
          ]),
        ])
      );
    }, undefined);
  }

  /* Several answers. choices: [{ id, label, kind: "primary" | "danger" | "ghost", hint }]. Resolves with the chosen id, or null. */
  function choose(options) {
    var opts = options || {};
    return open(function (dialog, close) {
      dialog.setAttribute("aria-labelledby", "dlg-title");
      var buttons = (opts.choices || []).map(function (choice, index) {
        return el(
          "button",
          {
            class: "choice choice-" + (choice.kind || "ghost"),
            type: "button",
            "data-autofocus": index === 0 ? true : null,
            on: { click: function () { close(choice.id); } },
          },
          [el("strong", { text: choice.label }), choice.hint ? el("span", { text: choice.hint }) : null]
        );
      });
      dialog.appendChild(
        el("div", { class: "dialog-body" }, [
          heading(opts.title || "Choose", "dlg-title"),
          opts.message ? el("p", { class: "dialog-text", text: opts.message }) : null,
          el("div", { class: "choices" }, buttons),
          opts.cancelLabel
            ? el("div", { class: "dialog-actions" }, [
                el("button", { class: "btn btn-ghost", type: "button", text: opts.cancelLabel, on: { click: function () { close(null); } } }),
              ])
            : null,
        ])
      );
    }, null);
  }

  /* Show any content (a picture, a preview of a file). `build(body)` fills the body. */
  function show(options) {
    var opts = options || {};
    return open(function (dialog, close) {
      dialog.classList.add("dialog-wide");
      dialog.setAttribute("aria-labelledby", "dlg-title");
      var body = el("div", { class: "dialog-view" });
      if (opts.build) opts.build(body, close);
      dialog.appendChild(
        el("div", { class: "dialog-body" }, [
          el("div", { class: "dialog-head" }, [
            heading(opts.title || "", "dlg-title"),
            el("button", { class: "ed-icon-btn", type: "button", "aria-label": "Close", on: { click: function () { close(); } } }, [E.icon("x")]),
          ]),
          body,
        ])
      );
    }, undefined);
  }

  E.dialogs = { prompt: prompt, confirm: confirm, alert: alert, choose: choose, show: show };
})();
